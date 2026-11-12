"""Opt-in ORIGINAL M03 WS3 micro research archive (not live trading).

Receives an ALREADY COLLECTED original WS3 snapshot, never creates a socket,
API request, worker, Telegram message, AI call, order or new account DB.
Separate private 72h research table. No original 2h scanner retention change.
Missing pairs/sides are UNKNOWN; source-reported sides are not aggressor proof.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3

from .m03_ws3_evidence_archive_nonlive import (
    DEFAULT_EVIDENCE_DB, PAIR, RETENTION_SECONDS, _prepare_private_target,
)
from .scanner import MAIN_EXCLUDED_PAIRS

MAX_MICRO_PAIRS_PER_CYCLE = 2
MAX_MICRO_TOTAL_ROWS = 20000


def _number(raw: object, *, positive: bool = False) -> str:
    if (type(raw) not in (str, int, Decimal)
            or (type(raw) is str and len(raw) > 64)):
        raise ValueError("M03_MICRO_RESEARCH_NUMBER_INVALID")
    try:
        num = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ValueError("M03_MICRO_RESEARCH_NUMBER_INVALID") from None
    if (not num.is_finite() or abs(num) >= Decimal("1e25")
            or (num <= 0 if positive else num < 0)):
        raise ValueError("M03_MICRO_RESEARCH_NUMBER_INVALID")
    return str(num)


def _asof_epoch(raw: object, now: int) -> int | None:
    if raw is None:
        return None
    if type(raw) is not int or not 0 <= now - raw <= 90:
        raise ValueError("M03_MICRO_RESEARCH_EPOCH_INVALID")
    return raw


def _checked_micro(row: dict, now: int, minute: int) -> tuple | None:
    if not isinstance(row, dict):
        raise ValueError("M03_MICRO_RESEARCH_ROW_NOT_MAPPING")
    pair = row.get("pair")
    if (type(pair) is not str or PAIR.fullmatch(pair) is None
            or pair in MAIN_EXCLUDED_PAIRS
            or row.get("g1_verified") is not False
            or row.get("aggressor_side_verified") is not False
            or row.get("source_coverage_complete") is not False
            or row.get("execution_allowed") is not False):
        raise ValueError("M03_MICRO_RESEARCH_SOURCE_AUTHORITY_INVALID")
    frames = row.get("trade_frames")
    if type(frames) is not int or not 0 <= frames <= 600:
        raise ValueError("M03_MICRO_RESEARCH_FRAMES_INVALID")
    flags = []
    for name in ("trade_gap_observed", "trade_truncated",
                 "source_malformed"):
        val = row.get(name)
        if type(val) is not bool:
            raise ValueError("M03_MICRO_RESEARCH_FLAG_INVALID")
        flags.append(int(val))
    last_trade = _asof_epoch(row.get("last_trade_epoch"), now)
    book_time = _asof_epoch(row.get("book_local_epoch"), now)
    trade_offset = row.get("trade_offset")
    book_offset = row.get("book_offset")
    for val in (trade_offset, book_offset):
        if val is not None and (type(val) is not int or val < 0):
            raise ValueError("M03_MICRO_RESEARCH_OFFSET_INVALID")
    buys = sells = buy_idr = sell_idr = None
    if frames:
        buys = row.get("source_buy_count")
        sells = row.get("source_sell_count")
        if (type(buys) is not int or type(sells) is not int
                or not 0 <= buys + sells <= 1000 or last_trade is None):
            raise ValueError("M03_MICRO_RESEARCH_TRADE_INVALID")
        buy_idr = _number(row.get("source_buy_idr"))
        sell_idr = _number(row.get("source_sell_idr"))
    book = row.get("book")
    bid = ask = spread = bid_depth = ask_depth = imbalance = None
    if book is not None:
        if (not isinstance(book, dict) or book.get("pair") != pair
                or book_time is None or book_offset is None):
            raise ValueError("M03_MICRO_RESEARCH_BOOK_INVALID")
        bid = _number(book.get("best_bid_idr"), positive=True)
        ask = _number(book.get("best_ask_idr"), positive=True)
        spread = _number(book.get("spread_bps"))
        bid_depth = _number(book.get("bid_depth_top5_idr"))
        ask_depth = _number(book.get("ask_depth_top5_idr"))
        imbalance_raw = book.get("book_imbalance_top5_ratio")
        # The official book parser returns None when ask depth is zero.
        # Preserve UNKNOWN instead of fabricating an imbalance ratio.
        imbalance = (_number(imbalance_raw)
                     if imbalance_raw is not None else None)
        b, a = Decimal(bid), Decimal(ask)
        expected = (a-b) * Decimal("10000") / ((a+b)/2)
        if b >= a or abs(expected - Decimal(spread)) >= Decimal("0.05"):
            raise ValueError("M03_MICRO_RESEARCH_BOOK_SPREAD_INCONSISTENT")
    # Zero frames with absent book mean no actual source. Do not insert
    # a fabricated zero-trade or empty-book historical observation.
    if frames == 0 and book is None:
        return None
    return (
        pair, minute, now, frames, buys, sells, buy_idr, sell_idr,
        last_trade, trade_offset, *flags[:2], book_offset, book_time,
        bid, ask, spread, bid_depth, ask_depth, imbalance, flags[2],
        0, 0, 0,
    )


def archive_ws3_micro_research_nonlive(
        snapshot: dict, *, now_epoch: int,
        evidence_db: Path = DEFAULT_EVIDENCE_DB) -> dict:
    """Best-effort isolated micro shadow, explicit caller opt-in required.

    This function performs bounded LOCAL research DB writes. Do not call
    it unless both original source archive and separate micro opt-in are YES.
    """
    if (not isinstance(snapshot, dict)
            or snapshot.get("status") != "M03_WS3_PUBLIC_SUMMARY_OBSERVED"
            or type(now_epoch) is not int or now_epoch <= 0
            or type(snapshot.get("microstructure")) is not list
            or len(snapshot["microstructure"]) > MAX_MICRO_PAIRS_PER_CYCLE):
        raise ValueError("M03_MICRO_RESEARCH_SNAPSHOT_INVALID")
    minute = now_epoch // 60 * 60
    records = []
    for row in snapshot["microstructure"]:
        checked = _checked_micro(row, now_epoch, minute)
        if checked is not None:
            records.append(checked)
    if len({x[0] for x in records}) != len(records):
        raise ValueError("M03_MICRO_RESEARCH_DUPLICATE_PAIR")
    path = Path(evidence_db)
    _prepare_private_target(path)
    with sqlite3.connect(str(path), timeout=3) as db:
        db.execute("PRAGMA busy_timeout=3000")
        db.execute("BEGIN IMMEDIATE")
        db.execute("""
            CREATE TABLE IF NOT EXISTS ws3_micro_research_evidence (
                pair TEXT NOT NULL, minute_epoch INTEGER NOT NULL,
                recorded_local_epoch INTEGER NOT NULL,
                trade_frames INTEGER NOT NULL,
                source_buy_count INTEGER, source_sell_count INTEGER,
                source_buy_idr TEXT, source_sell_idr TEXT,
                last_trade_epoch INTEGER, trade_offset INTEGER,
                trade_gap_observed INTEGER NOT NULL,
                trade_truncated INTEGER NOT NULL,
                book_offset INTEGER, book_local_epoch INTEGER,
                best_bid_idr TEXT, best_ask_idr TEXT, spread_bps TEXT,
                bid_depth_top5_idr TEXT, ask_depth_top5_idr TEXT,
                book_imbalance_top5_ratio TEXT, source_malformed INTEGER NOT NULL,
                source_complete INTEGER NOT NULL CHECK(source_complete=0),
                aggressor_verified INTEGER NOT NULL CHECK(aggressor_verified=0),
                g1_verified INTEGER NOT NULL CHECK(g1_verified=0),
                PRIMARY KEY(pair,minute_epoch)
            )
        """)
        db.execute(
            "DELETE FROM ws3_micro_research_evidence WHERE minute_epoch < ?",
            (minute - RETENTION_SECONDS,),
        )
        db.executemany("""
            INSERT OR IGNORE INTO ws3_micro_research_evidence
            (pair,minute_epoch,recorded_local_epoch,trade_frames,
             source_buy_count,source_sell_count,source_buy_idr,source_sell_idr,
             last_trade_epoch,trade_offset,trade_gap_observed,trade_truncated,
             book_offset,book_local_epoch,best_bid_idr,best_ask_idr,spread_bps,
             bid_depth_top5_idr,ask_depth_top5_idr,book_imbalance_top5_ratio,
             source_malformed,source_complete,aggressor_verified,g1_verified)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, records)
        db.execute("""
            DELETE FROM ws3_micro_research_evidence WHERE rowid IN (
                SELECT rowid FROM ws3_micro_research_evidence
                ORDER BY minute_epoch,pair LIMIT (
                    SELECT MAX(COUNT(*)-?,0) FROM ws3_micro_research_evidence
                )
            )
        """, (MAX_MICRO_TOTAL_ROWS,))
    return {
        "status": "M03_WS3_MICRO_RESEARCH_ARCHIVE_RECORDED",
        "source_micro_pairs_selected": len(snapshot["microstructure"]),
        "source_micro_evidence_pairs_attempted": len(records),
        "source_coverage_complete": False,
        "aggressor_verified": False,
        "three_ai_roles_called": False,
        "execution_allowed": False,
        "orders_sent": 0,
        "telegram_messages_sent": 0,
        "extra_exchange_requests": 0,
    }
