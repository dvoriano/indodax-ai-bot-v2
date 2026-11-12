"""Four original Strategy A Bot API ACKs: fixed-scope mature 5m/15m quality.

READ ONLY. Uses original M05 private owner DB, original M03 WS3 minute DB,
and already opted-in private WS3 72h research archive. No HTTP, Telegram
send, order, AI, service restart, new bot or data writes. Public LAST-price
changes are not executable BUY/SELL returns or bot-owned net ROI.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

from indodax_ai_bot.m03_ws3_evidence_archive_nonlive import DEFAULT_EVIDENCE_DB
from indodax_ai_bot.m05_radar_momentum_nonlive import DEFAULT_DB
from scripts.m05_signal_edge_audit_readonly import inspect_postack_quality_readonly
from scripts.m05_strategy_a_priority_receipts_readonly import _process_env

M05_UNIT = "indodax-v2-m05-telegram.service"
M03_UNIT = "indodax-v2-m03-mainpath.timer"
EXPECTED_ORIGINAL_PID = 588864
COHORT = ("aaplx_idr", "nbtnew_idr", "neon_idr", "anoa_idr")
HORIZONS = ("5m", "15m")
MEASURED = "OBSERVED_PRICE_ONLY_NOT_EXECUTABLE"
UNMEASURED = frozenset((
    "HORIZON_NOT_MATURE",
    "NO_FRESH_POST_ACK_ENTRY_QUOTE",
    "NO_FRESH_HORIZON_QUOTE",
))
VERIFIED_ACK = frozenset((
    "MATCHED_WS3_ACK", "MATCHED_WS3_ACK_RESEARCH_ARCHIVE",
))


def summarize_four_signal_quality_nonlive(report: dict) -> dict:
    """Never manufacture 4 trades, a denominator win rate, or missing prices."""
    if (type(report) is not dict
            or report.get("status") != "M05_ORIGINAL_WS3_POSTACK_QUALITY_AUDIT_READONLY"
            or report.get("execution_allowed") is not False
            or report.get("orders_sent") != 0
            or report.get("telegram_messages_sent") != 0
            or report.get("research_evidence_source_status")
                != "EXPLICIT_READONLY_RESEARCH_SOURCE"):
        raise ValueError("M05_FOUR_ACK_ORIGINAL_SOURCE_NOT_VERIFIED")
    events = report.get("events")
    if type(events) is not list or len(events) != len(COHORT):
        raise ValueError("M05_FOUR_ACK_INCOMPLETE_COHORT")
    seen = set()
    counters = {h: Counter() for h in HORIZONS}
    summary = []
    for event in events:
        if type(event) is not dict:
            raise ValueError("M05_FOUR_ACK_INVALID_EVENT")
        pair = event.get("pair")
        if (type(pair) is not str or pair not in COHORT or pair in seen
                or event.get("delivery_state") != "ACKED"):
            raise ValueError("M05_FOUR_ACK_WRONG_OR_DUPLICATE_PAIR")
        seen.add(pair)
        source = event.get("status")
        if source not in VERIFIED_ACK:
            raise ValueError("M05_FOUR_ACK_UNVERIFIED_WS3_PROVENANCE")
        proxies = event.get("price_proxies")
        if not isinstance(proxies, dict):
            raise ValueError("M05_FOUR_ACK_NO_PRICE_PROXIES")
        result = {"pair": pair, "source_status": source}
        for horizon in HORIZONS:
            observation = proxies.get(horizon)
            if not isinstance(observation, dict):
                raise ValueError("M05_FOUR_ACK_MISSING_HORIZON")
            status = observation.get("status")
            if status == MEASURED:
                gross = observation.get("gross_move_bps")
                if type(gross) is not str or not 1 <= len(gross) <= 40:
                    raise ValueError("M05_FOUR_ACK_INVALID_GROSS_MOVE")
                try:
                    bps = Decimal(gross)
                except (ValueError, InvalidOperation):
                    raise ValueError("M05_FOUR_ACK_INVALID_GROSS_MOVE") from None
                if not bps.is_finite() or abs(bps) > Decimal("100000"):
                    raise ValueError("M05_FOUR_ACK_INVALID_GROSS_MOVE")
                if (type(observation.get("post_ack_quote_wib")) is not str
                        or type(observation.get("horizon_quote_wib")) is not str):
                    raise ValueError("M05_FOUR_ACK_QUOTE_TIMES_MISSING")
                result[horizon] = {
                    "status": status,
                    "gross_last_price_bps": str(bps),
                    "gross_last_price_pct": str(
                        (bps / Decimal("100")).quantize(Decimal("0.0001"))
                    ),
                    "first_quote_wib": observation["post_ack_quote_wib"],
                    "horizon_quote_wib": observation["horizon_quote_wib"],
                }
                counters[horizon]["measured"] += 1
                counters[horizon][
                    "positive" if bps > 0 else
                    "negative" if bps < 0 else "flat"
                ] += 1
            elif status in UNMEASURED:
                # Missing quote is NOT 0%, loss, or a negative trade.
                result[horizon] = {"status": status, "gross_last_price_bps": None,
                                   "gross_last_price_pct": None}
                counters[horizon][status] += 1
            else:
                raise ValueError("M05_FOUR_ACK_UNKNOWN_PROXY_STATUS")
        summary.append(result)
    if seen != set(COHORT):
        raise ValueError("M05_FOUR_ACK_INCOMPLETE_SET")
    summary.sort(key=lambda x: COHORT.index(x["pair"]))
    return {
        "status": "M05_STRATEGY_A_FOUR_ACK_PRICE_FOLLOWUP_READONLY",
        "expected_original_ack_cohort": len(COHORT),
        "quality_counts": {
            h: {
                "observed_price_only": counters[h]["measured"],
                "observed_price_positive": counters[h]["positive"],
                "observed_price_negative": counters[h]["negative"],
                "observed_price_flat": counters[h]["flat"],
                "not_mature": counters[h]["HORIZON_NOT_MATURE"],
                "no_fresh_entry_quote": counters[h][
                    "NO_FRESH_POST_ACK_ENTRY_QUOTE"],
                "no_fresh_horizon_quote": counters[h][
                    "NO_FRESH_HORIZON_QUOTE"],
            } for h in HORIZONS
        },
        "events": summary,
        "missing_prices_are_not_losses": True,
        "observed_price_direction_is_not_trade_win_rate": True,
        "not_actual_fee_slippage_bid_depth_or_executable_roi": True,
        "not_market_risk_strategy_ai_attestation": True,
        "original_m03_m05_unmodified": True,
        "execution_allowed": False,
        "orders_sent": 0,
        "telegram_sent_by_audit": 0,
        "service_restarted": False,
    }


def _unit_pid() -> int:
    proc = subprocess.run(
        ["systemctl", "--user", "show", M05_UNIT,
         "-p", "MainPID", "-p", "ActiveState"],
        capture_output=True, text=True, check=True, timeout=8)
    values = dict(row.split("=", 1) for row in proc.stdout.splitlines()
                  if "=" in row)
    if values.get("ActiveState") != "active":
        raise ValueError("M05_SERVICE_NOT_ACTIVE")
    raw = values.get("MainPID", "")
    if not raw.isascii() or not raw.isdecimal():
        raise ValueError("M05_PID_INVALID")
    pid = int(raw)
    if pid != EXPECTED_ORIGINAL_PID:
        raise ValueError("M05_ORIGINAL_PID_CHANGED_REVIEW_REQUIRED")
    scanner = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", M03_UNIT],
        capture_output=True, check=False, timeout=8)
    if scanner.returncode != 0:
        raise ValueError("M03_SCANNER_TIMER_NOT_ACTIVE")
    return pid


def inspect_original_four_ack_followup_readonly() -> dict:
    try:
        pid = _unit_pid()
        origin = _process_env(pid)  # verifies actual new-process NO/NO/DRY_RUN
        wib = ZoneInfo("Asia/Jakarta")
        start = int(datetime(2026, 11, 12, 11, 0,
                             tzinfo=wib).timestamp())
        end = int(datetime(2026, 11, 12, 11, 9,
                           tzinfo=wib).timestamp())
        report = inspect_postack_quality_readonly(
            start_epoch=start, end_epoch=end, pairs=COHORT,
            radar_claims_db=Path(origin["owner_db"]),
            db_path=DEFAULT_DB, research_evidence_db=DEFAULT_EVIDENCE_DB,
            now_epoch=int(time.time()),
        )
        summary = summarize_four_signal_quality_nonlive(report)
        if _unit_pid() != pid:
            raise ValueError("M05_PID_CHANGED_DURING_FOLLOWUP")
        return {**summary, "original_m05_pid": pid}
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError,
            OverflowError):
        # Fail closed; never print raw path, exception, /proc env, token/ID.
        return {
            "status": "M05_STRATEGY_A_FOUR_ACK_FOLLOWUP_BLOCKED_READONLY",
            "reason": "ORIGINAL_PRIVATE_SOURCE_OR_COHORT_NOT_VERIFIED",
            "execution_allowed": False, "orders_sent": 0,
            "telegram_sent_by_audit": 0, "service_restarted": False,
        }


def main() -> int:
    result = inspect_original_four_ack_followup_readonly()
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":"),
                     sort_keys=True))
    return 0 if result["status"] == (
        "M05_STRATEGY_A_FOUR_ACK_PRICE_FOLLOWUP_READONLY") else 2


if __name__ == "__main__":
    sys.exit(main())
