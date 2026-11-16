"""M04-E: durable PUBLIC Indodax trade-stream checkpoint, gap detection.

Official Marketdata-websocket.md: market:trade-activity-<pair>, subscription
epoch/offset, recover=true with previous offset, trade sequence in row[2].
No private credentials, BUY/SELL, AI issuer, or assumptions about exchange-wide
complete trade-event coverage. WebSocket client is optional, imported at call.
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
import json
import re
import sqlite3
import time
from typing import Any

WS_URL = "wss://ws3.indodax.com/ws/"
# Public static market-data token, published in the official Indodax docs.
# NOT an account API key, NOT a trading credential.
PUBLIC_TOKEN = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
                "eyJleHAiOjE5NDY2MTg0MTV9."
                "UR1lBM6Eqh0yWz-PVirw1uPCxe60FdchR8eNVdsskeo")
_PAIR = re.compile(r"^[a-z0-9]+_idr$")


class StreamEvidenceError(ValueError):
    pass


def _safe_ws_receive_failure(exc: Exception, websocket_module: Any) -> str:
    """Bounded, non-secret failure class for recv() after subscription.

    Historical PUBLIC_WS_STREAM_RECEIVE_FAILED collapses every unexpected
    exception type. This diagnostic never logs exception strings, remote
    frames, request headers, URL, token, or the socket object; a 'closed'
    exception does not establish whether the peer or network caused it.
    Unknown errors preserve the old fail-stop phase code.
    """
    for library_class, suffix in (
        ("WebSocketConnectionClosedException", "CLOSED_OR_LOST"),
        ("WebSocketProtocolException", "PROTOCOL_ERROR"),
        ("WebSocketPayloadException", "PAYLOAD_ERROR"),
    ):
        cls = getattr(websocket_module, library_class, None)
        if isinstance(cls, type) and isinstance(exc, cls):
            return "PUBLIC_WS_STREAM_RECEIVE_" + suffix
    if isinstance(exc, ConnectionResetError):
        return "PUBLIC_WS_STREAM_RECEIVE_CONNECTION_RESET"
    if isinstance(exc, ConnectionAbortedError):
        return "PUBLIC_WS_STREAM_RECEIVE_CONNECTION_ABORTED"
    if isinstance(exc, BrokenPipeError):
        return "PUBLIC_WS_STREAM_RECEIVE_BROKEN_PIPE"
    if isinstance(exc, EOFError):
        return "PUBLIC_WS_STREAM_RECEIVE_EOF"
    # SSL errors are OSErrors too, so this order is important.
    import ssl
    if isinstance(exc, ssl.SSLError):
        return "PUBLIC_WS_STREAM_RECEIVE_TLS_ERROR"
    if isinstance(exc, OSError):
        return "PUBLIC_WS_STREAM_RECEIVE_OS_ERROR"
    return "PUBLIC_WS_STREAM_RECEIVE_FAILED"



def _safe_ws_transport_failure(exc: Exception, websocket_module: Any,
                               phase: str) -> str:
    """Explicit PUBLIC WS connect/auth transport outage classification.

    Only enumerated transport problems are eligible for bounded retry by
    the original self-heal gate. Never use remote exception text, headers,
    response body or credentials; HTTP 401/403/429 and unknown are denied.
    """
    if phase not in ("CONNECT", "AUTH_RECEIVE"):
        return "PUBLIC_WS_TRANSPORT_PHASE_UNRECOGNIZED"
    prefix = "PUBLIC_WS_" + phase + "_"
    # Distinguish exchange maintenance (gateway/server unavailable) from
    # auth denial, rate limiting and unexpected handshake responses.
    cls = getattr(websocket_module, "WebSocketBadStatusException", None)
    if phase == "CONNECT" and isinstance(cls, type) and isinstance(exc, cls):
        if type(getattr(exc, "status_code", None)) is int and exc.status_code in (
                502, 503, 504):
            return prefix + "HTTP_UNAVAILABLE"
        return prefix + "FAILED"
    cls = getattr(websocket_module, "WebSocketConnectionClosedException", None)
    if isinstance(cls, type) and isinstance(exc, cls):
        return prefix + "CLOSED_OR_LOST"
    # TLS and protocol errors never become retryable transport evidence.
    import ssl
    if isinstance(exc, ssl.SSLError):
        return prefix + "TLS_ERROR"
    cls = getattr(websocket_module, "WebSocketTimeoutException", None)
    if ((isinstance(cls, type) and isinstance(exc, cls))
            or isinstance(exc, TimeoutError)):
        return prefix + "TIMEOUT"
    for klass, code in (
        (ConnectionResetError, "CONNECTION_RESET"),
        (ConnectionAbortedError, "CONNECTION_ABORTED"),
        (ConnectionRefusedError, "CONNECTION_REFUSED"),
        (BrokenPipeError, "BROKEN_PIPE"),
    ):
        if isinstance(exc, klass):
            return prefix + code
    return prefix + "FAILED"


def channel_for_pair(pair: str) -> str:
    if not isinstance(pair, str) or not _PAIR.fullmatch(pair):
        raise StreamEvidenceError("INVALID_PUBLIC_STREAM_PAIR")
    return "market:trade-activity-" + pair.replace("_", "")


def _nonneg_int(value: Any, reason: str) -> int:
    if type(value) is not int or value < 0:
        raise StreamEvidenceError(reason)
    return value


def _positive_decimal(value: Any, reason: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise StreamEvidenceError(reason)
    try:
        parsed = Decimal(str(value))
    except InvalidOperation:
        raise StreamEvidenceError(reason) from None
    if not parsed.is_finite() or parsed <= 0:
        raise StreamEvidenceError(reason)
    return str(parsed)


def _trade_rows(rows: Any, pair: str, *, m03_index=False) -> list[list[Any]]:
    if not isinstance(rows, list) or not rows or len(rows) > 500:
        raise StreamEvidenceError("INVALID_PUBLIC_WS_TRADE_ROWS")
    result = []
    for row in rows:
        if not isinstance(row, list) or len(row) != 7 or row[0] != pair.replace("_", ""):
            raise StreamEvidenceError("PUBLIC_WS_PAIR_OR_ROW_MISMATCH")
        ts = _nonneg_int(row[1], "PUBLIC_WS_TRADE_TIME_INVALID")
        seq = _nonneg_int(row[2], "PUBLIC_WS_TRADE_SEQUENCE_INVALID")
        if not ts or not seq or row[3] not in ("buy", "sell"):
            raise StreamEvidenceError("PUBLIC_WS_TRADE_FIELDS_INVALID")
        if m03_index:
            row = row[:4]+[str(x) if type(x) is float else x for x in row[4:]]
        result.append([
            row[0], ts, seq, row[3],
            _positive_decimal(row[4], "PUBLIC_WS_PRICE_INVALID"),
            _positive_decimal(row[5], "PUBLIC_WS_IDR_VOLUME_INVALID"),
            _positive_decimal(row[6], "PUBLIC_WS_BASE_VOLUME_INVALID"),
        ])
    if len({r[2] for r in result}) != len(result):
        raise StreamEvidenceError("DUPLICATE_SEQUENCE_IN_PUBLICATION")
    return result


class PublicTradeStreamJournal:
    """SQLite-backed trade publications. Only an observed, restartable sample.

    Offset is a per-channel publication cursor, NOT a certified trade ID.
    Even contiguous WS offsets do not establish upstream historical completeness.
    """

    def __init__(self, database: str | Path, *, index_m03_samples: bool = False):
        path = Path(database)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._index_m03_samples = index_m03_samples
        with self._connect() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS ws_stream_state(
                pair TEXT PRIMARY KEY, epoch TEXT NOT NULL,
                last_offset INTEGER NOT NULL, status TEXT NOT NULL
            )""")
            con.execute("""CREATE TABLE IF NOT EXISTS ws_trade_publications(
                pair TEXT NOT NULL, epoch TEXT NOT NULL, offset INTEGER NOT NULL,
                payload_sha256 TEXT NOT NULL, rows_json TEXT NOT NULL,
                PRIMARY KEY(pair, epoch, offset)
            )""")
            # Append-only provenance of *unrecoverable* WS discontinuities.
            # This is not an authentication receipt and never fills history.
            con.execute("""CREATE TABLE IF NOT EXISTS ws_stream_discontinuities(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pair TEXT NOT NULL,
                prior_epoch TEXT NOT NULL,
                prior_offset INTEGER NOT NULL,
                new_epoch TEXT NOT NULL,
                new_offset INTEGER NOT NULL,
                reason TEXT NOT NULL,
                observed_at_epoch INTEGER NOT NULL
            )""")

            if index_m03_samples:
                con.execute("""CREATE TABLE IF NOT EXISTS m03_ws3_trade_samples(
                    pair TEXT NOT NULL, exchange_epoch INTEGER NOT NULL,
                    sequence INTEGER NOT NULL, price_idr TEXT NOT NULL,
                    quote_idr TEXT NOT NULL, recorded_epoch INTEGER NOT NULL,
                    PRIMARY KEY(pair,exchange_epoch,sequence))""")
                con.execute("CREATE INDEX IF NOT EXISTS m03_trade_sample_expiry ON m03_ws3_trade_samples(exchange_epoch)")
                con.execute("""CREATE TABLE IF NOT EXISTS m03_ws3_trade_publication_times(
                    pair TEXT NOT NULL, epoch TEXT NOT NULL, offset INTEGER NOT NULL,
                    recorded_epoch INTEGER NOT NULL, PRIMARY KEY(pair,epoch,offset))""")
                con.execute("CREATE INDEX IF NOT EXISTS m03_trade_publication_expiry ON m03_ws3_trade_publication_times(recorded_epoch)")

    def prune_m03_samples(self, now_epoch: int) -> None:
        """Bound only M03-owned indexed data; keep current cursor boundary."""
        if not getattr(self,"_index_m03_samples",False):
            return
        cutoff=now_epoch-86400
        with self._connect() as con:
            con.execute("DELETE FROM m03_ws3_trade_samples WHERE exchange_epoch<?",(cutoff,))
            con.execute("""DELETE FROM ws_trade_publications AS p WHERE (pair,epoch,offset) IN (
                SELECT pair,epoch,offset FROM m03_ws3_trade_publication_times
                WHERE recorded_epoch<?) AND NOT EXISTS (
                SELECT 1 FROM ws_stream_state s WHERE s.pair=p.pair
                AND s.epoch=p.epoch AND s.last_offset=p.offset)""",(cutoff,))
            con.execute("""DELETE FROM m03_ws3_trade_publication_times AS t
                WHERE recorded_epoch<? AND NOT EXISTS (
                SELECT 1 FROM ws_trade_publications p WHERE p.pair=t.pair
                AND p.epoch=t.epoch AND p.offset=t.offset)""",(cutoff,))

    @classmethod
    def readonly(cls, database: str | Path) -> "PublicTradeStreamJournal":
        """Read existing WS evidence without running schema-creating __init__.

        Used by the independent M03 REST diagnostic while the ORIGINAL WS
        collector owns writes. This cannot create a missing journal.
        """
        path = Path(database)
        if not path.is_file():
            raise StreamEvidenceError("PUBLIC_WS_SQLITE_NOT_FOUND")
        obj = cls.__new__(cls)
        obj._path = path
        obj._readonly = True
        return obj

    @contextmanager
    def _connect(self):
        if getattr(self, "_readonly", False):
            con = sqlite3.connect(
                self._path.resolve().as_uri() + "?mode=ro",
                uri=True, timeout=2,
            )
            con.execute("PRAGMA query_only=ON")
        else:
            con = sqlite3.connect(self._path, timeout=10)
        try:
            with con:
                yield con
        finally:
            con.close()

    def subscription(self, pair: str) -> dict[str, Any]:
        channel = channel_for_pair(pair)
        with self._connect() as con:
            row = con.execute(
                "SELECT epoch,last_offset,status FROM ws_stream_state WHERE pair=?",
                (pair,),
            ).fetchone()
        params: dict[str, Any] = {"channel": channel}
        if row is not None and row[2] != "GAP":
            params.update({"recover": True, "offset": row[1]})
        # GAP cannot be silently replayed as continuous. The only allowed
        # next subscription is a fresh public baseline recorded as a NEW
        # discontinuous segment after verifying the server ACK.
        return {"method": 1, "params": params, "id": 2}

    def _advance(self, con, pair: str, epoch: str, offset: int, rows: list[list[Any]]) -> None:
        encoded = json.dumps(rows, separators=(",", ":"), ensure_ascii=True)
        digest = sha256(encoded.encode()).hexdigest()
        existing = con.execute(
            "SELECT epoch,last_offset,status FROM ws_stream_state WHERE pair=?",
            (pair,),
        ).fetchone()
        if existing is None:
            raise StreamEvidenceError("WS_SUBSCRIPTION_NOT_ACKNOWLEDGED")
        previous_epoch, previous_offset, status = existing
        if status in ("GAP", "DISCONNECTED") or epoch != previous_epoch:
            raise StreamEvidenceError("WS_STREAM_GAP_OR_EPOCH_CHANGED")
        if offset <= previous_offset:
            earlier = con.execute(
                "SELECT payload_sha256 FROM ws_trade_publications "
                "WHERE pair=? AND epoch=? AND offset=?",
                (pair, epoch, offset),
            ).fetchone()
            if earlier is not None and earlier[0] == digest:
                return
            raise StreamEvidenceError("WS_OFFSET_REPLAY_OR_COLLISION")
        if offset != previous_offset + 1:
            con.execute(
                "UPDATE ws_stream_state SET status='GAP' WHERE pair=?", (pair,),
            )
            return
        con.execute(
            "INSERT INTO ws_trade_publications VALUES (?,?,?,?,?)",
            (pair, epoch, offset, digest, encoded),
        )
        if getattr(self,"_index_m03_samples",False):
            recorded=int(time.time())
            for row in rows:
                if not 0 <= recorded-row[1] <= 86400:
                    continue
                old=con.execute("""SELECT price_idr,quote_idr FROM m03_ws3_trade_samples
                    WHERE pair=? AND exchange_epoch=? AND sequence=?""",
                    (pair,row[1],row[2])).fetchone()
                if old is not None and (Decimal(old[0])!=Decimal(row[4]) or
                                        Decimal(old[1])!=Decimal(row[5])):
                    raise StreamEvidenceError("M03_TRADE_IDENTITY_COLLISION")
                con.execute("""INSERT OR IGNORE INTO m03_ws3_trade_samples VALUES(?,?,?,?,?,?)""",
                            (pair,row[1],row[2],row[4],row[5],recorded))
            con.execute("INSERT INTO m03_ws3_trade_publication_times VALUES(?,?,?,?)",
                        (pair,epoch,offset,recorded))
        con.execute(
            "UPDATE ws_stream_state SET last_offset=?,status='CONTIGUOUS_OBSERVED' WHERE pair=?",
            (offset, pair),
        )

    def acknowledge(self, pair: str, response: dict[str, Any], *,
                    fresh_segment: bool = False) -> dict[str, Any]:
        channel_for_pair(pair)
        # Response must correspond to subscription request id=2.
        if (not isinstance(response, dict) or response.get("id") != 2
                or not isinstance(response.get("result"), dict)):
            raise StreamEvidenceError("INVALID_WS_SUBSCRIPTION_ACK")
        ack = response["result"]
        epoch = ack.get("epoch")
        if not isinstance(epoch, str) or not epoch.isdecimal() or not epoch:
            raise StreamEvidenceError("INVALID_WS_STREAM_EPOCH")
        offset = _nonneg_int(ack.get("offset"), "INVALID_WS_START_OFFSET")
        if ack.get("recoverable") is not True:
            raise StreamEvidenceError("WS_RECOVERY_NOT_SUPPORTED")
        # recoverable=TRUE reports CHANNEL CAPABILITY, not proof that a
        # requested replay succeeded. Centrifugo may report recovered=FALSE
        # for an unrecoverable cursor/epoch or history-retention gap. The
        # Indodax WS3 examples omit this optional protocol field, so absence
        # is UNKNOWN, never proof of full market trade-tape completeness.
        # A present, malformed flag is not silently coerced to truthy.
        if "recovered" in ack and type(ack["recovered"]) is not bool:
            raise StreamEvidenceError("INVALID_WS_RECOVERED_FLAG")
        publications = ack.get("publications", [])
        if not isinstance(publications, list):
            raise StreamEvidenceError("INVALID_WS_RECOVERED_PUBLICATIONS")
        if fresh_segment and publications:
            raise StreamEvidenceError("WS_FRESH_BASELINE_CANNOT_REPLAY")
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            stored = con.execute(
                "SELECT epoch,last_offset,status FROM ws_stream_state WHERE pair=?",
                (pair,),
            ).fetchone()
            if stored is None:
                if fresh_segment:
                    raise StreamEvidenceError("WS_FRESH_BASELINE_WITHOUT_RECORDED_GAP")
                # The initial subscription only observes events AFTER its
                # baseline offset. No earlier tape is ever considered covered.
                if publications:
                    raise StreamEvidenceError("UNSOLICITED_WS_REPLAY_ON_FRESH_START")
                con.execute(
                    "INSERT INTO ws_stream_state VALUES (?,?,?,'BASELINE_ONLY')",
                    (pair, epoch, offset),
                )
            else:
                old_epoch, old_offset, old_status = stored
                if fresh_segment:
                    if old_status != "GAP":
                        raise StreamEvidenceError("WS_FRESH_BASELINE_WITHOUT_RECORDED_GAP")
                    if epoch == old_epoch and offset <= old_offset:
                        raise StreamEvidenceError("WS_FRESH_BASELINE_NONMONOTONIC")
                    con.execute(
                        "INSERT INTO ws_stream_discontinuities "
                        "(pair,prior_epoch,prior_offset,new_epoch,new_offset,"
                        "reason,observed_at_epoch) VALUES (?,?,?,?,?,?,?)",
                        (pair,old_epoch,old_offset,epoch,offset,
                         "UNRECOVERABLE_WS_GAP_REBASELINED",int(time.time())),
                    )
                    # Keep all historical publications. The missing interval
                    # stays explicit in ws_stream_discontinuities; any new
                    # samples remain UNATTESTED and non-exhaustive.
                    con.execute(
                        "UPDATE ws_stream_state SET epoch=?,last_offset=?,"
                        "status='BASELINE_ONLY' WHERE pair=?",
                        (epoch,offset,pair),
                    )
                elif ack.get("recovered") is False:
                    # Persistent fail-closed state even if offset/epoch are
                    # unchanged and the reply omits replay publications.
                    # Never claim that 'recoverable' authenticated recovery.
                    # The original collector will stop and rebaseline only
                    # through its existing bounded GAP+discontinuity workflow.
                    con.execute(
                        "UPDATE ws_stream_state SET status='GAP' WHERE pair=?", (pair,),
                    )
                elif old_epoch != epoch:
                    con.execute(
                        "UPDATE ws_stream_state SET status='GAP' WHERE pair=?", (pair,),
                    )
                else:
                    # Official replay can INCLUDE the requested last offset.
                    # That boundary does not establish an unseen publication.
                    if stored[2] == "GAP" and not publications:
                        con.execute(
                            "UPDATE ws_stream_state SET status='GAP' WHERE pair=?",
                            (pair,),
                        )
                    else:
                        con.execute(
                            "UPDATE ws_stream_state SET status='RECOVERING' WHERE pair=?",
                            (pair,),
                        )
                        for pub in publications:
                            off, rows = self._parse_recovered(pair, pub)
                            if off == old_offset:
                                encoded = json.dumps(rows, separators=(",", ":"), ensure_ascii=True)
                                prior = con.execute(
                                    "SELECT payload_sha256 FROM ws_trade_publications "
                                    "WHERE pair=? AND epoch=? AND offset=?",
                                    (pair, epoch, old_offset),
                                ).fetchone()
                                if prior is not None and prior[0] != sha256(encoded.encode()).hexdigest():
                                    raise StreamEvidenceError("WS_RESUME_BOUNDARY_COLLISION")
                                continue
                            self._advance(con, pair, epoch, off, rows)
                            # A replay with missing offsets must COMMIT GAP;
                            # continuing would raise and roll back the marker,
                            # disguising the gap as DISCONNECTED.
                            if con.execute(
                                "SELECT status FROM ws_stream_state WHERE pair=?",
                                (pair,),
                            ).fetchone()[0] == "GAP":
                                break
                        latest = con.execute(
                            "SELECT last_offset,status FROM ws_stream_state WHERE pair=?",
                            (pair,),
                        ).fetchone()
                        if latest[0] != offset or latest[1] == "GAP":
                            con.execute(
                                "UPDATE ws_stream_state SET status='GAP' WHERE pair=?",
                                (pair,),
                            )
                        elif latest[1] == "RECOVERING":
                            con.execute(
                                "UPDATE ws_stream_state SET status='BASELINE_ONLY' WHERE pair=?",
                                (pair,),
                            )
        return self.status(pair)

    def _parse_recovered(self, pair: str, publication: Any):
        if not isinstance(publication, dict):
            raise StreamEvidenceError("INVALID_WS_RECOVERY_FRAME")
        offset = _nonneg_int(publication.get("offset"), "INVALID_WS_TRADE_OFFSET")
        rows = _trade_rows(publication.get("data"), pair, m03_index=getattr(self,"_index_m03_samples",False))
        return offset, rows

    def ingest(self, pair: str, frame: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(frame, dict) or not isinstance(frame.get("result"), dict):
            raise StreamEvidenceError("INVALID_PUBLIC_WS_FRAME")
        body = frame["result"]
        if body.get("channel") != channel_for_pair(pair):
            raise StreamEvidenceError("PUBLIC_WS_CHANNEL_MISMATCH")
        data = body.get("data")
        if not isinstance(data, dict):
            raise StreamEvidenceError("INVALID_PUBLIC_WS_ENVELOPE")
        offset = _nonneg_int(data.get("offset"), "INVALID_WS_TRADE_OFFSET")
        rows = _trade_rows(data.get("data"), pair, m03_index=getattr(self,"_index_m03_samples",False))
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            epoch_row = con.execute(
                "SELECT epoch FROM ws_stream_state WHERE pair=?", (pair,),
            ).fetchone()
            if epoch_row is None:
                raise StreamEvidenceError("WS_SUBSCRIPTION_NOT_ACKNOWLEDGED")
            self._advance(con, pair, epoch_row[0], offset, rows)
        return self.status(pair)

    def closed_minute_sample(self, pair: str, *, closed_1m_epoch: int,
                             now_epoch: int, max_publications: int = 2000) -> dict[str, Any]:
        """M04-H: inspect exactly one CLOSED minute of locally persisted WS rows.

        Diagnostic-only cross-source evidence, not an issuer/verified trade tape.
        A disconnected/restarted short probe cannot establish full minute
        coverage, even with 100% contiguous publication offsets.
        """
        channel_for_pair(pair)
        if (type(closed_1m_epoch) is not int or closed_1m_epoch <= 0
                or closed_1m_epoch % 60 != 0
                or type(now_epoch) is not int or now_epoch <= 0
                or type(max_publications) is not int
                or not 1 <= max_publications <= 10000):
            raise StreamEvidenceError("INVALID_PUBLIC_WS_MINUTE_QUERY")
        minute_end = closed_1m_epoch + 60
        base: dict[str, Any] = {
            "stage": "M04H_PUBLIC_WS_CLOSED_MINUTE_SAMPLE",
            "pair": pair,
            "closed_1m_epoch": closed_1m_epoch,
            "closed_1m_end_epoch": minute_end,
            "source": "PUBLIC_WS_SQLITE_OBSERVED_SAMPLE_ONLY",
            "status": "REJECT",
            "minute_trade_coverage_complete": False,
            "independent_indodax_origin_verified": False,
            "g1_receipt_issuance_allowed": False,
            "g2_provider_call_allowed": False,
            "execution_allowed": False,
        }
        if now_epoch < minute_end:
            return {**base, "reason": "REJECT_MINUTE_NOT_CLOSED"}
        with self._connect() as con:
            # One consistent read snapshot while the persistent collector
            # advances the same SQLite journal concurrently.
            con.execute("BEGIN")
            state = con.execute(
                "SELECT epoch,last_offset,status FROM ws_stream_state WHERE pair=?",
                (pair,),
            ).fetchone()
            if state is None:
                return {**base, "reason": "NO_PUBLIC_WS_BASELINE"}
            stream_epoch, last_offset, stream_status = state
            total_publications = con.execute(
                "SELECT COUNT(*) FROM ws_trade_publications WHERE pair=? AND epoch=?",
                (pair, stream_epoch),
            ).fetchone()[0]
            known_gap_events = con.execute(
                "SELECT COUNT(*) FROM ws_stream_discontinuities WHERE pair=?",
                (pair,),
            ).fetchone()[0]
            base["unrecoverable_gap_rebaseline_count"] = known_gap_events
            base["has_explicit_unrecoverable_ws_discontinuity"] = (
                known_gap_events > 0
            )
            # Bound historical discontinuity lookup. The marker records when
            # a fresh baseline was established, NOT when the missing trade
            # interval began. Absence of a marker inside one minute cannot
            # establish full exchange trade tape coverage.
            if known_gap_events > 10_000:
                return {**base, "reason": "REJECT_WS_GAP_LEDGER_LIMIT_EXCEEDED"}
            gap_markers = con.execute(
                "SELECT prior_epoch,prior_offset,new_epoch,new_offset,"
                "observed_at_epoch FROM ws_stream_discontinuities "
                "WHERE pair=? ORDER BY id",
                (pair,),
            ).fetchall()
            if len(gap_markers) != known_gap_events:
                return {**base, "reason": "REJECT_WS_GAP_LEDGER_INCONSISTENT"}
            # An in-minute JSON predicate alone can HIDE tampering: changing
            # rows_json from a valid target-minute publication to [] or to
            # another minute removes it from the selected rows *before* the
            # per-minute hash check. Verify ALL persisted payload digests in
            # this stream epoch first, using a bounded, streaming read that
            # does not materialize old trade payloads in memory. This is a
            # local corruption check, never source authentication.
            max_epoch_integrity_publications = 500_000
            if total_publications > max_epoch_integrity_publications:
                return {
                    **base,
                    "reason": "REJECT_WS_EPOCH_INTEGRITY_SCAN_LIMIT_EXCEEDED",
                    "epoch_publications_visible": total_publications,
                    "epoch_integrity_scan_limit": max_epoch_integrity_publications,
                }
            digest_cursor = con.execute(
                "SELECT payload_sha256,rows_json "
                "FROM ws_trade_publications WHERE pair=? AND epoch=? "
                "ORDER BY offset",
                (pair, stream_epoch),
            )
            integrity_checked_count = 0
            while True:
                chunk = digest_cursor.fetchmany(512)
                if not chunk:
                    break
                for prior_digest, prior_raw in chunk:
                    if (not isinstance(prior_raw, str)
                            or not isinstance(prior_digest, str)
                            or sha256(prior_raw.encode("utf-8")).hexdigest() != prior_digest):
                        return {**base, "reason": "REJECT_WS_JOURNAL_DIGEST_MISMATCH"}
                    integrity_checked_count += 1
            if integrity_checked_count != total_publications:
                return {**base, "reason": "REJECT_WS_JOURNAL_COUNT_CHANGED"}
            # JSON1 filters ONLY publications containing trades from the
            # specified CLOSED minute, not the earliest N rows of a growing
            # multi-day journal. This remains a bounded/unattested sample.
            rows = con.execute(
                "SELECT p.offset,p.payload_sha256,p.rows_json "
                "FROM ws_trade_publications AS p "
                "WHERE p.pair=? AND p.epoch=? AND EXISTS ("
                " SELECT 1 FROM json_each(p.rows_json) AS j"
                " WHERE json_extract(j.value,'$[1]')>=?"
                " AND json_extract(j.value,'$[1]')<?"
                ") ORDER BY p.offset LIMIT ?",
                (pair, stream_epoch, closed_1m_epoch, minute_end,
                 max_publications + 1),
            ).fetchall()
        if len(rows) > max_publications:
            return {
                **base, "reason": "REJECT_WS_SNAPSHOT_LIMIT_EXCEEDED",
                "publication_query_limit": max_publications,
                "stream_epoch": stream_epoch, "stream_status": stream_status,
                "last_publication_offset": last_offset,
            }
        # M04-Q: source-segment integrity check BEFORE exposing BUY/SELL
        # aggregates. A rebaseline can keep the same server epoch, so a
        # simple 'epoch equality' test does NOT establish one continuous
        # interval. Detect offsets on both sides of a recorded discontinuity.
        # Also reject the wall-clock minute in which rebaseline was recorded,
        # even if only one side happens to contain visible trade rows.
        offsets_in_minute = [row[0] for row in rows]
        first_offset = min(offsets_in_minute, default=None)
        final_offset = max(offsets_in_minute, default=None)
        crosses_recorded_gap = False
        rebaseline_in_minute = False
        for prior_epoch, prior_offset, new_epoch, new_offset, observed_at in gap_markers:
            if (not isinstance(prior_epoch, str) or not prior_epoch.isdecimal()
                    or not isinstance(new_epoch, str) or not new_epoch.isdecimal()
                    or type(prior_offset) is not int or prior_offset < 0
                    or type(new_offset) is not int or new_offset < 0
                    or type(observed_at) is not int or observed_at <= 0
                    or (prior_epoch == new_epoch and new_offset <= prior_offset)):
                return {**base, "reason": "REJECT_WS_GAP_LEDGER_INVALID"}
            if closed_1m_epoch <= observed_at < minute_end:
                rebaseline_in_minute = True
            if (prior_epoch == new_epoch == stream_epoch
                    and first_offset is not None
                    and first_offset <= prior_offset
                    and final_offset is not None
                    and final_offset > new_offset):
                crosses_recorded_gap = True
        if crosses_recorded_gap or rebaseline_in_minute:
            return {
                **base,
                "reason": ("REJECT_WS_MINUTE_CROSSES_RECORDED_GAP"
                           if crosses_recorded_gap
                           else "REJECT_WS_REBASELINE_IN_CLOSED_MINUTE"),
                "minute_segment_continuity_status": "RECORDED_GAP_IN_MINUTE",
                "recorded_gap_crosses_trade_offsets": crosses_recorded_gap,
                "rebaseline_observed_in_minute": rebaseline_in_minute,
                "historical_gap_start_time_verified": False,
                "observed_rows_aggregated": False,
            }
        seen_trade_identity: set[tuple[int, int]] = set()
        trade_count = 0
        buy_count = 0
        sell_count = 0
        buy_idr = Decimal(0)
        sell_idr = Decimal(0)
        min_offset: int | None = None
        max_offset: int | None = None
        for offset, digest, raw in rows:
            if sha256(raw.encode("utf-8")).hexdigest() != digest:
                return {**base, "reason": "REJECT_WS_JOURNAL_DIGEST_MISMATCH"}
            try:
                trades = _trade_rows(json.loads(raw), pair)
            except (ValueError, TypeError, StreamEvidenceError):
                return {**base, "reason": "REJECT_WS_JOURNAL_CORRUPT"}
            for trade in trades:
                timestamp = trade[1]
                if closed_1m_epoch <= timestamp < minute_end:
                    identity = (timestamp, trade[2])
                    if identity in seen_trade_identity:
                        return {**base, "reason": "REJECT_WS_DUPLICATE_TRADE_IDENTITY"}
                    seen_trade_identity.add(identity)
                    trade_count += 1
                    min_offset = offset if min_offset is None else min(min_offset, offset)
                    max_offset = offset if max_offset is None else max(max_offset, offset)
                    if trade[3] == "buy":
                        buy_count += 1
                        buy_idr += Decimal(trade[5])
                    else:
                        sell_count += 1
                        sell_idr += Decimal(trade[5])
        return {
            **base,
            "status": "PUBLIC_SAMPLE_OBSERVED" if trade_count else "PUBLIC_SAMPLE_EMPTY",
            "reason": "FULL_MINUTE_TRADE_TAPE_NOT_PROVEN",
            "minute_segment_continuity_status": "NO_RECORDED_GAP_IN_MINUTE_UNATTESTED",
            "recorded_gap_crosses_trade_offsets": False,
            "rebaseline_observed_in_minute": False,
            "historical_gap_start_time_verified": False,
            "observed_rows_aggregated": True,
            "stream_epoch": stream_epoch,
            "stream_status": stream_status,
            "last_publication_offset": last_offset,
            "sampled_publication_count_total_epoch": total_publications,
            "epoch_publication_digests_checked": integrity_checked_count,
            "sampled_publications_in_minute": (
                0 if min_offset is None else sum(
                    1 for offset, _, raw in rows
                    if any(closed_1m_epoch <= trade[1] < minute_end
                           for trade in json.loads(raw))
                )
            ),
            "offset_minimum_in_minute": min_offset,
            "offset_maximum_in_minute": max_offset,
            "trade_rows_visible_in_minute": trade_count,
            "buy_trade_rows_visible": buy_count,
            "sell_trade_rows_visible": sell_count,
            "buy_idr_volume_visible": str(buy_idr),
            "sell_idr_volume_visible": str(sell_idr),
            "total_idr_volume_visible": str(buy_idr + sell_idr),
            "observed_publications_do_not_imply_full_minute": True,
        }

    def read_count(self, pair: str) -> int:
        channel_for_pair(pair)
        with self._connect() as con:
            return int(con.execute(
                "SELECT COUNT(*) FROM ws_trade_publications WHERE pair=?", (pair,),
            ).fetchone()[0])

    def mark_disconnected(self, pair: str) -> dict[str, Any]:
        channel_for_pair(pair)
        with self._connect() as con:
            con.execute(
                "UPDATE ws_stream_state SET status=CASE WHEN status='GAP' "
                "THEN 'GAP' ELSE 'DISCONNECTED' END WHERE pair=?",
                (pair,),
            )
        return self.status(pair)

    def status(self, pair: str) -> dict[str, Any]:
        channel_for_pair(pair)
        with self._connect() as con:
            state = con.execute(
                "SELECT epoch,last_offset,status FROM ws_stream_state WHERE pair=?",
                (pair,),
            ).fetchone()
            count = con.execute(
                "SELECT COUNT(*) FROM ws_trade_publications WHERE pair=?", (pair,),
            ).fetchone()[0]
        with self._connect() as con:
            gap_count = con.execute(
                "SELECT COUNT(*) FROM ws_stream_discontinuities WHERE pair=?",
                (pair,),
            ).fetchone()[0]
        return {
            "pair": pair,
            "channel": channel_for_pair(pair),
            "stream_status": state[2] if state else "UNSUBSCRIBED",
            "unrecoverable_gap_rebaseline_count": gap_count,
            "continuous_trade_tape_not_proven": True,
            "stream_epoch": state[0] if state else None,
            "last_publication_offset": state[1] if state else None,
            "persisted_publications": count,
            "observed_stream_continuity": state is not None and state[2] == "CONTIGUOUS_OBSERVED",
            "indodax_trade_tape_completeness_proven": False,
            "source_authenticated_for_g1": False,
            "g1_receipt_issuance_allowed": False,
            "g2_provider_call_allowed": False,
            "execution_allowed": False,
        }


def probe_official_public_ws(
    journal: PublicTradeStreamJournal, *, pair: str = "btc_idr",
    seconds: int = 12, windows: int | None = 1, on_window=None,
) -> dict[str, Any]:
    """One authenticated socket with optional 30s evaluation windows.

    Default research probe remains one bounded window. Explicit windows=None
    keeps the SAME authenticated public socket across arbitrarily many
    evaluations; callback merely reports persisted PUBLIC observations, never
    certifies trade-tape completeness or authorizes an order.

    Only one channel per session; never logs the public token or raw socket
    exception. websocket-client is an optional dependency for THIS probe.
    No private authenticated API and no paid model request.
    """
    if type(seconds) is not int or not 3 <= seconds <= 30:
        raise StreamEvidenceError("INVALID_WS_PROBE_SECONDS")
    if ((windows is not None and
         (type(windows) is not int or not 1 <= windows <= 4))
            or (on_window is not None and not callable(on_window))
            or (windows is None and on_window is None)):
        raise StreamEvidenceError("INVALID_WS_PROBE_WINDOWS")
    channel_for_pair(pair)
    try:
        import websocket  # type: ignore[import-not-found]
    except ImportError:
        raise StreamEvidenceError("PUBLIC_WS_CLIENT_PACKAGE_MISSING") from None
    import time
    started = time.monotonic()
    ws = None
    acked = False
    disconnect_recorded = False
    received = 0
    # Distinguish publications recovered during subscription from those
    # seen in live frames. Count only new SQLite inserts, not replay frames.
    publications_at_start = journal.read_count(pair)
    recovered_new_publications = 0
    server_replay_rows = 0
    resume_requested = False
    resume_requested_offset = None
    server_ack_offset = None
    server_ack_epoch = None
    publications_after_ack = publications_at_start
    recovery_diagnostic = "NOT_SUBSCRIBED"
    heartbeat_confirmed = False
    other_frames = 0
    timeout_reads = 0
    # Stable, bounded failure-phase identifiers. Never disclose socket
    # exception bodies, frames, or the public authorization token.
    failure_phase = "CONNECT"
    try:
        ws = websocket.create_connection(WS_URL, timeout=5, enable_multithread=True)
        ws.settimeout(1.0)
        failure_phase = "AUTH_SEND"
        ws.send(json.dumps({"params": {"token": PUBLIC_TOKEN}, "id": 1}))
        deadline = time.monotonic() + 8
        authenticated = False
        while time.monotonic() < deadline:
            failure_phase = "AUTH_RECEIVE"
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not isinstance(raw, str) or len(raw) > 512_000:
                raise StreamEvidenceError("INVALID_PUBLIC_WS_MESSAGE_SIZE")
            failure_phase = "AUTH_DECODE"
            message = json.loads(raw)
            if isinstance(message, dict) and message.get("id") == 1:
                if (not isinstance(message.get("result"), dict)
                        or not isinstance(message["result"].get("client"), str)):
                    raise StreamEvidenceError("PUBLIC_WS_AUTH_REJECTED")
                authenticated = True
                break
        if not authenticated:
            raise StreamEvidenceError("PUBLIC_WS_AUTH_TIMEOUT")
        failure_phase = "SUBSCRIBE_PREPARE"
        subscribe_request = journal.subscription(pair)
        params = subscribe_request["params"]
        resume_requested = params.get("recover") is True
        resume_requested_offset = params.get("offset")
        fresh_segment = (
            not resume_requested
            and journal.status(pair)["stream_status"] == "GAP"
        )
        failure_phase = "SUBSCRIBE_SEND"
        ws.send(json.dumps(subscribe_request))
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            failure_phase = "SUBSCRIBE_RECEIVE"
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not isinstance(raw, str) or len(raw) > 512_000:
                raise StreamEvidenceError("INVALID_PUBLIC_WS_MESSAGE_SIZE")
            failure_phase = "SUBSCRIBE_DECODE"
            msg = json.loads(raw)
            if isinstance(msg, dict) and msg.get("id") == 2:
                result = msg.get("result")
                if not isinstance(result, dict):
                    raise StreamEvidenceError("INVALID_WS_SUBSCRIPTION_ACK")
                replay = result.get("publications", [])
                if not isinstance(replay, list):
                    raise StreamEvidenceError("INVALID_WS_RECOVERED_PUBLICATIONS")
                server_replay_rows = len(replay)
                server_ack_offset = result.get("offset")
                server_ack_epoch = result.get("epoch")
                failure_phase = "SUBSCRIBE_ACK"
                subscription_state = journal.acknowledge(
                    pair, msg, fresh_segment=fresh_segment,
                )
                if subscription_state["stream_status"] == "GAP":
                    raise StreamEvidenceError("WS_RECOVERY_GAP_UNRESOLVED")
                publications_after_ack = journal.read_count(pair)
                recovered_new_publications = publications_after_ack - publications_at_start
                if recovered_new_publications < 0:
                    raise StreamEvidenceError("PUBLIC_WS_RECOVERY_COUNTER_INVALID")
                if not resume_requested:
                    recovery_diagnostic = "INITIAL_SUBSCRIPTION"
                elif subscription_state["stream_status"] == "GAP":
                    recovery_diagnostic = "REPLAY_GAP_UNRESOLVED"
                elif recovered_new_publications:
                    recovery_diagnostic = "REPLAY_NEW_PUBLICATIONS_PERSISTED"
                elif server_replay_rows:
                    recovery_diagnostic = "REPLAY_BOUNDARY_ONLY"
                else:
                    recovery_diagnostic = "RESUME_NO_NEW_PUBLICATIONS"
                acked = True
                break
        if not acked:
            raise StreamEvidenceError("PUBLIC_WS_SUBSCRIBE_TIMEOUT")
        # A rolling 30-second window is a measurement boundary, never a
        # reconnect boundary. One socket stays open across all evaluations.
        # A new heartbeat ID per window prevents a late response being
        # mistaken for proof that a newer window is alive.
        window_index = 0
        window_start_publications = publications_after_ack
        while windows is None or window_index < windows:
            heartbeat_id = 3 + window_index
            window_heartbeat = False
            failure_phase = "HEARTBEAT_SEND"
            ws.send(json.dumps({"method": 7, "id": heartbeat_id}))
            until = time.monotonic() + seconds
            while time.monotonic() < until:
                failure_phase = "STREAM_RECEIVE"
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    timeout_reads += 1
                    continue
                if raw is None or raw == "":
                    raise StreamEvidenceError("PUBLIC_WS_STREAM_RECEIVE_EMPTY_OR_CLOSED")
                if not isinstance(raw, str) or len(raw) > 512_000:
                    raise StreamEvidenceError("INVALID_PUBLIC_WS_MESSAGE_SIZE")
                failure_phase = "STREAM_DECODE"
                msg = json.loads(raw)
                if not isinstance(msg, dict):
                    raise StreamEvidenceError("INVALID_PUBLIC_WS_MESSAGE_SHAPE")
                if msg.get("id") == heartbeat_id and set(msg) == {"id"}:
                    window_heartbeat = True
                    heartbeat_confirmed = True
                elif (isinstance(msg.get("result"), dict)
                        and msg["result"].get("channel") == channel_for_pair(pair)):
                    failure_phase = "STREAM_INGEST"
                    journal.ingest(pair, msg)
                    received += 1
                else:
                    other_frames += 1
            failure_phase = "STREAM_STATUS"
            during_probe = journal.status(pair)
            if during_probe["stream_status"] == "GAP":
                raise StreamEvidenceError("WS_RECOVERY_GAP_UNRESOLVED")
            window_live = (
                during_probe["persisted_publications"] - window_start_publications
            )
            if window_live < 0:
                raise StreamEvidenceError("PUBLIC_WS_LIVE_COUNTER_INVALID")
            if on_window is not None:
                # A quiet market is valid only if this exact window's
                # protocol heartbeat was acknowledged.
                if not window_heartbeat:
                    raise StreamEvidenceError("WS_HEARTBEAT_NOT_CONFIRMED")
                on_window({
                    "session": window_index + 1,
                    "subscription_acknowledged": True,
                    "protocol_heartbeat_confirmed": window_heartbeat,
                    "stream_status_during_probe": during_probe["stream_status"],
                    "recovered_publications_newly_persisted": (
                        recovered_new_publications if window_index == 0 else 0
                    ),
                    "live_publications_newly_persisted": window_live,
                    "last_publication_offset": during_probe["last_publication_offset"],
                    "persisted_publications": during_probe["persisted_publications"],
                    "unrecoverable_gap_rebaseline_count": (
                        during_probe["unrecoverable_gap_rebaseline_count"]
                    ),
                    "g1_receipt_issuance_allowed": False,
                    "g2_provider_call_allowed": False,
                    "execution_allowed": False,
                })
            window_start_publications = during_probe["persisted_publications"]
            window_index += 1
        live_publications_persisted = (
            during_probe["persisted_publications"] - publications_after_ack
        )
        if live_publications_persisted < 0:
            raise StreamEvidenceError("PUBLIC_WS_LIVE_COUNTER_INVALID")
        # This is a bounded probe, not a daemon. Report the persisted
        # post-close state truthfully rather than a stale ACTIVE snapshot.
        failure_phase = "STREAM_FINALIZE"
        persisted = journal.mark_disconnected(pair)
        disconnect_recorded = True  # Cleanup state, NOT subscription ACK.
        return {
            **persisted,
            "stream_status_during_probe": during_probe["stream_status"],
            "public_ws_probe_completed": True,
            "received_publications_in_this_probe": received,
            "live_publications_newly_persisted": live_publications_persisted,
            "publications_persisted_before_subscription": publications_at_start,
            "publications_persisted_after_subscription": publications_after_ack,
            "resume_requested": resume_requested,
            "resume_requested_offset": resume_requested_offset,
            "fresh_discontinuous_segment": fresh_segment,
            "unrecoverable_gap_rebaseline_count": persisted["unrecoverable_gap_rebaseline_count"],
            "server_ack_epoch": server_ack_epoch,
            "server_ack_offset": server_ack_offset,
            "server_replayed_publication_frames": server_replay_rows,
            "recovered_publications_newly_persisted": recovered_new_publications,
            "recovery_diagnostic": recovery_diagnostic,
            "subscription_acknowledged": acked,
            "protocol_heartbeat_confirmed": heartbeat_confirmed,
            "unrecognized_frame_count": other_frames,
            "socket_read_timeouts": timeout_reads,
            "ws_activity_diagnostic": (
                "PUBLIC_TRADE_FRAMES_RECEIVED" if received
                else "QUIET_CHANNEL_PROTOCOL_ALIVE" if heartbeat_confirmed
                else "NO_TRADE_FRAMES_HEARTBEAT_UNCONFIRMED"
            ),
            "probe_wall_elapsed_seconds": round(time.monotonic() - started, 2),
        }
    except StreamEvidenceError:
        raise
    except Exception as exc:
        # No exception text, URLs, raw frames, or tokens in logs/sidecar.
        # Keep fail-stop, but expose a SAFE subtype on actual stream recv.
        # Crucial: no blanket retry, no fabricated trade continuity.
        reason = (
            _safe_ws_receive_failure(exc, websocket)
            if failure_phase in ("STREAM_RECEIVE", "HEARTBEAT_SEND")
            else _safe_ws_transport_failure(exc, websocket, failure_phase)
            if failure_phase in ("CONNECT", "AUTH_RECEIVE")
            else "PUBLIC_WS_" + failure_phase + "_FAILED"
        )
        raise StreamEvidenceError(reason) from None
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        # The next invocation MUST request replay from the last persisted
        # offset. Disconnect is not a certified continuous trade tape.
        if acked and not disconnect_recorded:
            journal.mark_disconnected(pair)



# Only an explicitly classified connection loss gets one immediate new socket.
# An HTTP 401/403/429, TLS/protocol/payload error, unknown exception or
# incomplete source receipt must never be retried through this fast path.
# Re-subscribing uses the original durable SQLite offset/gap validation.
_IMMEDIATE_TRANSIENT_WS_RECONNECT = frozenset({
    "PUBLIC_WS_STREAM_RECEIVE_EMPTY_OR_CLOSED",
    "PUBLIC_WS_STREAM_RECEIVE_CLOSED_OR_LOST",
    "PUBLIC_WS_STREAM_RECEIVE_CONNECTION_RESET",
    "PUBLIC_WS_STREAM_RECEIVE_CONNECTION_ABORTED",
    "PUBLIC_WS_STREAM_RECEIVE_BROKEN_PIPE",
    "PUBLIC_WS_STREAM_RECEIVE_EOF",
    "PUBLIC_WS_CONNECT_CLOSED_OR_LOST",
    "PUBLIC_WS_CONNECT_CONNECTION_RESET",
    "PUBLIC_WS_CONNECT_CONNECTION_ABORTED",
    "PUBLIC_WS_CONNECT_CONNECTION_REFUSED",
    "PUBLIC_WS_CONNECT_BROKEN_PIPE",
    "PUBLIC_WS_CONNECT_TIMEOUT",
    "PUBLIC_WS_CONNECT_HTTP_UNAVAILABLE",  # Only explicitly typed 502/503/504.
    "PUBLIC_WS_AUTH_RECEIVE_CLOSED_OR_LOST",
    "PUBLIC_WS_AUTH_RECEIVE_CONNECTION_RESET",
    "PUBLIC_WS_AUTH_RECEIVE_CONNECTION_ABORTED",
    "PUBLIC_WS_AUTH_RECEIVE_BROKEN_PIPE",
    "PUBLIC_WS_AUTH_RECEIVE_TIMEOUT",
})


def collect_official_public_ws(
    journal: PublicTradeStreamJournal, *, pair: str = "btc_idr",
    sessions: int = 2, session_seconds: int = 30, pause_seconds: int = 1,
    probe=None, sleep=None, reconnect_until_recovered: bool = False,
) -> dict[str, Any]:
    """M04-I: bounded PUBLIC trade collection using existing verified WS probe.

    Default research acquisition stays finite and fail-stop. The explicit
    ORIGINAL supervised-service option reconnects indefinitely on ONLY typed
    transient transport losses, without an attempt-count cap. First retry
    starts immediately; subsequent failures use bounded exponential backoff.
    This never relaxes source continuity, G1, AI, or execution authority.
    Identical SQLite journal is reused across sessions, so server offset
    recovery can cover short intentional reconnect gaps. No other scanner.
    """
    channel_for_pair(pair)
    if (type(sessions) is not int or not 2 <= sessions <= 10
            or type(session_seconds) is not int or not 3 <= session_seconds <= 30
            or type(pause_seconds) is not int or not 0 <= pause_seconds <= 10
            or type(reconnect_until_recovered) is not bool):
        raise StreamEvidenceError("INVALID_PUBLIC_WS_COLLECTOR_BOUNDS")
    import time
    session_probe = probe if probe is not None else probe_official_public_ws
    wait = sleep if sleep is not None else time.sleep
    samples: list[dict[str, Any]] = []
    total_recovered = 0
    total_live = 0
    safe_count = 0
    attempts_after_unrecoverable_gap = 0
    immediate_transient_reconnect_attempts = 0
    persistent_transient_reconnect_attempts = 0
    attempted = 0
    halted_reason: str | None = None
    for index in range(sessions):
        if index:
            wait(pause_seconds)
        attempted += 1
        try:
            if reconnect_until_recovered:
                failures = 0
                while True:
                    try:
                        result = session_probe(
                            journal, pair=pair, seconds=session_seconds,
                        )
                        break
                    except StreamEvidenceError as transient:
                        # Only an enumerated network/socket loss may
                        # trigger this loop. A malformed frame, source GAP,
                        # exchange refusal, HTTP 401/403/429, TLS, protocol
                        # or unexpected exception propagates to fail-stop.
                        if str(transient) not in _IMMEDIATE_TRANSIENT_WS_RECONNECT:
                            raise
                        persistent_transient_reconnect_attempts += 1
                        if failures:
                            # First retry is immediate. During a sustained
                            # outage apply 1/2/4/8/16/30s pauses, with NO
                            # total-count limit. Actual network dial time
                            # is outside this deliberate sleep.
                            wait(min(30, 2 ** min(failures - 1, 5)))
                        failures += 1
                        # Each probe is a new authorized WS socket and fresh
                        # subscribe/recover against the ORIGINAL journal.
            else:
                result = session_probe(
                    journal, pair=pair, seconds=session_seconds,
                )
        except StreamEvidenceError as exc:
            # Attempt a NEW public socket immediately, at most ONCE per batch.
            # No timer wait for the first typed transient disconnect; repeated
            # faults retain the original fail-stop and backoff rules. The
            # replacement session must freshly authenticate, subscribe,
            # validate replay/gaps, and complete the whole bounded probe.
            if (immediate_transient_reconnect_attempts == 0
                    and str(exc) in _IMMEDIATE_TRANSIENT_WS_RECONNECT):
                immediate_transient_reconnect_attempts = 1
                try:
                    result = session_probe(
                        journal, pair=pair, seconds=session_seconds,
                    )
                except StreamEvidenceError as retry_exc:
                    halted_reason = "WS_SESSION_REJECTED:" + str(retry_exc)
                    break
                except Exception:
                    halted_reason = "WS_SESSION_UNAVAILABLE"
                    break
            # ONE controlled fresh PUBLIC baseline retry after a *persisted*
            # GAP, preserving historical publications and recording the
            # discontinuity. No retries for transport/auth/other faults.
            elif (attempts_after_unrecoverable_gap == 0
                    and str(exc) in ("WS_RECOVERY_GAP_UNRESOLVED",
                                     "WS_STREAM_GAP_OR_EPOCH_CHANGED")
                    and journal.status(pair)["stream_status"] == "GAP"):
                attempts_after_unrecoverable_gap += 1
                try:
                    result = session_probe(
                        journal, pair=pair, seconds=session_seconds,
                    )
                except StreamEvidenceError as retry_exc:
                    halted_reason = "WS_SESSION_REJECTED:" + str(retry_exc)
                    break
                except Exception:
                    halted_reason = "WS_SESSION_UNAVAILABLE"
                    break
            else:
                # Never log raw transport exception text/secret bodies.
                halted_reason = "WS_SESSION_REJECTED:" + str(exc)
                break
        except Exception:
            halted_reason = "WS_SESSION_UNAVAILABLE"
            break
        if not isinstance(result, dict):
            halted_reason = "WS_SESSION_MISSING_REPORT"
            break
        if (result.get("g1_receipt_issuance_allowed") is not False
                or result.get("g2_provider_call_allowed") is not False
                or result.get("execution_allowed") is not False):
            halted_reason = "WS_SESSION_UNSAFE_AUTHORITY_CLAIM"
            break
        record = {
            "session": index + 1,
            "subscription_acknowledged": result.get("subscription_acknowledged") is True,
            "protocol_heartbeat_confirmed": result.get("protocol_heartbeat_confirmed") is True,
            "stream_status_during_probe": result.get("stream_status_during_probe"),
            "persisted_stream_status": result.get("stream_status"),
            "resume_requested": result.get("resume_requested"),
            "resume_requested_offset": result.get("resume_requested_offset"),
            "fresh_discontinuous_segment": result.get("fresh_discontinuous_segment") is True,
            "recovery_diagnostic": result.get("recovery_diagnostic"),
            "server_replayed_publication_frames": result.get("server_replayed_publication_frames"),
            "recovered_publications_newly_persisted": result.get("recovered_publications_newly_persisted"),
            "live_publications_newly_persisted": result.get("live_publications_newly_persisted"),
            "last_publication_offset": result.get("last_publication_offset"),
            "persisted_publications": result.get("persisted_publications"),
        }
        samples.append(record)
        if not record["subscription_acknowledged"]:
            halted_reason = "WS_SUBSCRIPTION_NOT_CONFIRMED"
            break
        if not record["protocol_heartbeat_confirmed"]:
            halted_reason = "WS_HEARTBEAT_NOT_CONFIRMED"
            break
        if (record["stream_status_during_probe"] not in
                ("CONTIGUOUS_OBSERVED", "BASELINE_ONLY")
                or record["persisted_stream_status"] != "DISCONNECTED"):
            halted_reason = "WS_CONTINUITY_OR_DISCONNECT_REJECTED"
            break
        recovered = record["recovered_publications_newly_persisted"]
        live = record["live_publications_newly_persisted"]
        if (type(recovered) is not int or recovered < 0
                or type(live) is not int or live < 0):
            halted_reason = "WS_PUBLICATION_ACCOUNTING_INVALID"
            break
        total_recovered += recovered
        total_live += live
        safe_count += 1
    persisted = journal.status(pair)
    complete = halted_reason is None and safe_count == sessions
    return {
        "stage": "M04I_BOUNDED_PUBLIC_WS_COLLECTION",
        "pair": pair,
        "status": "PUBLIC_SAMPLE_COLLECTED_UNATTESTED" if complete else "REJECT",
        "reason": "PUBLIC_WS_SAMPLE_IS_NOT_CERTIFIED_TAPE" if complete else halted_reason,
        "requested_sessions": sessions,
        "completed_safe_sessions": safe_count,
        "attempted_sessions": attempted,
        "total_recovered_publications_newly_persisted": total_recovered,
        "fresh_retry_after_unrecoverable_gap_count": attempts_after_unrecoverable_gap,
        "immediate_transient_reconnect_attempts": immediate_transient_reconnect_attempts,
        "persistent_transient_reconnect_attempts": persistent_transient_reconnect_attempts,
        "reconnect_until_recovered": reconnect_until_recovered,
        "unrecoverable_gap_rebaseline_count": persisted["unrecoverable_gap_rebaseline_count"],
        "full_stream_continuity_proven": False,
        "total_live_publications_newly_persisted": total_live,
        "last_publication_offset": persisted["last_publication_offset"],
        "persisted_publications": persisted["persisted_publications"],
        "persisted_stream_status": persisted["stream_status"],
        "session_reports": samples,
        "unattested_public_sample_only": True,
        "full_closed_minute_coverage_proven": False,
        "indodax_trade_tape_completeness_proven": False,
        "source_authenticated_for_g1": False,
        "g1_receipt_issuance_allowed": False,
        "g2_provider_call_allowed": False,
        "execution_allowed": False,
    }
