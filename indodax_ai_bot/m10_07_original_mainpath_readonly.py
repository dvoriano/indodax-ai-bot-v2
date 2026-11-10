"""M10-07 original V2 online mainpath, READ-ONLY runtime readiness join.

This is one evidence join, NOT a second scanner, Bot API poller, order
issuer, provider client, watchdog or M10-07 acceptance. Reuse the existing
M03 fixed V2 unit metadata, the original M05 owner outbox and M04 offline
provider configuration. No exchange/provider/Telegram HTTP or writes.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import stat
import time
from typing import Any

from .m10_v2_units_readonly import (
    inspect_named_v2_units, classify_m05_receiver_metadata,
)

ROLES = ("MARKET", "RISK", "STRATEGY")
_MAX_ADVISORY_AGE_SECONDS = 3600
_MAX_SCANNER_SAMPLE_AGE_SECONDS = 90


def _original_owner_outbox_readonly(path: Path, now_epoch: int) -> dict[str, Any]:
    result = {
        "trusted_db": False, "sent_owner_advisory_observed": False,
        "last_sent_closed_1m_epoch": None, "last_advisory_age_seconds": None,
    }
    try:
        p = Path(path)
        for d in (p.parent, p.parent.parent):
            st_dir = d.lstat()
            if (not stat.S_ISDIR(st_dir.st_mode)
                    or st_dir.st_uid != os.geteuid()
                    or st_dir.st_mode & 0o077):
                return result
        st = p.lstat()
        if (not stat.S_ISREG(st.st_mode)
                or st.st_uid != os.geteuid()
                or st.st_mode & 0o077
                or st.st_size == 0):
            return result
        # Only the already-running M05 owner outbox. URI mode=ro and SQLite
        # query_only prevent mutating its owner decisions or Telegram cursor.
        with sqlite3.connect(p.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            db.execute("PRAGMA query_only=ON")
            row = db.execute(
                "SELECT closed_1m_epoch FROM m05_market_advisory_outbox "
                "WHERE delivery_state='SENT' "
                "ORDER BY closed_1m_epoch DESC LIMIT 1"
            ).fetchone()
        result["trusted_db"] = True
        if (row is not None and type(row[0]) is int
                and 0 <= now_epoch - row[0] <= _MAX_ADVISORY_AGE_SECONDS):
            result["sent_owner_advisory_observed"] = True
            result["last_sent_closed_1m_epoch"] = row[0]
            result["last_advisory_age_seconds"] = now_epoch - row[0]
    except (OSError, sqlite3.Error, ValueError, OverflowError):
        pass
    return result


def _original_m03_sample_readonly(path: Path, now_epoch: int) -> dict[str, Any]:
    """Read only the original M03 ticker minute sample timestamp.

    A fresh row is an unverified market observation, not an authenticated
    market source, G1/AI admission or proof that systemd is healthy.
    Never read prices, tokens, accounts or unrelated project files.
    """
    result: dict[str, Any] = {
        "trusted_db": False,
        "recent_sample_observed": False,
        "last_sample_age_seconds": None,
    }
    try:
        p = Path(path)
        parent = p.parent.lstat()
        if (not stat.S_ISDIR(parent.st_mode)
                or stat.S_ISLNK(parent.st_mode)
                or parent.st_uid != os.geteuid()):
            return result
        file_stat = p.lstat()
        if (not stat.S_ISREG(file_stat.st_mode)
                or file_stat.st_uid != os.geteuid()
                or file_stat.st_mode & 0o077
                or file_stat.st_size == 0):
            return result
        with sqlite3.connect(p.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            db.execute("PRAGMA query_only=ON")
            row = db.execute(
                "SELECT MAX(observed_epoch) FROM public_ticker_minute_samples"
            ).fetchone()
        result["trusted_db"] = True
        observed_epoch = row[0] if row else None
        if type(observed_epoch) is int:
            age = now_epoch - observed_epoch
            if 0 <= age <= _MAX_SCANNER_SAMPLE_AGE_SECONDS:
                result["recent_sample_observed"] = True
                result["last_sample_age_seconds"] = age
            elif age > _MAX_SCANNER_SAMPLE_AGE_SECONDS:
                result["last_sample_age_seconds"] = age
    except (OSError, sqlite3.Error, ValueError, OverflowError):
        pass
    return result


def inspect_m10_07_original_mainpath_readonly(
    *, units_report: dict | None = None,
    provider_report: dict | None = None,
    owner_db: Path | None = None,
    m03_db: Path | None = None,
    now_epoch: int | None = None,
) -> dict[str, Any]:
    """Single original V2 M03→M04→M05 runtime evidence join.

    M03 timer metadata + recent SENT owner outbox are only observations,
    NOT signed source evidence, G1 provenance, delivery confirmation, paid
    three-role responses or permission to execute an exchange order.
    """
    now = int(time.time()) if now_epoch is None else now_epoch
    if type(now) is not int or now <= 0:
        raise ValueError("M10_07_INVALID_CLOCK")
    units = inspect_named_v2_units() if units_report is None else units_report
    if not isinstance(units, dict):
        units = {}
    receiver = classify_m05_receiver_metadata(units)
    rows = units.get("units")
    m03 = next((r for r in rows
                if isinstance(r, dict) and r.get("role") == "m03_scanner_timer"),
               None) if isinstance(rows, list) else None
    m03_active = bool(
        m03 is not None
        and m03.get("observed_load") == "loaded"
        and m03.get("observed_active") == "active"
        and m03.get("observed_enabled") in ("enabled", "enabled-runtime")
        and m03.get("v2_user_fragment_path_matches") is True
        and m03.get("unit_needs_daemon_reload") == "no"
    )
    service = next((r for r in rows
                    if isinstance(r, dict)
                    and r.get("role") == "m03_scanner"),
                   None) if isinstance(rows, list) else None
    # A oneshot unit may be INACTIVE between healthy ticks. FAILED is an
    # explicit veto; missing/unknown metadata cannot prove it healthy.
    m03_service_not_failed = bool(
        service is not None
        and service.get("observed_load") == "loaded"
        and service.get("observed_active") in ("inactive", "active")
        and service.get("v2_user_fragment_path_matches") is True
        and service.get("unit_needs_daemon_reload") == "no"
    )
    sample = _original_m03_sample_readonly(
        m03_db if m03_db is not None else (
            Path.home() / "indodax-ai-bot-v2" / "runtime" / "decisions.sqlite3"
        ),
        now,
    )
    db = owner_db if owner_db is not None else (
        Path.home() / ".local" / "state" / "indodax-ai-bot-v2"
        / "m05-telegram" / "owner.sqlite3"
    )
    outbox = _original_owner_outbox_readonly(Path(db), now)
    provider = provider_report if isinstance(provider_report, dict) else {}
    models = provider.get("configured_models_by_role")
    models = models if isinstance(models, dict) else {}
    configured_roles = {
        role: isinstance(models.get(role), str) and bool(models[role])
        for role in ROLES
    }
    local_m04_configuration = (
        all(configured_roles.values())
        and provider.get("sumopod_key_present_nonempty") is True
    )
    sent = outbox["sent_owner_advisory_observed"]
    return {
        "status": "M10_07_ORIGINAL_V2_MAINPATH_READONLY_PREFLIGHT",
        "scope": "ONE_ORIGINAL_V2_M03_M04_M05_NONLIVE",
        "m03_original_timer_active_metadata_observed": m03_active,
        "m03_original_service_not_failed_metadata_observed": m03_service_not_failed,
        "m03_original_market_db_trusted": sample["trusted_db"],
        "m03_original_market_sample_recent_observed": sample[
            "recent_sample_observed"
        ],
        "m03_original_last_sample_age_seconds": sample[
            "last_sample_age_seconds"
        ],
        "m05_original_receiver_active_metadata_observed": (
            receiver["m05_receiver_metadata_active"]
        ),
        "m05_receiver_profile": receiver["m05_receiver_metadata_mode"],
        "m05_original_owner_db_trusted": outbox["trusted_db"],
        "m03_m05_recent_sent_public_advisory_observed": bool(sent),
        "m03_m05_source_runtime_metadata_join_observed": bool(
            sent and m03_active and m03_service_not_failed
            and sample["recent_sample_observed"]
            and receiver["m05_receiver_metadata_active"]
        ),
        "last_sent_public_advisory_age_seconds": (
            outbox["last_advisory_age_seconds"]
        ),
        "m04_configured_roles_present": configured_roles,
        "m04_local_models_and_credential_present": local_m04_configuration,
        "m04_provider_configuration_only": True,
        "m03_source_freshness_is_independent_provenance": False,
        "m03_sample_only_does_not_prove_rest_or_ws_authorized": True,
        "g1_independent_trade_tape_verified": False,
        "g1_independent_issuer_verified": False,
        "three_paid_ai_role_responses_verified": False,
        "owner_order_specific_final_verified": False,
        "runtime_process_source_sha_verified": False,
        "m10_07_one_pinned_online_release_verified": False,
        "m10_07_full_acceptance": False,
        "billable_provider_calls": 0,
        "exchange_api_calls": 0,
        "telegram_api_calls": 0,
        "service_modified": False,
        "final_decision": None,
        "execution_allowed": False,
        "live_trading_authorized": False,
    }
