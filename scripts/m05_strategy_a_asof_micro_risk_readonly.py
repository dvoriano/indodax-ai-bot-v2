"""Original V2 Strategy A as-of pre-entry microstructure risk diagnostic.

READ ONLY: original owner M05 ACK + original M03 private micro SQLite.
The original M03 micro archive keeps ~2 hours and observes at most two pairs
per cycle. Missing evidence is UNKNOWN, never a failed coin, zero activity,
aggressor BUY proof, G1 approval or a BUY/SELL suggestion. No lookahead.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from zoneinfo import ZoneInfo

from indodax_ai_bot.m05_radar_momentum_nonlive import (
    DEFAULT_DB, _owned_private_regular,
)
from scripts.m05_strategy_a_four_ack_followup_readonly import (
    COHORT, EXPECTED_ORIGINAL_PID, _unit_pid,
)
from scripts.m05_strategy_a_priority_receipts_readonly import _process_env

WIB = ZoneInfo("Asia/Jakarta")
COLUMNS = (
    "minute_epoch", "observed_local_epoch", "trade_frames",
    "source_buy_count", "source_sell_count", "source_buy_idr",
    "source_sell_idr", "last_trade_epoch", "trade_gap_observed",
    "trade_truncated", "book_local_epoch", "best_bid_idr", "best_ask_idr",
    "spread_bps", "bid_depth_top5_idr", "ask_depth_top5_idr",
    "source_malformed",
)
MAX_AGE_SECONDS = 90
MAX_SPREAD_BPS_RESEARCH = Decimal("50")
MIN_BID_DEPTH_IDR_RESEARCH = Decimal("1000000")
SELL_SIDE_RATIO_RESEARCH = Decimal("1.20")


def _number(value: object, *, positive: bool = False) -> Decimal:
    if (type(value) not in (str, int) or
            (type(value) is str and len(value) > 64)):
        raise ValueError("M05_ASOF_MICRO_INVALID_NUMBER")
    try:
        v = Decimal(value)
    except (ValueError, InvalidOperation):
        raise ValueError("M05_ASOF_MICRO_INVALID_NUMBER") from None
    if (not v.is_finite() or abs(v) >= Decimal("1e25")
            or (v <= 0 if positive else v < 0)):
        raise ValueError("M05_ASOF_MICRO_INVALID_NUMBER")
    return v


def assess_asof_micro_risk_nonlive(*, claimed_epoch: int,
                                  row: tuple | None) -> dict:
    """Bounded, conservative, descriptive. Zero trade/order authority."""
    if type(claimed_epoch) is not int or claimed_epoch <= 0:
        raise ValueError("M05_ASOF_MICRO_INVALID_CLAIM")
    common = {
        "trading_allowed": False,
        "buy_recommendation_allowed": False,
        "execution_allowed": False,
        "orders_sent": 0,
        "telegram_messages_sent": 0,
        "source_reported_side_is_not_aggressor_verified": True,
        "complete_trade_tape_verified": False,
        "g1_verified": False,
        "three_ai_role_responses_verified": False,
        "asof_micro_is_not_attested_original_sender_input": True,
    }
    if row is None:
        return {**common, "status": "ASOF_MICRO_NOT_RETAINED_OR_NOT_CAPTURED",
                "risk_reasons": ["MICRO_SOURCE_ABSENT"]}
    if type(row) is not tuple or len(row) != len(COLUMNS):
        raise ValueError("M05_ASOF_MICRO_BAD_ROW")
    data = dict(zip(COLUMNS, row))
    minute, captured = data["minute_epoch"], data["observed_local_epoch"]
    if (type(minute) is not int or minute % 60
            or type(captured) is not int
            or captured < minute or captured > claimed_epoch
            or minute > claimed_epoch // 60 * 60
            or type(data["trade_frames"]) is not int
            or not 0 <= data["trade_frames"] <= 600
            or any(data[k] not in (0, 1) or type(data[k]) is not int
                   for k in ("source_malformed", "trade_gap_observed",
                             "trade_truncated"))):
        return {**common, "status": "ASOF_MICRO_MALFORMED_OR_FUTURE",
                "risk_reasons": ["SOURCE_TIMELINE_OR_STRUCTURE_INVALID"]}
    if claimed_epoch - captured > MAX_AGE_SECONDS:
        return {**common, "status": "ASOF_MICRO_STALE_BEFORE_CLAIM",
                "risk_reasons": ["CAPTURE_NOT_FRESH_AT_CLAIM"]}
    reasons = []
    if data["source_malformed"] or data["trade_gap_observed"] or data[
            "trade_truncated"]:
        reasons.append("TRADES_MALFORMED_GAPPED_OR_TRUNCATED")
    trade_ok = data["trade_frames"] > 0
    buy_val = sell_val = None
    if trade_ok:
        buys, sells = data["source_buy_count"], data["source_sell_count"]
        last = data["last_trade_epoch"]
        if (type(buys) is not int or type(sells) is not int
                or not 0 <= buys + sells <= 1000
                or type(last) is not int or not 0 <= claimed_epoch-last <= 90):
            trade_ok = False
        else:
            try:
                buy_val = _number(data["source_buy_idr"])
                sell_val = _number(data["source_sell_idr"])
            except ValueError:
                trade_ok = False
    if not trade_ok:
        reasons.append("SOURCE_TRADE_ACTIVITY_UNVERIFIED")
    book_ok = True
    book_epoch = data["book_local_epoch"]
    if (type(book_epoch) is not int or
            not 0 <= claimed_epoch-book_epoch <= 90):
        book_ok = False
    else:
        try:
            bid = _number(data["best_bid_idr"], positive=True)
            ask = _number(data["best_ask_idr"], positive=True)
            spread = _number(data["spread_bps"])
            bid_depth = _number(data["bid_depth_top5_idr"])
            ask_depth = _number(data["ask_depth_top5_idr"])
            expected = (ask - bid) / ((ask + bid) / 2) * Decimal("10000")
            if (bid >= ask or abs(expected-spread) > Decimal("0.05")
                    or ask_depth < 0):
                book_ok = False
        except (ValueError, InvalidOperation, ZeroDivisionError):
            book_ok = False
    if not book_ok:
        reasons.append("SOURCE_BOOK_MISSING_STALE_OR_INCONSISTENT")
    if trade_ok and buy_val is not None and sell_val is not None:
        if sell_val > buy_val * SELL_SIDE_RATIO_RESEARCH:
            reasons.append("SOURCE_REPORTED_SELL_SIDE_EXCEEDS_BUY_SIDE")
    if book_ok:
        if spread > MAX_SPREAD_BPS_RESEARCH:
            reasons.append("OBSERVED_BOOK_SPREAD_GT_50_BPS_RESEARCH")
        if bid_depth < MIN_BID_DEPTH_IDR_RESEARCH:
            reasons.append("OBSERVED_TOP5_BID_DEPTH_LT_1M_IDR_RESEARCH")
    return {
        **common,
        "status": ("ASOF_MICRO_RISK_REVIEW_ONLY" if reasons
                   else "ASOF_MICRO_PARTIAL_OBSERVED_NOT_BUY_PROOF"),
        "risk_reasons": reasons,
        "trade_activity_evidence_present": trade_ok,
        "book_snapshot_evidence_present": book_ok,
        "source_reported_buy_idr": str(buy_val) if trade_ok else None,
        "source_reported_sell_idr": str(sell_val) if trade_ok else None,
        "observed_spread_bps": str(spread) if book_ok else None,
        "observed_bid_depth_top5_idr": str(bid_depth) if book_ok else None,
        "source_captured_wib": datetime.fromtimestamp(
            captured, WIB).strftime("%Y-%m-%d %H:%M:%S WIB"),
        "research_thresholds_not_calibrated": True,
    }


def inspect_original_four_ack_asof_micro_readonly() -> dict:
    try:
        pid = _unit_pid()
        if pid != EXPECTED_ORIGINAL_PID:
            raise ValueError("ORIGINAL_PID_CHANGED")
        process = _process_env(pid)  # active process NO/NO/DRY_RUN & opt-in
        owner = Path(process["owner_db"])
        source = Path(DEFAULT_DB)
        if not _owned_private_regular(owner) or not _owned_private_regular(source):
            raise ValueError("PRIVATE_LEDGER_OR_WS3_SOURCE_UNTRUSTED")
        start = int(datetime(2026, 11, 12, 11, 0,
                             tzinfo=WIB).timestamp())
        end = int(datetime(2026, 11, 12, 11, 9,
                           tzinfo=WIB).timestamp())
        with sqlite3.connect(owner.as_uri() + "?mode=ro", uri=True,
                             timeout=3) as db:
            db.execute("PRAGMA query_only=ON")
            events = db.execute(
                "SELECT pair,claimed_epoch,source_observed_epoch,"
                "delivery_state,source_kind,ack_received_epoch "
                "FROM m05_radar_delivery_events "
                "WHERE claimed_epoch BETWEEN ? AND ? "
                "ORDER BY claimed_epoch,pair LIMIT 10",
                (start, end+59),
            ).fetchall()
        if len(events) != 4 or {e[0] for e in events} != set(COHORT):
            raise ValueError("FOUR_ORIGINAL_ACK_COHORT_CHANGED")
        with sqlite3.connect(source.as_uri() + "?mode=ro",
                             uri=True, timeout=3) as db:
            db.execute("PRAGMA query_only=ON")
            table = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='m03_ws_microstructure_watch_minute'"
            ).fetchone()
            records = []
            for pair, claim, observed, state, kind, ack in events:
                if (type(claim) is not int or type(observed) is not int
                        or type(ack) is not int
                        or not observed <= claim <= ack
                        or state != "ACKED" or kind != "WS3"):
                    raise ValueError("ORIGINAL_ACK_EVENT_MISMATCH")
                row = None
                if table:
                    row = db.execute(
                        "SELECT " + ",".join(COLUMNS) + " "
                        "FROM m03_ws_microstructure_watch_minute "
                        "WHERE pair=? AND minute_epoch BETWEEN ? AND ? "
                        "AND observed_local_epoch<=? "
                        "ORDER BY observed_local_epoch DESC LIMIT 1",
                        (pair, (claim//60)*60-180, (claim//60)*60, claim),
                    ).fetchone()
                verdict = assess_asof_micro_risk_nonlive(
                    claimed_epoch=claim, row=row)
                records.append({"pair": pair, "claim_wib":
                    datetime.fromtimestamp(claim, WIB).strftime(
                        "%Y-%m-%d %H:%M WIB"),
                    **verdict})
        if _unit_pid() != pid:
            raise ValueError("M05_CHANGED_DURING_AUDIT")
        return {
            "status": "M05_STRATEGY_A_FOUR_ACK_ASOF_MICRO_RISK_READONLY",
            "original_m05_pid": pid, "events": records,
            "micro_not_retained_or_not_captured": sum(
                e["status"] == "ASOF_MICRO_NOT_RETAINED_OR_NOT_CAPTURED"
                for e in records),
            "micro_observed_not_recommendations": sum(
                e["status"] in (
                    "ASOF_MICRO_PARTIAL_OBSERVED_NOT_BUY_PROOF",
                    "ASOF_MICRO_RISK_REVIEW_ONLY")
                for e in records),
            "micro_retention_is_2h_not_complete_universe": True,
            "source_side_is_not_aggressor_verified": True,
            "future_5m_15m_prices_not_used": True,
            "execution_allowed": False, "orders_sent": 0,
            "telegram_messages_sent": 0, "service_restarted": False,
        }
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError,
            OverflowError):
        return {
            "status": "M05_STRATEGY_A_ASOF_MICRO_RISK_AUDIT_BLOCKED_READONLY",
            "reason": "ORIGINAL_OWNER_OR_MICRO_SOURCE_NOT_VERIFIED",
            "execution_allowed": False, "orders_sent": 0,
            "telegram_messages_sent": 0, "service_restarted": False,
        }


def main() -> int:
    result = inspect_original_four_ack_asof_micro_readonly()
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0 if result["status"] == (
        "M05_STRATEGY_A_FOUR_ACK_ASOF_MICRO_RISK_READONLY") else 2


if __name__ == "__main__":
    sys.exit(main())
