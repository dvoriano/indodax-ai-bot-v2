"""M04-K: read-only operational health for the existing PUBLIC BTC WS journal.

This is a last-completed-batch consistency check, NOT a liveness monitor,
authenticated market evidence, proof of full candles, or a systemd query.
Neither status nor any sidecar can authorize G1, AI or trading.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote
import time


def public_ws_health(
    database: str | Path,
    status_file: str | Path,
    *,
    now_epoch: int | None = None,
    max_age_seconds: int = 3600,
) -> dict[str, Any]:
    """Never create files, connect to network, call the exchange, or spend money.

    Compare a well-formed last-batch status to a read-only SQLite snapshot.
    Later publications may increase the cursor/count, but regressions reject.
    """
    base: dict[str, Any] = {
        "stage": "M04K_PUBLIC_WS_OPERATOR_HEALTH",
        "status": "REJECT",
        "pair": "btc_idr",
        "last_batch_recent_and_consistent": False,
        "systemd_service_active": None,
        "live_stream_currently_connected": None,
        "source_authenticated_for_g1": False,
        "indodax_trade_tape_completeness_proven": False,
        "full_closed_minute_coverage_proven": False,
        "g1_receipt_issuance_allowed": False,
        "g2_provider_call_allowed": False,
        "execution_allowed": False,
    }
    if (type(max_age_seconds) is not int or not 60 <= max_age_seconds <= 86400
            or (now_epoch is not None and (type(now_epoch) is not int or now_epoch <= 0))):
        return {**base, "reason": "INVALID_PUBLIC_WS_HEALTH_BOUNDS"}
    current = int(time.time()) if now_epoch is None else now_epoch
    db = Path(database)
    sidecar = Path(status_file)
    if not db.is_file() or not sidecar.is_file():
        return {**base, "reason": "WS_HEALTH_FILES_MISSING"}
    # Bound file size before reading or JSON parsing an operator-writable file.
    try:
        if sidecar.stat().st_size > 32768:
            return {**base, "reason": "WS_HEALTH_STATUS_OVERSIZED"}
        raw = sidecar.read_text(encoding="utf-8")
        document = json.loads(raw)
    except (OSError, UnicodeError, ValueError):
        return {**base, "reason": "WS_HEALTH_STATUS_INVALID"}
    if not isinstance(document, dict):
        return {**base, "reason": "WS_HEALTH_STATUS_INVALID"}
    exact = {
        "stage": "M04J_PUBLIC_WS_SUPERVISION_SLICE",
        "status": "PUBLIC_COLLECTION_BATCH_OK_UNATTESTED",
        "pair": "btc_idr",
        "stream_status": "DISCONNECTED",
        "restart_only_after_clean_batch": True,
        "g1_receipt_issuance_allowed": False,
        "g2_provider_call_allowed": False,
        "execution_allowed": False,
        "indodax_trade_tape_completeness_proven": False,
        "source_authenticated_for_g1": False,
    }
    if any(type(document.get(k)) is not type(v) or document.get(k) != v
           for k, v in exact.items()):
        return {**base, "reason": "WS_HEALTH_UNTRUSTED_BATCH_STATUS"}
    epoch = document.get("last_completed_batch_epoch")
    offset = document.get("last_publication_offset")
    count = document.get("persisted_publications")
    stream_epoch = document.get("stream_epoch")
    total = document.get("requested_sessions")
    completed = document.get("completed_safe_sessions")
    if (type(epoch) is not int or epoch <= 0
            or type(count) is not int or count < 0
            or (offset is not None and (type(offset) is not int or offset < 0))
            or type(stream_epoch) is not str or not stream_epoch.isdecimal()
            or type(total) is not int or not 2 <= total <= 10
            or type(completed) is not int or completed != total):
        return {**base, "reason": "WS_HEALTH_MISSING_OR_INVALID_FIELDS"}
    age = current - epoch
    if age < 0:
        return {**base, "reason": "WS_HEALTH_FUTURE_BATCH_TIME", "batch_age_seconds": age}
    if age > max_age_seconds:
        return {
            **base, "reason": "WS_HEALTH_LAST_BATCH_STALE",
            "batch_age_seconds": age, "max_age_seconds": max_age_seconds,
        }
    uri = "file:" + quote(str(db.resolve()), safe="/") + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=3)
        try:
            row = connection.execute(
                "SELECT epoch,last_offset,status FROM ws_stream_state WHERE pair=?",
                ("btc_idr",),
            ).fetchone()
            number = connection.execute(
                "SELECT COUNT(*) FROM ws_trade_publications WHERE pair=?",
                ("btc_idr",),
            ).fetchone()[0]
        finally:
            connection.close()
    except (sqlite3.Error, OSError):
        return {**base, "reason": "WS_HEALTH_READONLY_DB_ERROR"}
    if (row is None or row[0] != stream_epoch or row[2] != "DISCONNECTED"
            or type(row[1]) is not int
            or offset is None or row[1] < offset
            or type(number) is not int or number < count):
        return {
            **base, "reason": "WS_HEALTH_JOURNAL_MISMATCH_OR_ROLLBACK",
            "batch_age_seconds": age,
        }
    return {
        **base,
        "status": "RECENT_COMPLETED_PUBLIC_BATCH_UNATTESTED",
        "reason": "LAST_BATCH_ONLY_NO_RUNTIME_LIVENESS_PROOF",
        "last_batch_recent_and_consistent": True,
        "batch_age_seconds": age,
        "max_age_seconds": max_age_seconds,
        "stream_epoch": stream_epoch,
        "last_publication_offset": row[1],
        "persisted_publications": number,
        "completed_safe_sessions": completed,
    }
