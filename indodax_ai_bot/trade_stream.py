"""M04-E: durable PUBLIC Indodax trade-stream checkpoint, gap detection.

Official Marketdata-websocket.md: market:trade-activity-<pair>, subscription
epoch/offset, recover=true with previous offset, trade sequence in row[2].
No private credentials, BUY/SELL, AI issuer, or assumptions about exchange-wide
complete trade-event coverage. WebSocket client is optional, imported at call.
"""
from __future__ import annotations

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

    def _connect(self):
        return sqlite3.connect(self._path, timeout=10)

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
        if status == "GAP" or epoch != previous_epoch:
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
                    # Recovery ack may contain publications after old offset.
                    # Only contiguous publications may bridge the old cursor
                    # to the newly announced server offset.
                    for pub in publications:
                        off, rows = self._parse_recovered(pair, pub)
                        self._advance(con, pair, epoch, off, rows)
                    latest = con.execute(
                        "SELECT last_offset,status FROM ws_stream_state WHERE pair=?",
                        (pair,),
                    ).fetchone()
                    if latest[0] != offset or latest[1] == "GAP":
                        con.execute(
                            "UPDATE ws_stream_state SET status='GAP' WHERE pair=?", (pair,),
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

    def mark_disconnected(self, pair: str) -> dict[str, Any]:
        channel_for_pair(pair)
        with self._connect() as con:
            con.execute(
                "UPDATE ws_stream_state SET status='DISCONNECTED' WHERE pair=?",
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
