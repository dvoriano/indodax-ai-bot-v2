"""M05 isolated systemd ONE-SHOT dedicated Telegram tick.

This is a thin service adapter to the ORIGINAL V2 owner ledger and poller.
A timer may invoke it only after explicit local opt-in. No trading, paid AI,
legacy Caipiti token, order, webhook mutations or competing poller.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from functools import partial
import sqlite3
import stat
import sys
from typing import Any, Mapping

from .config import ConfigurationError, Settings
from .owner_control import OwnerDecisionLedger
from .telegram_bot_api import TelegramBotApiClient, TelegramTransportError, TOKEN_RE
from .telegram_dedicated_inbound import (
    LEGACY_CAIPITI_BOT_ID, dedicated_owner_poll_once,
    dedicated_owner_fast_loop_nonlive,
)


def _numeric(env: Mapping[str, str], name: str) -> int:
    raw = env.get(name, "")
    if (not isinstance(raw, str) or not raw.isascii()
            or not raw.isdecimal() or not 1 <= len(raw) <= 19
            or int(raw) <= 0):
        raise ConfigurationError("M05_TICK_INVALID_" + name)
    return int(raw)


def _private_path(raw: str, *, directory: bool = False) -> Path:
    if not isinstance(raw, str) or not raw or len(raw) > 1024:
        raise ConfigurationError("M05_TICK_INVALID_PRIVATE_PATH")
    p = Path(raw)
    if not p.is_absolute() or p.resolve() != p:
        raise ConfigurationError("M05_TICK_INVALID_PRIVATE_PATH")
    try:
        st = p.lstat()
        if (st.st_uid != os.getuid() or st.st_nlink != (2 if directory else 1)):
            # Directories can have more links when containing subdirectories.
            if not directory or st.st_uid != os.getuid():
                raise ConfigurationError("M05_TICK_UNSAFE_PRIVATE_FILE")
        expected_mode = 0o700 if directory else 0o600
        if (stat.S_IMODE(st.st_mode) != expected_mode
                or (not stat.S_ISDIR(st.st_mode) if directory
                    else not stat.S_ISREG(st.st_mode))):
            raise ConfigurationError("M05_TICK_UNSAFE_PRIVATE_FILE")
    except (FileNotFoundError, PermissionError, OSError):
        raise ConfigurationError("M05_TICK_PRIVATE_FILE_UNAVAILABLE") from None
    return p


def _private_file(raw: str) -> Path:
    p = _private_path(raw)
    _private_path(str(p.parent), directory=True)
    return p


def _token_from_file(path: Path, expected_bot_id: int) -> str:
    # Opening with NOFOLLOW prevents a last-moment symlink substitution.
    try:
        fd = os.open(
            path, os.O_RDONLY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            st = os.fstat(fd)
            if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
                    or st.st_nlink != 1 or stat.S_IMODE(st.st_mode) != 0o600
                    or st.st_size > 256):
                raise ConfigurationError("M05_TICK_UNSAFE_TOKEN_FILE")
            raw = os.read(fd, 257)
        finally:
            os.close(fd)
    except OSError:
        raise ConfigurationError("M05_TICK_TOKEN_FILE_UNAVAILABLE") from None

    try:
        token = raw.decode("ascii").rstrip("\r\n")
    except UnicodeError:
        raise ConfigurationError("M05_TICK_TOKEN_INVALID") from None
    if (not TOKEN_RE.fullmatch(token)
            or token.split(":", 1)[0] != str(expected_bot_id)):
        raise ConfigurationError("M05_TICK_TOKEN_ID_MISMATCH")
    return token


def _existing_owner_ledger(db_path: Path, owner_id: int) -> OwnerDecisionLedger:
    _private_file(str(db_path))
    try:
        db = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            db.execute("PRAGMA query_only=ON")
            who = db.execute(
                "SELECT owner_user_id FROM m05_config WHERE singleton=1"
            ).fetchone()
            cursor = db.execute(
                "SELECT next_offset FROM m05_telegram_cursor WHERE singleton=1"
            ).fetchone()
            claimed = db.execute(
                "SELECT COUNT(*) FROM m05_telegram_updates WHERE status='CLAIMED'"
            ).fetchone()[0]
        finally:
            db.close()
    except (OSError, sqlite3.Error):
        raise ConfigurationError("M05_TICK_OWNER_DB_UNAVAILABLE") from None
    if who != (owner_id,) or cursor is None or (
            type(cursor[0]) is not int or cursor[0] < 0):
        raise ConfigurationError("M05_TICK_OWNER_DB_MISMATCH")
    if claimed:
        # Fail BEFORE getMe/getWebhookInfo/getUpdates to avoid repeatedly
        # querying Telegram when an interrupted claim requires review.
        raise ConfigurationError("M05_TICK_INCOMPLETE_REVIEW_REQUIRED")
    return OwnerDecisionLedger(db_path, owner_user_id=owner_id)


def _prepare_tick_runtime(
        cfg: Mapping[str, str], *, client_timeout_seconds: float,
) -> tuple[TelegramBotApiClient, OwnerDecisionLedger, int, Path, Any]:
    """Shared original M05 auth and owner-safe source setup for both modes."""
    Settings.from_env(cfg).require_m01_safety()
    for gate in (
        "M05_TELEGRAM_TICK_GO", "M05_TELEGRAM_NETWORK_GO",
        "M05_TELEGRAM_DEDICATED_GO",
    ):
        if cfg.get(gate) != "YES":
            raise ConfigurationError("M05_TICK_REQUIRED_" + gate)
    bot_id = _numeric(cfg, "M05_TELEGRAM_DEDICATED_BOT_ID")
    owner_id = _numeric(cfg, "M05_TELEGRAM_OWNER_ID")
    if bot_id == LEGACY_CAIPITI_BOT_ID:
        raise ConfigurationError("M05_TICK_LEGACY_CAIPITI_DENIED")

    state_db = _private_file(cfg.get("M05_TELEGRAM_OWNER_DB", ""))
    token_path = _private_file(cfg.get("M05_TELEGRAM_TOKEN_FILE", ""))
    if token_path == state_db or token_path == Path(
            str(state_db) + ".dedicated-poller.lock"):
        raise ConfigurationError("M05_TICK_TOKEN_PATH_CONFLICT")
    ledger = _existing_owner_ledger(state_db, owner_id)
    # Optional original M07 callback bridge. No View Only GET, public scan
    # or journal side effects when GO is absent. The on-disk owner and
    # original decision journals must already exist and be private.
    on_click = None
    if cfg.get("M07_MANUAL_RECHECK_NONLIVE_GO") == "YES":
        from decimal import Decimal, InvalidOperation
        from .journal import DecisionJournal
        from .orchestrator import DailyScalpingOrchestrator
        from .m06_scoped_https import (
            _owner_private_file, read_view_only_credentials,
        )
        decisions = _private_file(cfg.get(
            "M07_MANUAL_RECHECK_DECISION_DB", ""))
        funding_file = _private_file(cfg.get(
            "M07_MANUAL_RECHECK_FUNDING_FILE", ""))
        key_file = _private_file(cfg.get(
            "M07_MANUAL_RECHECK_VIEWONLY_KEY_FILE", ""))
        if len({decisions, funding_file, key_file, token_path, state_db}) != 5:
            raise ConfigurationError("M07_TICK_SOURCE_FILE_COLLISION")
        owned = cfg.get("M07_MANUAL_RECHECK_OWNERSHIP_DB")
        ownership_file = _private_file(owned) if owned else None
        if ownership_file is not None and ownership_file in (
                decisions, funding_file, key_file, token_path, state_db):
            raise ConfigurationError("M07_TICK_OWNERSHIP_FILE_COLLISION")
        raw = _owner_private_file(str(funding_file))
        from .m07_main_idr_sizing import valid_main_funding_schema
        if not valid_main_funding_schema(raw):
            raise ConfigurationError("M07_TICK_FUNDING_SCHEMA_INVALID")
        expected_keys = set(raw)
        if any(type(raw[k]) is not str or not 1 <= len(raw[k]) <= 40
               or not raw[k].isascii() for k in expected_keys):
            raise ConfigurationError("M07_TICK_FUNDING_AMOUNT_INVALID")
        try:
            funding = {key: Decimal(raw[key]) for key in expected_keys}
        except (ValueError, InvalidOperation):
            raise ConfigurationError("M07_TICK_FUNDING_AMOUNT_INVALID") from None
        if (any(not v.is_finite() or v < 0 for v in funding.values())
                or ("requested_ticket_ceiling_idr" in funding
                    and funding["requested_ticket_ceiling_idr"] <= 0)
                or not 0 < funding["max_spread_bps"] <= 500):
            raise ConfigurationError("M07_TICK_FUNDING_AMOUNT_INVALID")
        # Instantiate the SAME original orchestrator only inside owner
        # click handler. The normal M05 tick remains Telegram-only.
        def _nonlive_owner_click(preview_id: str, click_epoch: int) -> dict:
            if type(click_epoch) is not int or click_epoch <= 0:
                raise ConfigurationError("M07_TICK_CLICK_EPOCH_INVALID")
            api_key, secret_key = read_view_only_credentials(str(key_file))
            app = DailyScalpingOrchestrator(
                Settings.from_env(cfg), DecisionJournal(decisions),
                owner_control=ledger,
            )
            return app.recheck_m07_clicked_manual_buy_from_sources_nonlive_once(
                owner_user_id=owner_id,
                preview_id=preview_id, api_key=api_key,
                secret_key=secret_key, funding=funding,
                local_ownership_db=(
                    str(ownership_file) if ownership_file is not None else None),
            )
        on_click = _nonlive_owner_click
    token = _token_from_file(token_path, bot_id)
    client = TelegramBotApiClient(
        token=token, allow_network=True,
        timeout_seconds=client_timeout_seconds,
    )
    return client, ledger, bot_id, state_db, on_click


def service_tick_once(*, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    cfg = os.environ if env is None else env
    client, ledger, bot_id, state_db, on_click = _prepare_tick_runtime(
        cfg, client_timeout_seconds=6,
    )
    outcome = dedicated_owner_poll_once(
        api=client, ledger=ledger, expected_bot_id=bot_id,
        lock_path=str(state_db) + ".dedicated-poller.lock",
        limit=10,
        on_manual_buy_preview_nonlive=on_click,
    )
    return {
        "status": "M05_TICK_COMPLETED",
        "processed": outcome["handler"]["handled"],
        "ignored": outcome["handler"]["ignored"],
        "next_offset": outcome["handler"]["next_offset"],
        "bot_identity_pinned": True,
        "old_caipiti_bot_used": False,
        "execution_allowed": False,
        "final_decision": None,
    }


def service_fast_loop_nonlive(
        *, env: Mapping[str, str] | None = None,
        max_batches: int | None = None, stop_requested=None,
        heartbeat=None) -> dict[str, Any]:
    """Explicit opt-in persistent low-latency owner Telegram receiver.

    The old one-shot service and the installed timer remain unchanged
    unless the owner *separately* deploys the reviewed systemd drop-in
    after disabling the original M05 timer. Same bot ID, same cursor,
    same SQLite lock, same M05 commands, same M07 NONLIVE callback.
    """
    cfg = os.environ if env is None else env
    if cfg.get("M05_TELEGRAM_FAST_LONGPOLL_GO") != "YES":
        raise ConfigurationError("M05_FAST_LONGPOLL_GO_REQUIRED")
    client, ledger, bot_id, state_db, on_click = _prepare_tick_runtime(
        cfg, client_timeout_seconds=9,
    )
    # Explicit opt-in only. The original running receiver stays unchanged
    # until the owner deploys this source and configures this gate.
    auto_ops_alert = None
    if cfg.get("M10_OWNER_AUTO_ALERT_GO") == "YES":
        from .m10_owner_ops_auto_alert import send_one_owner_ops_alert_nonlive
        auto_ops_alert = send_one_owner_ops_alert_nonlive
    auto_radar_alert = None
    # Separate opt-in. Leave existing M05 fast receiver unchanged during
    # the in-progress M10-06 24h audit; never restart here.
    if cfg.get("M05_RADAR_MOMENTUM_ALERT_GO") == "YES":
        from .m05_radar_momentum_nonlive import send_one_owner_radar_momentum_nonlive
        # Owner opt-in for STRICT priority publication only. Default OFF:
        # historical radar + early M03 scouting remain unchanged.
        priority_kwargs = (
            {"priority_alert_enabled": True}
            if cfg.get("M05_RADAR_PRIORITY_V2_GO") == "YES" else {}
        )
        auto_radar_alert = (
            partial(send_one_owner_radar_momentum_nonlive,
                    ws3_enabled=True, **priority_kwargs)
            if cfg.get("M05_WS3_RADAR_WATCH_GO") == "YES"
            else (partial(send_one_owner_radar_momentum_nonlive,
                          **priority_kwargs) if priority_kwargs
                  else send_one_owner_radar_momentum_nonlive)
        )
    return dedicated_owner_fast_loop_nonlive(
        api=client, ledger=ledger, expected_bot_id=bot_id,
        lock_path=str(state_db) + ".dedicated-poller.lock",
        on_manual_buy_preview_nonlive=on_click,
        wait_seconds=2,
        max_batches=max_batches, stop_requested=stop_requested,
        heartbeat=heartbeat,
        auto_ops_alert=auto_ops_alert,
        auto_radar_alert=auto_radar_alert,
    )


def fast_main() -> int:
    """The sole alternative ExecStart for the ORIGINAL V2 M05 service."""
    import signal
    stopping = False

    def _stop(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        report = service_fast_loop_nonlive(
            stop_requested=lambda: stopping,
            heartbeat=lambda row: print(
                json.dumps(row, sort_keys=True), flush=True),
        )
    except (ConfigurationError, TelegramTransportError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error):
        print("M05_FAST_LONGPOLL_LOCAL_ERROR", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0


def main() -> int:
    try:
        result = service_tick_once()
    except (ConfigurationError, TelegramTransportError, ValueError) as error:
        # Stable error codes only: NEVER echo token, raw HTTP response or env.
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error):
        print("M05_TICK_LOCAL_ERROR", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
