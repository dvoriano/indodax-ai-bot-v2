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


def _trade_rows(rows: Any, pair: str) -> list[list[Any]]:
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

    def __init__(self, database: str | Path):
        path = Path(database)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
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

    @contextmanager
    def _connect(self):
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
                "SELECT epoch,last_offset FROM ws_stream_state WHERE pair=?", (pair,),
            ).fetchone()
        params: dict[str, Any] = {"channel": channel}
        if row is not None:
            params.update({"recover": True, "offset": row[1]})
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
        con.execute(
            "UPDATE ws_stream_state SET last_offset=?,status='CONTIGUOUS_OBSERVED' WHERE pair=?",
            (offset, pair),
        )

    def acknowledge(self, pair: str, response: dict[str, Any]) -> dict[str, Any]:
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
        publications = ack.get("publications", [])
        if not isinstance(publications, list):
            raise StreamEvidenceError("INVALID_WS_RECOVERED_PUBLICATIONS")
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            stored = con.execute(
                "SELECT epoch,last_offset,status FROM ws_stream_state WHERE pair=?",
                (pair,),
            ).fetchone()
            if stored is None:
                # The initial subscription only observes events AFTER its
                # baseline offset. No earlier tape is ever considered covered.
                if publications:
                    raise StreamEvidenceError("UNSOLICITED_WS_REPLAY_ON_FRESH_START")
                con.execute(
                    "INSERT INTO ws_stream_state VALUES (?,?,?,'BASELINE_ONLY')",
                    (pair, epoch, offset),
                )
            else:
                old_epoch, old_offset, _ = stored
                if old_epoch != epoch:
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
        rows = _trade_rows(publication.get("data"), pair)
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
        rows = _trade_rows(data.get("data"), pair)
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
            state = con.execute(
                "SELECT epoch,last_offset,status FROM ws_stream_state WHERE pair=?",
                (pair,),
            ).fetchone()
            if state is None:
                return {**base, "reason": "NO_PUBLIC_WS_BASELINE"}
            stream_epoch, last_offset, stream_status = state
            rows = con.execute(
                "SELECT offset,payload_sha256,rows_json FROM ws_trade_publications "
                "WHERE pair=? AND epoch=? ORDER BY offset LIMIT ?",
                (pair, stream_epoch, max_publications + 1),
            ).fetchall()
        if len(rows) > max_publications:
            return {
                **base, "reason": "REJECT_WS_SNAPSHOT_LIMIT_EXCEEDED",
                "publication_query_limit": max_publications,
                "stream_epoch": stream_epoch, "stream_status": stream_status,
                "last_publication_offset": last_offset,
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
            "stream_epoch": stream_epoch,
            "stream_status": stream_status,
            "last_publication_offset": last_offset,
            "sampled_publication_count_total_epoch": len(rows),
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
        return {
            "pair": pair,
            "channel": channel_for_pair(pair),
            "stream_status": state[2] if state else "UNSUBSCRIBED",
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
    seconds: int = 12,
) -> dict[str, Any]:
    """One bounded public WS session, persisted in the V2-only SQLite DB.

    Only one channel per session; never logs the public token or raw socket
    exception. websocket-client is an optional dependency for THIS probe.
    No private authenticated API and no paid model request.
    """
    if type(seconds) is not int or not 3 <= seconds <= 30:
        raise StreamEvidenceError("INVALID_WS_PROBE_SECONDS")
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
    try:
        ws = websocket.create_connection(WS_URL, timeout=5, enable_multithread=True)
        ws.settimeout(1.0)
        ws.send(json.dumps({"params": {"token": PUBLIC_TOKEN}, "id": 1}))
        deadline = time.monotonic() + 8
        authenticated = False
        while time.monotonic() < deadline:
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not isinstance(raw, str) or len(raw) > 512_000:
                raise StreamEvidenceError("INVALID_PUBLIC_WS_MESSAGE_SIZE")
            message = json.loads(raw)
            if isinstance(message, dict) and message.get("id") == 1:
                if (not isinstance(message.get("result"), dict)
                        or not isinstance(message["result"].get("client"), str)):
                    raise StreamEvidenceError("PUBLIC_WS_AUTH_REJECTED")
                authenticated = True
                break
        if not authenticated:
            raise StreamEvidenceError("PUBLIC_WS_AUTH_TIMEOUT")
        subscribe_request = journal.subscription(pair)
        params = subscribe_request["params"]
        resume_requested = params.get("recover") is True
        resume_requested_offset = params.get("offset")
        ws.send(json.dumps(subscribe_request))
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not isinstance(raw, str) or len(raw) > 512_000:
                raise StreamEvidenceError("INVALID_PUBLIC_WS_MESSAGE_SIZE")
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
                subscription_state = journal.acknowledge(pair, msg)
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
        # Documented WS method 7 heartbeat distinguishes a quiet channel
        # from a connection that cannot reply to control frames.
        ws.send(json.dumps({"method": 7, "id": 3}))
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                timeout_reads += 1
                continue
            if not isinstance(raw, str) or len(raw) > 512_000:
                raise StreamEvidenceError("INVALID_PUBLIC_WS_MESSAGE_SIZE")
            msg = json.loads(raw)
            if not isinstance(msg, dict):
                raise StreamEvidenceError("INVALID_PUBLIC_WS_MESSAGE_SHAPE")
            if msg.get("id") == 3 and set(msg) == {"id"}:
                heartbeat_confirmed = True
            elif (isinstance(msg.get("result"), dict)
                    and msg["result"].get("channel") == channel_for_pair(pair)):
                journal.ingest(pair, msg)
                received += 1
            else:
                other_frames += 1
        during_probe = journal.status(pair)
        live_publications_persisted = (
            during_probe["persisted_publications"] - publications_after_ack
        )
        if live_publications_persisted < 0:
            raise StreamEvidenceError("PUBLIC_WS_LIVE_COUNTER_INVALID")
        # This is a bounded probe, not a daemon. Report the persisted
        # post-close state truthfully rather than a stale ACTIVE snapshot.
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
    except Exception:
        # No socket exception text or token in operator output/logs.
        raise StreamEvidenceError("PUBLIC_WS_CONNECTION_OR_FRAME_FAILED") from None
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
