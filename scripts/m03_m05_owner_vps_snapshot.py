"""One-shot ORIGINAL V2 M03/M05 VPS evidence. NEVER starts services or sends network.

Only read fixed V2 service metadata, local original BTC WS sidecar, scoped
owner settings file METADATA (never token contents), source SHA and local
read-only market advisory count. Owner decides on any future systemd action.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import time
from typing import Callable

from indodax_ai_bot.m03_egress_admission import (
    M03SharedEgressAdmission, M03EgressAdmissionError,
)
from indodax_ai_bot.m10_v2_units_readonly import (
    inspect_named_v2_units, classify_m05_receiver_metadata,
)
from indodax_ai_bot.m10_ops_health_readonly import (
    _read_v2_ws_supervisor_status, _batch_observation,
)

_HEX = re.compile(r"[a-f0-9]{40}\Z")
_CONFIG = frozenset((
    "M05_TELEGRAM_OWNER_DB", "M05_TELEGRAM_OWNER_ID",
    "M05_TELEGRAM_TOKEN_FILE",
))
_EXPECTED = (
    "INDODAX_TRADING_ENABLED=NO",
    "INDODAX_ALLOW_LIVE_EXECUTION=NO",
    "INDODAX_EXECUTION_MODE=DRY_RUN",
)


def _safe_private_file(path: Path, *, max_bytes: int) -> bool:
    """Metadata only; refuse symlinks and files owned by another user."""
    try:
        st = path.lstat()
        parent = path.parent.lstat()
        return (path.is_absolute()
                and stat.S_ISREG(st.st_mode) and not stat.S_ISLNK(st.st_mode)
                and st.st_uid == os.geteuid() and st.st_nlink == 1
                and stat.S_IMODE(st.st_mode) == 0o600
                and 0 < st.st_size <= max_bytes
                and stat.S_ISDIR(parent.st_mode)
                and not stat.S_ISLNK(parent.st_mode)
                and parent.st_uid == os.geteuid()
                and stat.S_IMODE(parent.st_mode) == 0o700)
    except OSError:
        return False


def _owner_env_metadata(path: Path, home: Path) -> dict:
    """Read ONLY explicitly allowlisted path/owner ID values, never token."""
    result = {
        "owner_service_env_permissions_ok": False,
        "owner_db_permissions_ok": False,
        "telegram_token_file_permissions_ok": False,
        "owner_ledger_source_identity_verified": False,
        "m05_queued_advisories": None,
        "m05_sent_advisories": None,
        "m05_real_market_sent_with_message_id": None,
        "m05_real_market_latest_closed_epoch": None,
        "m05_real_market_db_ack_status": "NOT_VERIFIED",
        "m05_pending_manual_previews": None,
        "m05_manual_preview_read_status": "NOT_VERIFIED",
        "m05_readonly_inspection_status": "NOT_AVAILABLE",
    }
    if not _safe_private_file(path, max_bytes=8192):
        return result
    result["owner_service_env_permissions_ok"] = True
    try:
        # Owner config may contain other sensitive assignments. Parse nothing
        # except exact allowlisted key names; NEVER include raw values in result.
        raw = path.read_text("utf-8")
        lines = {}
        for line in raw.splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k in _CONFIG:
                if k in lines or not v or len(v) > 1024:
                    return result
                lines[k] = v.strip().strip('"').strip("'")
        if set(lines) != _CONFIG:
            return result
        uid = lines["M05_TELEGRAM_OWNER_ID"]
        if not uid.isascii() or not uid.isdecimal() or not 0 < int(uid) < 10**19:
            return result
        db, token = Path(lines["M05_TELEGRAM_OWNER_DB"]), Path(
            lines["M05_TELEGRAM_TOKEN_FILE"])
        # Paths can differ by operator, but cannot escape user home,
        # touch V1, or be followed through symlinks.
        for p in (db, token):
            if (not p.is_absolute()
                    or not p.is_relative_to(home)
                    or p == db and p == token):
                return result
        db_ok = _safe_private_file(db, max_bytes=100_000_000)
        token_ok = _safe_private_file(token, max_bytes=256)
        result["owner_db_permissions_ok"] = db_ok
        result["telegram_token_file_permissions_ok"] = token_ok
        if not db_ok:
            return result
        # immutable=1 avoids creating .sqlite3-shm; if an existing WAL is
        # present, decline to report counts rather than give a stale count.
        if (Path(str(db) + "-wal").exists()
                or Path(str(db) + "-shm").exists()):
            result["m05_readonly_inspection_status"] = "WAL_PRESENT_UNVERIFIED"
            return result
        con = sqlite3.connect(
            "file:" + str(db) + "?mode=ro&immutable=1",
            uri=True, timeout=2,
        )
        try:
            con.execute("PRAGMA query_only=ON")
            owner = con.execute(
                "SELECT owner_user_id,mode FROM m05_config "
                "WHERE singleton=1"
            ).fetchone()
            if owner != (int(uid), "MANUAL"):
                result["m05_readonly_inspection_status"] = "OWNER_OR_MODE_UNVERIFIED"
                return result
            result["owner_ledger_source_identity_verified"] = True
            result["m05_queued_advisories"] = con.execute(
                "SELECT COUNT(*) FROM m05_market_advisory_outbox "
                "WHERE delivery_state='QUEUED'"
            ).fetchone()[0]
            result["m05_sent_advisories"] = con.execute(
                "SELECT COUNT(*) FROM m05_market_advisory_outbox "
                "WHERE delivery_state='SENT'"
            ).fetchone()[0]
            # Distinguish real market advisory DB ACKs from synthetic M05
            # /button-test messages. No actual Telegram recipient proof here.
            # A legacy owner DB may lack optional preview table; that must
            # not turn already-read market counts into blanket UNVERIFIED.
            try:
                real = con.execute(
                    "SELECT COUNT(*),MAX(closed_1m_epoch) "
                    "FROM m05_market_advisory_outbox "
                    "WHERE delivery_state='SENT' "
                    "AND pair!='reviewtest_idr' "
                    "AND sent_message_id IS NOT NULL "
                    "AND sent_message_id>0"
                ).fetchone()
                if (real is not None and type(real[0]) is int
                        and real[0] >= 0
                        and (real[1] is None or
                             (type(real[1]) is int and real[1] > 0))):
                    result["m05_real_market_sent_with_message_id"] = real[0]
                    result["m05_real_market_latest_closed_epoch"] = real[1]
                    result["m05_real_market_db_ack_status"] = (
                        "DB_ACK_ONLY_TELEGRAM_RECEIPT_NOT_VERIFIED"
                    )
            except sqlite3.Error:
                result["m05_real_market_db_ack_status"] = (
                    "SCHEMA_NOT_VERIFIED"
                )
            try:
                result["m05_pending_manual_previews"] = con.execute(
                    "SELECT COUNT(*) FROM m05_manual_trade_previews "
                    "WHERE owner_choice IS NULL "
                    "AND send_state IN ('QUEUED','CLAIMED','SENT')"
                ).fetchone()[0]
                result["m05_manual_preview_read_status"] = "COUNTS_ONLY"
            except sqlite3.Error:
                result["m05_manual_preview_read_status"] = (
                    "MANUAL_PREVIEW_SCHEMA_UNVERIFIED"
                )
            result["m05_readonly_inspection_status"] = (
                "MARKET_COUNTS_ONLY_MANUAL_PREVIEW_UNVERIFIED"
                if result["m05_manual_preview_read_status"] != "COUNTS_ONLY"
                else "COUNTS_ONLY"
            )
        finally:
            con.close()
    except (OSError, ValueError, UnicodeError, sqlite3.Error):
        result["m05_readonly_inspection_status"] = "UNVERIFIED"
    return result


def _git_sha(root: Path) -> str | None:
    try:
        p = subprocess.run(
            ["/usr/bin/git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            check=False,
        )
        value = p.stdout.strip()
        if p.returncode == 0 and _HEX.fullmatch(value):
            return value
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


def collect_original_v2_runtime_snapshot(
        *, root: Path | None = None, home: Path | None = None,
        clock: Callable[[], float] | None = None,
        unit_reader: Callable[[str], str | None] | None = None,
        source_sha_reader: Callable[[Path], str | None] | None = None,
        egress_validator: Callable[[Path], int] | None = None) -> dict:
    """Read existing original V2 only, without HTTP, private keys or writes.

    Runtime source SHA and systemd load metadata are observations, NOT proof
    that an active process executes that SHA or 24/7 reliability.
    """
    owner_home = Path.home() if home is None else Path(home)
    original = owner_home / "indodax-ai-bot-v2"
    target = original if root is None else Path(root)
    current = int(time.time() if clock is None else clock())
    if (type(current) is not int or current <= 0
            or target != original or target.is_symlink()
            or not target.is_dir()
            or not (target / "indodax_ai_bot" / "__main__.py").is_file()):
        raise ValueError("M03_M05_ONLY_CANONICAL_ORIGINAL_V2")
    sha = (source_sha_reader or _git_sha)(target)
    if sha is not None and not _HEX.fullmatch(sha):
        sha = None

    units = inspect_named_v2_units(reader=unit_reader, home=owner_home)
    by_role = {r["role"]: r for r in units["units"]}
    m03 = by_role["m03_scanner_timer"]
    m05 = by_role["m05_telegram_timer"]
    receiver = classify_m05_receiver_metadata(units)
    ws = target / "runtime" / "public_ws_btc.sqlite3"
    try:
        ws_st = ws.lstat()
        ws_present = stat.S_ISREG(ws_st.st_mode) and ws_st.st_size > 0
        ws_file_age = max(0, current - int(ws_st.st_mtime)) if ws_present else None
    except OSError:
        ws_present, ws_file_age = False, None
    status = _read_v2_ws_supervisor_status(
        target / "runtime" / "public_ws_btc.status.json")
    batch, batch_alerts = _batch_observation(status, current)

    policy = (owner_home / ".config" / "indodax-ai-bot-v2"
              / "m03-egress-admission.json")
    egress = {"status": "NOT_VERIFIED",
              "max_local_requests_rolling_60s_declared": None,
              "all_egress_peers_independently_measured": False}
    try:
        allowance = ((egress_validator or (
            lambda p: M03SharedEgressAdmission(p, clock=lambda: current
                                              ).allowed_local_limit()
        ))(policy))
        if type(allowance) is int and 0 < allowance <= 168:
            egress["status"] = "OWNER_POLICY_CURRENT_DECLARED_ONLY"
            egress["max_local_requests_rolling_60s_declared"] = allowance
    except (M03EgressAdmissionError, OSError, ValueError):
        egress["status"] = "M03_EGRESS_POLICY_UNVERIFIED_OR_EXPIRED"

    source = (target / "deploy" / "indodax-v2-m03-mainpath.service")
    dropin = target / "deploy" / "indodax-v2-m03-owner-advisory.conf"
    def _template(path: Path) -> bool:
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 8192:
                return False
            raw = path.read_text("utf-8")
            return (all(f"Environment={s}" in raw for s in _EXPECTED)
                    and "%h/indodax-ai-bot-v2/" in raw
                    and "indodax-ai-bot-v2-m03-runtime" not in raw
                    and "BUY" not in raw.split("ExecStart=", 1)[-1].split("\n")[0])
        except (OSError, UnicodeError):
            return False
    source_consistent = _template(source) and _template(dropin)
    owner = _owner_env_metadata(
        owner_home / ".config" / "indodax-ai-bot-v2" / "m05" / "service.env",
        owner_home)
    # This is a diagnostic only; no claim that scanner/AI is currently ON.
    scanner_metadata_active = (
        m03["observed_load"] == "loaded"
        and m03["observed_active"] == "active"
        and m03["observed_enabled"] in ("enabled", "enabled-runtime")
        and m03["v2_user_fragment_path_matches"] is True
        and m03["unit_needs_daemon_reload"] == "no"
    )
    telegram_timer_metadata_active = receiver["m05_timer_active_enabled_metadata_only"]
    telegram_metadata_active = receiver["m05_receiver_metadata_active"]
    # Deterministic blockers are observations, never authorization to start
    # a scanner, enable trading, or bypass independent source admission.
    blockers = []
    if not source_consistent:
        blockers.append("M03_NONLIVE_SOURCE_TEMPLATE_UNVERIFIED")
    if not ws_present:
        blockers.append("BTC_WS_DATABASE_MISSING")
    if batch_alerts:
        blockers.append("BTC_WS_BATCH_NOT_FRESH_AND_CLEAN")
    if ws_file_age is None or ws_file_age > 3600:
        blockers.append("BTC_WS_FILE_NOT_RECENT")
    if egress["status"] != "OWNER_POLICY_CURRENT_DECLARED_ONLY":
        blockers.append("M03_EGRESS_POLICY_UNVERIFIED")
    if not telegram_metadata_active:
        blockers.append("M05_TELEGRAM_TIMER_NOT_VERIFIED_ACTIVE")
    if not owner["owner_service_env_permissions_ok"]:
        blockers.append("M05_OWNER_ENV_PERMISSIONS_UNVERIFIED")
    if not owner["owner_db_permissions_ok"]:
        blockers.append("M05_OWNER_DB_PERMISSIONS_UNVERIFIED")
    if not owner["telegram_token_file_permissions_ok"]:
        blockers.append("M05_TOKEN_FILE_PERMISSIONS_UNVERIFIED")
    if not owner["owner_ledger_source_identity_verified"]:
        blockers.append("M05_OWNER_LEDGER_IDENTITY_UNVERIFIED")
    if sha is None:
        blockers.append("V2_SOURCE_HEAD_UNVERIFIED")
    return {
        "scope": "SINGLE_CANONICAL_V2_OWNER_VPS_READONLY_SNAPSHOT",
        "status": "OBSERVED_NOT_DEPLOYED_NOT_GO",
        "nonlive_canary_blocker_codes": blockers,
        "observed_epoch": current,
        "original_v2_git_head_sha": sha,
        "source_sha_is_running_process_attestation": False,
        "units": units,
        "m03_scanner_timer_active_metadata_only": scanner_metadata_active,
        "m05_telegram_timer_active_metadata_only": telegram_timer_metadata_active,
        "m05_receiver": receiver,
        "btc_ws_database_file_present": ws_present,
        "btc_ws_database_file_age_seconds_metadata_only": ws_file_age,
        "btc_ws_last_batch": batch,
        "btc_ws_alert_codes": batch_alerts,
        "m03_advisory_source_templates_nonlive": source_consistent,
        "m03_egress": egress,
        "m05_owner": owner,
        "g1_independent_issuer_verified": False,
        "three_ai_paid_model_outputs_observed": False,
        "scanner_recommendations_actually_delivered_verified": False,
        "trading_flags_source_templates": "NO_NO_DRY_RUN"
            if source_consistent else "UNVERIFIED",
        "trading_flags_running_process_verified": False,
        "m03_timer_started_or_modified": False,
        "m05_timer_started_or_modified": False,
        "exchange_gets_or_orders_sent": 0,
        "telegram_api_calls_sent": 0,
        "auto_execution_enabled": False,
        "manual_order_sent": False,
        "live_trading_authorized": False,
        "ready_for_nonlive_scanner_canary": (
            source_consistent and ws_present and sha is not None
            and telegram_metadata_active and not batch_alerts
            and ws_file_age is not None and ws_file_age <= 3600
            and egress["status"] == "OWNER_POLICY_CURRENT_DECLARED_ONLY"
            and owner["owner_service_env_permissions_ok"]
            and owner["owner_db_permissions_ok"]
            and owner["telegram_token_file_permissions_ok"]
            and owner["owner_ledger_source_identity_verified"]
        ),
        "scanner_canary_start_authorized": False,
    }


def main() -> int:
    try:
        report = collect_original_v2_runtime_snapshot()
    except (OSError, ValueError):
        print(json.dumps({
            "status": "M03_M05_ORIGINAL_V2_SNAPSHOT_UNAVAILABLE",
            "live_trading_authorized": False,
        }, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
