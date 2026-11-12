"""Original V2 M05 WS3 ACK as-of micro quality, strictly nonexecuting.

Research-only source classifier for a historical owner ACK. It never issues a
G1 receipt, claims that M05 used the reconstructed evidence, calls models,
modifies an archive or changes the active M03/M05 WATCH path.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from indodax_ai_bot.m05_radar_momentum_nonlive import WS3_MAX_AGE_SECONDS

WIB = ZoneInfo("Asia/Jakarta")
MAX_MICRO_AGE_SECONDS = 90
MICRO_LOOKBACK_SECONDS = 180
MAX_ACK_EVENTS = 64
MIN_VOLUME_IDR = Decimal("150000000")

SOURCE_COLUMNS = (
    "minute_epoch", "source_exchange_epoch", "last_idr",
    "rolling_24h_idr", "public_offset", "source_status",
    "recorded_local_epoch",
)
MICRO_COLUMNS = (
    "minute_epoch", "recorded_local_epoch", "trade_frames",
    "source_buy_count", "source_sell_count", "source_buy_idr",
    "source_sell_idr", "last_trade_epoch", "trade_offset",
    "trade_gap_observed", "trade_truncated", "book_local_epoch",
    "book_offset", "best_bid_idr", "best_ask_idr", "spread_bps",
    "bid_depth_top5_idr", "ask_depth_top5_idr", "source_malformed",
    "source_complete", "aggressor_verified", "g1_verified",
)


def _number(value: object, *, positive: bool = False) -> Decimal:
    if (type(value) not in (str, int, Decimal)
            or (type(value) is str and len(value) > 64)):
        raise ValueError("SOURCE_NUMBER_INVALID")
    try:
        result = Decimal(value)
    except (ValueError, InvalidOperation, TypeError):
        raise ValueError("SOURCE_NUMBER_INVALID") from None
    if (not result.is_finite() or abs(result) >= Decimal("1e25")
            or (result <= 0 if positive else result < 0)):
        raise ValueError("SOURCE_NUMBER_INVALID")
    return result


def classify_ws3_preclaim_quality_nonlive(
    *, pair: str, claim_epoch: int, source_epoch: int,
    price_row: tuple | None, micro_row: tuple | None,
) -> dict:
    """One bound source/claim pair. UNKNOWN never becomes a negative trade.

    Timestamp relation to original alert-source epoch is distinct from proof
    that the original sender consumed this observation. No hindsight prices.
    """
    if (type(pair) is not str or not pair.endswith("_idr")
            or not pair[:-4].isalnum() or not pair.islower()
            or type(claim_epoch) is not int or claim_epoch <= 0
            or type(source_epoch) is not int
            or not 0 < source_epoch <= claim_epoch):
        raise ValueError("WS3_CLAIM_ARGUMENT_INVALID")
    result = {
        "pair": pair,
        "claim_wib": datetime.fromtimestamp(claim_epoch, WIB).isoformat(),
        "evidence_tier": "SOURCE_UNVERIFIED",
        "price_status": "PRICE_SOURCE_NOT_RECONSTRUCTED",
        "micro_status": "MICRO_UNKNOWN",
        "micro_vs_alert_source": "UNKNOWN",
        "source_age_sec": claim_epoch - source_epoch,
        "micro_age_sec": None,
        "fresh_trade_observed": False,
        "fresh_book_observed": False,
        "research_cautions": [
            "G1_INDEPENDENT_SOURCE_UNVERIFIED",
            "COMPLETE_15M_1H_TRADE_TAPE_UNVERIFIED",
            "AGGRESSOR_SIDE_UNVERIFIED",
        ],
        "m04_risk_input_status": "OBSERVATIONAL_SOURCE_REVIEW_ONLY",
        "m04_actual_role_responses_verified": False,
        "original_m05_sender_consumed_micro_verified": False,
        "source_side_is_not_aggressor_proof": True,
        "watch_publication_policy_changed": False,
        "g1_verified": False,
        "risk_veto": True,
        "final_decision": "REJECT",
        "execution_allowed": False,
        "buy_recommendation_allowed": False,
        "orders_sent": 0,
        "telegram_messages_sent_by_audit": 0,
        "provider_calls_by_audit": 0,
    }
    price_ok = False
    if price_row is not None:
        if type(price_row) is not tuple or len(price_row) != len(SOURCE_COLUMNS):
            raise ValueError("WS3_PRICE_ROW_INVALID")
        minute, epoch, raw_price, raw_vol, offset, status, recorded = price_row
        if (type(minute) is int and minute % 60 == 0
                and type(epoch) is int and epoch == source_epoch
                and -90 <= epoch - minute < 60
                and type(recorded) is int
                and epoch <= recorded <= claim_epoch
                and type(offset) is int and offset >= 0
                and status == "ELIGIBLE_SOURCE_OBSERVATION"
                and 0 <= claim_epoch - epoch <= WS3_MAX_AGE_SECONDS):
            try:
                _number(raw_price, positive=True)
                price_ok = _number(raw_vol) >= MIN_VOLUME_IDR
            except ValueError:
                price_ok = False
    if not price_ok:
        result["research_cautions"].append("PRICE_ASOF_MISSING_STALE_OR_INVALID")
        return result

    result["evidence_tier"] = "PRICE_ONLY"
    result["price_status"] = "PRICE_FRESH_AT_CLAIM"
    if micro_row is None:
        result["research_cautions"].append("MICRO_NOT_CAPTURED_OR_NOT_RETAINED")
        return result
    if type(micro_row) is not tuple or len(micro_row) != len(MICRO_COLUMNS):
        raise ValueError("WS3_MICRO_ROW_INVALID")
    m = dict(zip(MICRO_COLUMNS, micro_row))
    minute, captured = m["minute_epoch"], m["recorded_local_epoch"]
    if (type(minute) is not int or minute % 60 != 0
            or type(captured) is not int
            or captured // 60 * 60 != minute
            or captured > claim_epoch or captured < claim_epoch - MICRO_LOOKBACK_SECONDS):
        result["micro_status"] = "MICRO_BAD_TIMELINE_OR_FUTURE"
        result["research_cautions"].append("MICRO_TIMELINE_UNTRUSTED")
        return result
    result["micro_age_sec"] = claim_epoch - captured
    result["micro_vs_alert_source"] = (
        "AFTER_ALERT_SOURCE_BEFORE_CLAIM"
        if captured > source_epoch else "AT_OR_BEFORE_ALERT_SOURCE"
    )
    if claim_epoch - captured > MAX_MICRO_AGE_SECONDS:
        result["micro_status"] = "MICRO_STALE_AT_CLAIM"
        result["research_cautions"].append("MICRO_CAPTURE_STALE")
        return result
    flags = ("trade_gap_observed", "trade_truncated", "source_malformed")
    authority = ("source_complete", "aggressor_verified", "g1_verified")
    if (any(type(m[k]) is not int or m[k] not in (0, 1) for k in flags)
            or any(type(m[k]) is not int or m[k] != 0 for k in authority)
            or any(m[k] != 0 for k in flags)
            or type(m["trade_frames"]) is not int
            or not 0 <= m["trade_frames"] <= 600):
        result["micro_status"] = "MICRO_FLAGS_OR_STRUCTURE_INVALID"
        result["research_cautions"].append("MICRO_GAP_TRUNCATION_OR_MALFORMED")
        return result

    trade_ok = False
    last = m["last_trade_epoch"]
    buys, sells = m["source_buy_count"], m["source_sell_count"]
    if (m["trade_frames"] > 0
            and type(last) is int and 0 <= claim_epoch - last <= 90
            and last <= captured and type(m["trade_offset"]) is int
            and m["trade_offset"] >= 0
            and type(buys) is int and type(sells) is int
            and 0 < buys + sells <= 1000 and buys >= 0 and sells >= 0):
        try:
            buy_value = _number(m["source_buy_idr"])
            sell_value = _number(m["source_sell_idr"])
            trade_ok = True
            if sell_value > buy_value * Decimal("1.20"):
                result["research_cautions"].append(
                    "SOURCE_REPORTED_SELL_SIDE_HIGH_NOT_AGGRESSOR_PROOF"
                )
        except ValueError:
            pass

    book_ok = False
    book_stamp = m["book_local_epoch"]
    if (type(book_stamp) is int
            and 0 <= claim_epoch - book_stamp <= 90
            and book_stamp <= captured
            and type(m["book_offset"]) is int
            and m["book_offset"] >= 0):
        try:
            bid = _number(m["best_bid_idr"], positive=True)
            ask = _number(m["best_ask_idr"], positive=True)
            spread = _number(m["spread_bps"])
            bid_depth = _number(m["bid_depth_top5_idr"])
            _number(m["ask_depth_top5_idr"])
            if bid < ask:
                expected_spread = (ask-bid) * 10000 / ((bid+ask)/2)
                book_ok = abs(expected_spread - spread) < Decimal("0.05")
                if book_ok and spread > Decimal("50"):
                    result["research_cautions"].append(
                        "OBSERVED_SPREAD_OVER_50BPS_RESEARCH"
                    )
                if book_ok and bid_depth < Decimal("1000000"):
                    result["research_cautions"].append(
                        "OBSERVED_BID_DEPTH_BELOW_1M_RESEARCH"
                    )
        except (ValueError, InvalidOperation, ZeroDivisionError):
            pass

    result["fresh_trade_observed"] = trade_ok
    result["fresh_book_observed"] = book_ok
    if trade_ok and book_ok:
        result["evidence_tier"] = "TRADE_BOOK_OBSERVED"
        result["micro_status"] = "FRESH_PARTIAL_TRADE_AND_BOOK"
    elif book_ok:
        result["evidence_tier"] = "BOOK_ONLY"
        result["micro_status"] = "FRESH_BOOK_TRADE_UNKNOWN"
    elif trade_ok:
        result["evidence_tier"] = "TRADE_ONLY"
        result["micro_status"] = "FRESH_TRADE_BOOK_UNKNOWN"
    else:
        result["micro_status"] = "NO_FRESH_USABLE_MICRO"
    if not trade_ok:
        result["research_cautions"].append("TRADE_ACTIVITY_UNKNOWN")
    if not book_ok:
        result["research_cautions"].append("ORDERBOOK_UNKNOWN")
    return result


def audit_ws3_ack_cohort_nonlive(
    owner_db: sqlite3.Connection, archive_db: sqlite3.Connection,
    *, start_epoch: int, end_epoch: int,
) -> dict:
    """Fixed, bounded ledger→source→micro as-of join, no table mutations."""
    if (type(start_epoch) is not int or type(end_epoch) is not int
            or start_epoch <= 0 or not start_epoch <= end_epoch
            or end_epoch - start_epoch > 24*3600):
        raise ValueError("WS3_AUDIT_WINDOW_INVALID")

    def has_table(db, name):
        return db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone() is not None

    if not has_table(owner_db, "m05_radar_delivery_events"):
        raise ValueError("OWNER_ACK_TABLE_MISSING")
    price_tables = all(
        has_table(archive_db, name)
        for name in ("ws3_source_evidence", "ws3_source_cycles")
    )
    micro_table = has_table(archive_db, "ws3_micro_research_evidence")
    rows = owner_db.execute(
        "SELECT pair,claimed_epoch,source_observed_epoch,"
        "source_kind,delivery_state,ack_received_epoch "
        "FROM m05_radar_delivery_events "
        "WHERE claimed_epoch BETWEEN ? AND ? "
        "ORDER BY claimed_epoch,pair LIMIT ?",
        (start_epoch, end_epoch, MAX_ACK_EVENTS + 1),
    ).fetchall()
    if len(rows) > MAX_ACK_EVENTS:
        raise ValueError("WS3_AUDIT_EVENT_CAP_EXCEEDED")
    counts = Counter()
    entries = []
    for pair, claim, source_epoch, kind, state, ack in rows:
        if (kind not in ("REST", "WS3")
                or type(pair) is not str
                or type(claim) is not int or type(source_epoch) is not int
                or not 0 < source_epoch <= claim
                or state not in ("CLAIMED", "ACKED", "UNCERTAIN")
                or (state == "ACKED" and
                    (type(ack) is not int or ack < claim))
                or (state != "ACKED" and ack is not None)):
            raise ValueError("WS3_ACK_LEDGER_UNTRUSTED")
        if kind != "WS3":
            counts["REST_NOT_RECONSTRUCTED"] += 1
            continue
        if state != "ACKED":
            counts["WS3_NOT_ACKED"] += 1
            continue
        price = None
        if price_tables:
            price = archive_db.execute(
                "SELECT p.minute_epoch,p.source_exchange_epoch,"
                "p.last_idr,p.rolling_24h_idr,p.public_offset,"
                "p.source_status,c.recorded_local_epoch "
                "FROM ws3_source_evidence p "
                "JOIN ws3_source_cycles c USING(minute_epoch) "
                "WHERE p.pair=? AND p.source_exchange_epoch=? "
                "AND c.recorded_local_epoch<=? "
                "ORDER BY c.recorded_local_epoch DESC LIMIT 1",
                (pair, source_epoch, claim),
            ).fetchone()
        micro = None
        if micro_table:
            micro = archive_db.execute(
                "SELECT " + ",".join(MICRO_COLUMNS)
                + " FROM ws3_micro_research_evidence "
                "WHERE pair=? AND recorded_local_epoch BETWEEN ? AND ? "
                "ORDER BY recorded_local_epoch DESC LIMIT 1",
                (pair, claim-MICRO_LOOKBACK_SECONDS, claim),
            ).fetchone()
        quality = classify_ws3_preclaim_quality_nonlive(
            pair=pair, claim_epoch=claim, source_epoch=source_epoch,
            price_row=price, micro_row=micro,
        )
        quality["bot_api_ack_delay_sec"] = ack - claim
        entries.append(quality)
        counts[quality["evidence_tier"]] += 1
        counts[quality["micro_status"]] += 1
    return {
        "status": ("M05_WS3_ASOF_QUALITY_REVIEW_READONLY"
                   if entries else "M05_WS3_NO_ACK_IN_WINDOW_READONLY"),
        "window_start_wib": datetime.fromtimestamp(start_epoch, WIB).isoformat(),
        "window_end_wib": datetime.fromtimestamp(end_epoch, WIB).isoformat(),
        "ws3_acked_reviewed": len(entries),
        "evidence_tier_counts": {
            tier: counts[tier] for tier in (
                "SOURCE_UNVERIFIED", "PRICE_ONLY", "BOOK_ONLY",
                "TRADE_ONLY", "TRADE_BOOK_OBSERVED"
            )
        },
        "rest_not_reconstructed": counts["REST_NOT_RECONSTRUCTED"],
        "ws3_not_acked": counts["WS3_NOT_ACKED"],
        "source_archive_available": price_tables,
        "micro_archive_available": micro_table,
        "events": entries,
        "g1_verified": False,
        "m04_actual_role_responses_verified": False,
        "m05_alert_filters_changed": False,
        "final_decision": "REJECT",
        "execution_allowed": False,
        "orders_sent": 0,
        "telegram_messages_sent_by_audit": 0,
        "paid_ai_calls_by_audit": 0,
    }
