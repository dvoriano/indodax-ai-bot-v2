"""Owner-only one-shot PUBLIC WATCH paid AI entrypoint, OFF by default.

A plain invocation cannot spend tokens. No environment/Telegram message can
silently grant paid GO. The person operating original V2 must give BOTH a
typed explicit confirmation and operator-chosen daily/per-call USD micro-unit
caps after reviewing provider prices. No background timer or scanner changes.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat

from indodax_ai_bot.ai_budget import AiBudgetError, PersistentAiBudget
from indodax_ai_bot.m04_owner_config import (
    CONFIG_DIR, M04OwnerConfigDenied, owner_provider_source,
)
from indodax_ai_bot.m04_public_watch_paid_once import (
    OWNER_PAID_ONCE_CONFIRMATION, run_public_watch_paid_once,
)
from indodax_ai_bot.m04_radar_three_role_handoff_readonly import (
    inspect_original_radar_three_role_handoff_readonly,
)

BUDGET_DB = CONFIG_DIR / "public-watch-paid-budget.sqlite3"


def _private_budget_file_ok(path: Path) -> bool:
    try:
        st = path.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return (
        stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid()
        and stat.S_IMODE(st.st_mode) == 0o600 and st.st_nlink == 1
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Original V2 paid one-shot PUBLIC WATCH; default is OFF"
    )
    parser.add_argument("--execute-paid-once", action="store_true")
    parser.add_argument("--owner-confirm")
    parser.add_argument("--owner-pricing-reviewed", action="store_true")
    parser.add_argument("--daily-limit-micro-usd", type=int)
    parser.add_argument("--per-request-reservation-micro-usd", type=int)
    args = parser.parse_args()

    # The default CLI always returns safely BEFORE secrets, disk writes,
    # real owner API keys or any network capability.
    if not args.execute_paid_once:
        print(json.dumps({
            "status": "M04_PAID_PUBLIC_WATCH_DEFAULT_OFF",
            "owner_authorized_in_this_invocation": False,
            "daily_limit_set_by_owner": False,
            "per_request_reservation_set_by_owner": False,
            "provider_http_attempts": 0,
            "trading_enabled": False,
        }, sort_keys=True))
        return 0
    if (args.owner_confirm != OWNER_PAID_ONCE_CONFIRMATION
            or args.owner_pricing_reviewed is not True
            or type(args.daily_limit_micro_usd) is not int
            or type(args.per_request_reservation_micro_usd) is not int
            or not 1 <= args.per_request_reservation_micro_usd
                <= args.daily_limit_micro_usd <= 100_000_000):
        print(json.dumps({
            "status": "M04_OWNER_PAID_GO_OR_EXPLICIT_COST_LIMITS_REQUIRED",
            "provider_http_attempts": 0,
            "execution_allowed": False,
        }, sort_keys=True))
        return 2
    # DO NOT allow historical force-asof. One ORIGINAL current M03
    # observation only, at the instant of the requested paid test.
    handoff = inspect_original_radar_three_role_handoff_readonly()
    if (handoff.get("status") !=
            "M04_RADAR_HANDOFF_PUBLIC_PACKETS_PREPARED"
            or not handoff.get("candidates")):
        print(json.dumps({
            "status": "M04_NO_FRESH_PUBLIC_WATCH_FOR_PAID_TEST",
            "provider_http_attempts": 0,
            "execution_allowed": False,
        }, sort_keys=True))
        return 0

    try:
        original = owner_provider_source()
        if original is None:
            raise M04OwnerConfigDenied("M04_PROVIDER_LOCAL_FILE_MISSING")
        env, key_status = original
        if key_status != "M04_V2_KEY_FILE_PRESENT_LOCAL_ONLY":
            raise M04OwnerConfigDenied("M04_PROVIDER_KEY_UNAVAILABLE")
        if (not _private_budget_file_ok(BUDGET_DB)
                or BUDGET_DB.parent != CONFIG_DIR):
            raise M04OwnerConfigDenied("M04_PAID_BUDGET_FILE_UNTRUSTED")
        # Parent protected directory is checked by owner_provider_source.
        # SQLite creates the private ledger with 0600 permissions.
        old_umask = os.umask(0o077)
        try:
            budget = PersistentAiBudget(
                BUDGET_DB,
                daily_limit_micro_usd=args.daily_limit_micro_usd,
                per_request_reservation_micro_usd=
                    args.per_request_reservation_micro_usd,
            )
        finally:
            os.umask(old_umask)
        result = run_public_watch_paid_once(
            handoff=handoff,
            owner_models={r: env["SUMOPOD_MODEL_" + r]
                          for r in ("MARKET", "RISK", "STRATEGY")},
            api_key=env["SUMOPOD_API_KEY"],
            budget=budget,
            owner_confirmation=OWNER_PAID_ONCE_CONFIRMATION,
            owner_pricing_reviewed=True,
        )
        # Never print env, HTTP headers, provider raw JSON, or prompts.
        print(json.dumps(result, sort_keys=True, ensure_ascii=True))
        return (0 if result["status"] ==
                "M04_PUBLIC_WATCH_THREE_ROLE_PROVIDER_RESPONSES_RECEIVED"
                else 2)
    except (M04OwnerConfigDenied, AiBudgetError, OSError):
        print(json.dumps({
            "status": "M04_PAID_PROVIDER_CONFIG_OR_BUDGET_UNAVAILABLE",
            "provider_http_attempts": 0,
            "execution_allowed": False,
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
