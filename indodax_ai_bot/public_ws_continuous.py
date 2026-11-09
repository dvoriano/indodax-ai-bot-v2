"""M04-W original V2 BTC PUBLIC persistent socket: 4 x 30s observation windows.

One original lock, one authenticated WebSocket across arbitrarily many
120-second reports. Only typed transport losses reconnect without a total
attempt limit. No alternate public writer, source promotion, paid AI, orders,
or reconstruction of unobserved trades. Historical finite probes are unchanged.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any

from .btc_ops_gate import AdmissionDenied, ROOT, SCOPE, STANDING_MODE, _private_json
from .public_ws_supervisor import (
    PublicWSSupervisorError, _public_ws_failure_code, exclusive_collector_lock,
)
from .trade_stream import (
    PublicTradeStreamJournal, StreamEvidenceError,
    _IMMEDIATE_TRANSIENT_WS_RECONNECT, probe_official_public_ws,
)

WINDOWS_PER_REPORT = 2
SECONDS_PER_WINDOW = 30
_GO = ROOT / "btc_public_ops.go.json"
_HOLD = ROOT / "btc_public_ops.hold.json"


class _OfflineStopAfterReports(Exception):
    """Only used by injected test code; not reachable from the production CLI."""


def _standing_gap_budget(
    now: int, journal_gap: int, go: Path, hold: Path = _HOLD,
) -> None:
    """Check owner PUBLIC standing scope, manual HOLD and gap budget."""
    if hold.exists() or hold.is_symlink():
        raise StreamEvidenceError("PUBLIC_WS_OPERATOR_HOLD")
    try:
        value, _ = _private_json(go, 2048)
    except AdmissionDenied:
        raise StreamEvidenceError("PUBLIC_WS_STANDING_GO_UNAVAILABLE") from None
    if (set(value) != {"scope", "mode", "issued_at_epoch", "max_gap_count"}
            or value.get("scope") != SCOPE
            or value.get("mode") != STANDING_MODE
            or type(value.get("issued_at_epoch")) is not int
            or not 0 < value["issued_at_epoch"] <= now
            or type(value.get("max_gap_count")) is not int
            or not 0 <= journal_gap <= value["max_gap_count"] <= 100000):
        raise StreamEvidenceError("PUBLIC_WS_STANDING_GO_OR_GAP_INVALID")


def _atomic_sidecar(path: Path, doc: dict[str, Any]) -> None:
    """Same bounded, owner-only atomic last-window contract as original V2."""
    if path.is_symlink() or path.parent.is_symlink():
        raise StreamEvidenceError("PUBLIC_WS_STATUS_SYMLINK_DENIED")
    raw = (json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(raw) > 32768:
        raise StreamEvidenceError("PUBLIC_WS_STATUS_OVERSIZED")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
    try:
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                     | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _checkpoint(
    journal: PublicTradeStreamJournal, *, now: int, status: str,
    completed: int, attempted: int, reason: str | None,
    reconnects: int,
) -> dict[str, Any]:
    state = journal.status("btc_idr")
    clean = status == "PUBLIC_COLLECTION_WINDOW_OK_UNATTESTED"
    return {
        "stage": "M04W_PERSISTENT_PUBLIC_WS_TWO_WINDOWS",
        "status": status,
        "reason": "PUBLIC_WS_WINDOW_DATA_UNATTESTED" if clean
                  else "PUBLIC_WS_WINDOW_INTERRUPTED_OR_UNVERIFIED",
        "pair": "btc_idr",
        "last_completed_batch_epoch": now,
        "requested_sessions": WINDOWS_PER_REPORT,
        "attempted_sessions": attempted,
        "completed_safe_sessions": completed,
        "failed_session_number": attempted if not clean and attempted > completed else None,
        "collector_failure_code": None if clean else reason,
        "stream_epoch": state["stream_epoch"],
        "last_publication_offset": state["last_publication_offset"],
        "persisted_publications": state["persisted_publications"],
        "stream_status": state["stream_status"],
        "unrecoverable_gap_rebaseline_count": state["unrecoverable_gap_rebaseline_count"],
        "persistent_transient_reconnect_attempts": reconnects,
        "reconnect_until_recovered": True,
        "window_seconds": SECONDS_PER_WINDOW,
        "checkpoint_is_exchange_closed_1m_candle": False,
        "calendar_minute_alignment_proven": False,
        "persistent_socket_observed_within_windows": clean,
        "full_stream_continuity_proven": False,
        "indodax_trade_tape_completeness_proven": False,
        "source_authenticated_for_g1": False,
        "g1_receipt_issuance_allowed": False,
        "g2_provider_call_allowed": False,
        "execution_allowed": False,
        # The daemon never exits successfully after each window. Restart
        # policy applies only to the actual original service process exit.
        "restart_only_after_clean_batch": False,
        "systemd_service_active": None,
    }


def supervised_public_ws_continuous(
    database: str | Path, *, pair: str = "btc_idr",
    sessions: int = WINDOWS_PER_REPORT,
    seconds: int = SECONDS_PER_WINDOW, pause_seconds: int = 0,
    status_file: str | Path,
    probe=None, sleep=None, clock=None, go: Path = _GO,
    hold: Path = _HOLD,
    _test_stop_after_reports: int | None = None,
) -> dict[str, Any]:
    """Original one-writer persistent WS process. Never loops on auth/rate denial.

    A genuine network reconnect starts a NEW socket and durable offset resume;
    an evaluation window never asks the source to disconnect. A partially
    observed 4-window report is REJECT, not a fabricated four-of-four.
    """
    if (pair != "btc_idr" or type(sessions) is not int
            or sessions != WINDOWS_PER_REPORT
            or type(seconds) is not int or seconds != SECONDS_PER_WINDOW
            or type(pause_seconds) is not int or pause_seconds != 0
            or not status_file or Path(status_file) == Path(database)
            or (_test_stop_after_reports is not None and (
                type(_test_stop_after_reports) is not int
                or not 1 <= _test_stop_after_reports <= 10))):
        raise PublicWSSupervisorError("INVALID_PUBLIC_WS_CONTINUOUS_BOUNDS")
    stream_probe = probe if probe is not None else probe_official_public_ws
    wait = sleep if sleep is not None else time.sleep
    now = clock if clock is not None else time.time
    status_path = Path(status_file)
    with exclusive_collector_lock(database):
        journal = PublicTradeStreamJournal(database)
        # The original systemd ExecCondition already enforces admission;
        # also deny a direct CLI launch before touching the public network.
        _standing_gap_budget(
            int(now()), journal.status(pair)["unrecoverable_gap_rebaseline_count"], go,
            hold=hold,
        )
        windows: list[dict[str, Any]] = []
        reconnects = 0
        consecutive_failures = 0
        completed_reports = 0
        last_report: dict[str, Any] | None = None
        gap_retry = 0

        def publish(*, status: str, reason: str | None = None) -> dict[str, Any]:
            stamp = int(now())
            state = journal.status(pair)
            _standing_gap_budget(
                stamp, state["unrecoverable_gap_rebaseline_count"], go, hold=hold
            )
            completed = len(windows)
            attempted = WINDOWS_PER_REPORT if status == "PUBLIC_COLLECTION_WINDOW_OK_UNATTESTED" else min(
                WINDOWS_PER_REPORT, completed + 1)
            record = _checkpoint(
                journal, now=stamp, status=status,
                completed=completed, attempted=attempted,
                reason=reason, reconnects=reconnects,
            )
            _atomic_sidecar(status_path, record)
            return record

        def on_window(sample: dict[str, Any]) -> None:
            nonlocal consecutive_failures, completed_reports, gap_retry, last_report
            if (not isinstance(sample, dict)
                    or sample.get("subscription_acknowledged") is not True
                    or sample.get("protocol_heartbeat_confirmed") is not True
                    or sample.get("stream_status_during_probe") not in (
                        "BASELINE_ONLY", "CONTIGUOUS_OBSERVED")
                    or sample.get("g1_receipt_issuance_allowed") is not False
                    or sample.get("g2_provider_call_allowed") is not False
                    or sample.get("execution_allowed") is not False
                    or type(sample.get("live_publications_newly_persisted")) is not int
                    or sample["live_publications_newly_persisted"] < 0):
                raise StreamEvidenceError("PUBLIC_WS_WINDOW_PROOF_INVALID")
            # Manual HOLD, owner revocation and exceeded gap budget must
            # halt an already-running persistent socket within one window,
            # not merely prevent its next systemd start.
            _standing_gap_budget(
                int(now()), sample.get("unrecoverable_gap_rebaseline_count")
                if type(sample.get("unrecoverable_gap_rebaseline_count")) is int
                else journal.status(pair)["unrecoverable_gap_rebaseline_count"],
                go, hold=hold,
            )
            windows.append(sample)
            consecutive_failures = 0
            gap_retry = 0
            if len(windows) == WINDOWS_PER_REPORT:
                last_report = publish(status="PUBLIC_COLLECTION_WINDOW_OK_UNATTESTED")
                completed_reports += 1
                windows.clear()
                # A short sanitized operational heartbeat, not source trust.
                print(json.dumps({
                    "stage": "M04W_TWO_30S_WINDOWS",
                    "completed_safe_sessions": WINDOWS_PER_REPORT,
                    "status": last_report["status"],
                    "g1_receipt_issuance_allowed": False,
                    "execution_allowed": False,
                }, sort_keys=True), flush=True)
                if (_test_stop_after_reports is not None
                        and completed_reports >= _test_stop_after_reports):
                    raise _OfflineStopAfterReports()

        while True:
            try:
                # windows=None means the SAME authenticated WS socket stays
                # open across 4-window report boundaries for as long as alive.
                stream_probe(
                    journal, pair=pair, seconds=seconds,
                    windows=None, on_window=on_window,
                )
                raise StreamEvidenceError("PUBLIC_WS_UNEXPECTED_SOCKET_EXIT")
            except _OfflineStopAfterReports:
                return last_report or {"status": "REJECT"}
            except StreamEvidenceError as exc:
                cause = str(exc)
                code = _public_ws_failure_code("WS_SESSION_REJECTED:" + cause)
                if cause in _IMMEDIATE_TRANSIENT_WS_RECONNECT:
                    reconnects += 1
                    try:
                        publish(status="REJECT", reason=code)
                    except StreamEvidenceError:
                        # Owner GO/gap denial always wins over reconnect.
                        raise
                    windows.clear()
                    if consecutive_failures:
                        wait(min(30, 2 ** min(consecutive_failures - 1, 5)))
                    consecutive_failures += 1
                    continue
                # One fresh public baseline after an actual persisted GAP;
                # no gap is erased and no repeated gap-retry loop is allowed.
                if (cause in ("WS_RECOVERY_GAP_UNRESOLVED",
                              "WS_STREAM_GAP_OR_EPOCH_CHANGED")
                        and gap_retry == 0
                        and journal.status(pair)["stream_status"] == "GAP"):
                    gap_retry = 1
                    publish(status="REJECT", reason=code)
                    windows.clear()
                    continue
                publish(status="REJECT", reason=code)
                raise
            except Exception:
                publish(status="REJECT", reason="PUBLIC_WS_SESSION_UNAVAILABLE")
                raise StreamEvidenceError("PUBLIC_WS_SESSION_UNAVAILABLE") from None
