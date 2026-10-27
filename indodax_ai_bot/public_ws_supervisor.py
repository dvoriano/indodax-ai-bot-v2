"""M04-J: supervised PUBLIC-only BTC WebSocket service slice.

One finite, lock-protected collector batch. A separately staged user-systemd
service may restart SUCCESSFUL batches; any failure exits nonzero and stops
until an operator reviews it. Never signs a G1 receipt or makes paid/API trades.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import time
from typing import Any, Iterator

from .trade_stream import (
    PublicTradeStreamJournal,
    StreamEvidenceError,
    collect_official_public_ws,
)


class PublicWSSupervisorError(ValueError):
    pass


@contextmanager
def exclusive_collector_lock(database: str | Path) -> Iterator[None]:
    """Nonblocking OS-held single-writer guard, separate from trade journal."""
    lockfile = Path(str(database) + ".collector.lock")
    lockfile.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lockfile), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PublicWSSupervisorError("PUBLIC_WS_SUPERVISOR_ALREADY_RUNNING") from None
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def supervised_public_ws_batch(
    database: str | Path, *, pair: str = "btc_idr",
    sessions: int = 10, seconds: int = 30, pause_seconds: int = 1,
    status_file: str | Path | None = None,
    collector=None, now=None,
) -> dict[str, Any]:
    """One operator-controlled batch, not infinite looping.

    Sidecar status is written ONLY at completion, with an atomic replace.
    It reports batch state, not live stream continuity between batches.
    On any interruption, caller must return a nonzero code so a staged unit
    configured with Restart=on-success will NOT restart a failed collector.
    """
    # Validate bounds before acquiring lock or creating any SQLite file.
    if (type(sessions) is not int or not 2 <= sessions <= 10
            or type(seconds) is not int or not 3 <= seconds <= 30
            or type(pause_seconds) is not int or not 0 <= pause_seconds <= 10):
        raise PublicWSSupervisorError("INVALID_PUBLIC_WS_SUPERVISOR_BOUNDS")
    if pair != "btc_idr":
        raise PublicWSSupervisorError("M04J_BTC_ONLY_UNTIL_STREAM_SCOPES_QUALIFIED")
    database = Path(database)
    if status_file is not None and Path(status_file) == database:
        raise PublicWSSupervisorError("PUBLIC_WS_STATUS_CANNOT_OVERWRITE_DATABASE")
    run = collector if collector is not None else collect_official_public_ws
    clock = now if now is not None else time.time
    with exclusive_collector_lock(database):
        journal = PublicTradeStreamJournal(database)
        try:
            data = run(journal, pair=pair, sessions=sessions,
                       session_seconds=seconds, pause_seconds=pause_seconds)
        except StreamEvidenceError:
            data = {"status": "REJECT", "reason": "PUBLIC_WS_SESSION_EXCEPTION"}
        except Exception:
            data = {"status": "REJECT", "reason": "PUBLIC_WS_SESSION_UNAVAILABLE"}
        public_state = journal.status(pair)
        safe = (
            public_state["stream_status"] == "DISCONNECTED"
            and isinstance(data, dict)
            and data.get("status") == "PUBLIC_SAMPLE_COLLECTED_UNATTESTED"
            and data.get("requested_sessions") == sessions
            and data.get("completed_safe_sessions") == sessions
            and data.get("attempted_sessions") == sessions
            and data.get("g1_receipt_issuance_allowed") is False
            and data.get("g2_provider_call_allowed") is False
            and data.get("execution_allowed") is False
        )
        result: dict[str, Any] = {
            "stage": "M04J_PUBLIC_WS_SUPERVISION_SLICE",
            "status": "PUBLIC_COLLECTION_BATCH_OK_UNATTESTED" if safe else "REJECT",
            "reason": "PUBLIC_WS_DATA_UNATTESTED" if safe
                      else "PUBLIC_WS_BATCH_INTERRUPTED_OR_UNVERIFIED",
            "pair": pair,
            "last_completed_batch_epoch": int(clock()),
            "requested_sessions": sessions,
            "completed_safe_sessions": data.get("completed_safe_sessions", 0)
                                       if isinstance(data, dict) else 0,
            "stream_epoch": public_state["stream_epoch"],
            "last_publication_offset": public_state["last_publication_offset"],
            "persisted_publications": public_state["persisted_publications"],
            "stream_status": public_state["stream_status"],
            "g1_receipt_issuance_allowed": False,
            "g2_provider_call_allowed": False,
            "execution_allowed": False,
            "indodax_trade_tape_completeness_proven": False,
            "source_authenticated_for_g1": False,
            "restart_only_after_clean_batch": safe,
            "systemd_service_active": None,  # Deployment status cannot be inferred from code.
        }
        if status_file is not None:
            path = Path(status_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
            try:
                with open(tmp, "x", encoding="utf-8") as fh:
                    os.chmod(tmp, 0o600)
                    json.dump(result, fh, sort_keys=True)
                    fh.write("\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, path)
            finally:
                if tmp.exists():
                    tmp.unlink()
        return result
