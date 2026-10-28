"""Persistent and idempotent REJECT audit log. No private/account information."""
from __future__ import annotations

import sqlite3
import hashlib
import json
from decimal import Decimal
from dataclasses import dataclass
from contextlib import contextmanager
from typing import Iterator
from pathlib import Path

from .contracts import FinalDecision
from .public_market import Candle, PublicCandleHistory, PublicDataError


class DecisionConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeepWindow:
    pair_ids: tuple[str, ...]
    starting_index: int
    next_index: int
    universe_digest: str
    replayed: bool = False


class DecisionJournal:
    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS decisions (
                    trace_id TEXT PRIMARY KEY,
                    final_decision TEXT NOT NULL CHECK(final_decision IN ('PASS', 'REJECT')),
                    reason TEXT NOT NULL,
                    schema_version INTEGER NOT NULL
                )"""
            )
            con.execute(
                """CREATE TABLE IF NOT EXISTS deep_scan_cursor (
                    lane TEXT PRIMARY KEY,
                    next_index INTEGER NOT NULL,
                    last_started_epoch INTEGER NOT NULL
                )"""
            )
            con.execute(
                """CREATE TABLE IF NOT EXISTS deep_scan_reservation (
                    trace_id TEXT PRIMARY KEY,
                    universe_digest TEXT NOT NULL,
                    selected_json TEXT NOT NULL,
                    starting_index INTEGER NOT NULL,
                    next_index INTEGER NOT NULL
                )"""
            )
            # M03-L: only untrusted, closed PUBLIC research OHLC. Never use
            # these rows to claim full trade-tape coverage or G1 origin.
            con.execute(
                """CREATE TABLE IF NOT EXISTS public_ohlc_1m (
                    pair TEXT NOT NULL,
                    start_epoch INTEGER NOT NULL,
                    open TEXT NOT NULL,
                    high TEXT NOT NULL,
                    low TEXT NOT NULL,
                    close TEXT NOT NULL,
                    volume TEXT NOT NULL,
                    row_digest TEXT NOT NULL,
                    PRIMARY KEY(pair, start_epoch)
                )"""
            )
            con.execute(
                """CREATE TABLE IF NOT EXISTS public_ohlc_batches (
                    pair TEXT NOT NULL,
                    from_epoch INTEGER NOT NULL,
                    to_epoch INTEGER NOT NULL,
                    count INTEGER NOT NULL,
                    observed_epoch INTEGER NOT NULL,
                    exchange_epoch INTEGER NOT NULL,
                    batch_digest TEXT NOT NULL,
                    provenance TEXT NOT NULL,
                    PRIMARY KEY(pair, from_epoch, to_epoch)
                )"""
            )

            # M03-N: first ticker_all quote sampled each minute, per pair.
            # These are observed snapshots, NOT closed 1m candles or trades.
            con.execute(
                """CREATE TABLE IF NOT EXISTS public_ticker_minute_samples (
                    pair TEXT NOT NULL,
                    minute_epoch INTEGER NOT NULL,
                    observed_epoch INTEGER NOT NULL,
                    ticker_epoch INTEGER NOT NULL,
                    last_idr TEXT NOT NULL,
                    volume_24h_idr TEXT NOT NULL,
                    spread_bps TEXT NOT NULL,
                    PRIMARY KEY(pair, minute_epoch)
                )"""
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_public_ticker_minute_latest "
                "ON public_ticker_minute_samples(pair,minute_epoch DESC)"
            )


    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self._db_path, timeout=10)
        try:
            with con:
                yield con
        finally:
            con.close()

    def record_reject(self, trace_id: str, reason: str) -> None:
        if not trace_id or not trace_id.strip() or not reason:
            raise ValueError("trace_id and reason required")
        # A real transaction with one UNIQUE trace prevents conflicting replay.
        with self._connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO decisions(trace_id, final_decision, reason, schema_version) VALUES (?, ?, ?, 1)",
                (trace_id, FinalDecision.REJECT.value, reason),
            )
            row = con.execute(
                "SELECT final_decision, reason FROM decisions WHERE trace_id = ?",
                (trace_id,),
            ).fetchone()
            if row != (FinalDecision.REJECT.value, reason):
                raise DecisionConflictError("conflicting replay for trace_id")

    def count(self) -> int:
        with self._connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0])

    def reserve_deep_window(
        self, trace_id: str, ranked_pairs: tuple[str, ...],
        *, observed_epoch: int, batch_size: int = 2, min_interval_seconds: int = 12,
    ) -> DeepWindow:
        """Atomically rotate public-only deep candidates across restarts.

        The cursor tracks scheduling, NOT evidence freshness or authorization.
        Reusing a trace id with a changed universe fails closed.
        """
        if not isinstance(trace_id, str) or not trace_id.strip():
            raise ValueError("INVALID_SCAN_TRACE")
        if (not ranked_pairs or len(set(ranked_pairs)) != len(ranked_pairs)
                or any(not isinstance(p, str) or not p.endswith("_idr") for p in ranked_pairs)):
            raise ValueError("INVALID_SCAN_UNIVERSE")
        if isinstance(observed_epoch, bool) or not isinstance(observed_epoch, int) or observed_epoch <= 0:
            raise ValueError("INVALID_SCAN_EPOCH")
        if not 1 <= batch_size <= 2 or min_interval_seconds < 1:
            raise ValueError("INVALID_SCAN_BUDGET")
        digest = hashlib.sha256(json.dumps(ranked_pairs, separators=(",", ":")).encode()).hexdigest()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute(
                "SELECT universe_digest, selected_json, starting_index, next_index "
                "FROM deep_scan_reservation WHERE trace_id = ?", (trace_id,)
            ).fetchone()
            if existing is not None:
                if existing[0] != digest:
                    raise DecisionConflictError("DEEP_TRACE_UNIVERSE_CHANGED")
                return DeepWindow(tuple(json.loads(existing[1])), existing[2], existing[3], digest, True)
            cursor = con.execute(
                "SELECT next_index, last_started_epoch FROM deep_scan_cursor WHERE lane='MAIN'"
            ).fetchone()
            if cursor is None:
                start = 0
            else:
                last_start = cursor[1]
                if observed_epoch - last_start < min_interval_seconds:
                    raise ValueError("DEEP_SCAN_COOLDOWN")
                start = cursor[0] % len(ranked_pairs)
            count = min(batch_size, len(ranked_pairs))
            chosen = tuple(ranked_pairs[(start + i) % len(ranked_pairs)] for i in range(count))
            nxt = (start + count) % len(ranked_pairs)
            con.execute(
                "INSERT INTO deep_scan_reservation(trace_id, universe_digest, selected_json, starting_index, next_index) "
                "VALUES (?, ?, ?, ?, ?)",
                (trace_id, digest, json.dumps(chosen), start, nxt),
            )
            con.execute(
                "INSERT INTO deep_scan_cursor(lane, next_index, last_started_epoch) "
                "VALUES ('MAIN', ?, ?) ON CONFLICT(lane) DO UPDATE SET "
                "next_index=excluded.next_index, last_started_epoch=excluded.last_started_epoch",
                (nxt, observed_epoch),
            )
            return DeepWindow(chosen, start, nxt, digest)


    @staticmethod
    def _public_ohlc_values(candle: Candle) -> tuple[str, str, str, str, str]:
        # Decimal canonical string avoids a false conflict between e.g.
        # 100.00 and 100 while preserving the numeric identity.
        return tuple(format(x.normalize(), "f") for x in (
            candle.open, candle.high, candle.low, candle.close, candle.volume,
        ))

    @classmethod
    def _public_ohlc_digest(cls, candle: Candle) -> str:
        data = ",".join((str(candle.start), *cls._public_ohlc_values(candle)))
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    def earliest_public_ohlc_epoch(self, pair: str) -> int | None:
        if not isinstance(pair, str) or not pair.endswith("_idr"):
            raise ValueError("INVALID_RESEARCH_PAIR")
        with self._connect() as con:
            row = con.execute(
                "SELECT MIN(start_epoch) FROM public_ohlc_1m WHERE pair = ?",
                (pair,),
            ).fetchone()
        return row[0] if row[0] is not None else None

    def archive_public_ohlc_history(self, history: PublicCandleHistory) -> dict:
        """Atomic immutable-as-observed PUBLIC candle archive with dedup.

        A changed same-minute OHLC rejects the whole batch. Hashes detect
        accidental corruption; they are NOT exchange cryptographic signatures.
        """
        if (not isinstance(history, PublicCandleHistory)
                or not isinstance(history.pair, str)
                or not history.pair.endswith("_idr")
                or history.source_trust_verified is not False
                or history.trade_history_complete is not False
                or history.provenance != "INDODAX_PUBLIC_OHLC_V2_UNATTESTED"
                or history.from_epoch <= 0
                or history.to_epoch - history.from_epoch != 60*len(history.candles)
                or not 90 <= len(history.candles) <= 360
                or abs(history.observed_epoch-history.exchange_epoch) > 30
                or history.to_epoch > min(history.observed_epoch,history.exchange_epoch)
                or history.to_epoch % 60):
            raise PublicDataError("INVALID_ARCHIVE_PUBLIC_SOURCE")
        rows = history.candles
        for i, candle in enumerate(rows):
            if (candle.start != history.from_epoch + 60*i
                    or candle.interval != 60
                    or candle.low < 0
                    or candle.volume < 0
                    or candle.low > min(candle.open,candle.close)
                    or candle.high < max(candle.open,candle.close)
                    or any(not x.is_finite() for x in (
                        candle.open,candle.high,candle.low,candle.close,candle.volume
                    ))):
                raise PublicDataError("INVALID_ARCHIVE_1M_CONTINUITY")
        digests = tuple(self._public_ohlc_digest(c) for c in rows)
        batch_digest = hashlib.sha256("\n".join(digests).encode("utf-8")).hexdigest()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            previous = con.execute(
                "SELECT count,batch_digest FROM public_ohlc_batches "
                "WHERE pair=? AND from_epoch=? AND to_epoch=?",
                (history.pair,history.from_epoch,history.to_epoch),
            ).fetchone()
            if previous is not None and previous != (len(rows),batch_digest):
                raise PublicDataError("RESEARCH_ARCHIVE_BATCH_CONFLICT")
            added = 0
            for candle,digest in zip(rows,digests):
                existing = con.execute(
                    "SELECT open,high,low,close,volume,row_digest "
                    "FROM public_ohlc_1m WHERE pair=? AND start_epoch=?",
                    (history.pair,candle.start),
                ).fetchone()
                canonical = (*self._public_ohlc_values(candle),digest)
                if existing is not None:
                    if existing != canonical:
                        raise PublicDataError("RESEARCH_ARCHIVE_CANDLE_CONFLICT")
                    continue
                con.execute(
                    "INSERT INTO public_ohlc_1m("
                    "pair,start_epoch,open,high,low,close,volume,row_digest"
                    ") VALUES (?,?,?,?,?,?,?,?)",
                    (history.pair,candle.start,*canonical),
                )
                added += 1
            if previous is None:
                con.execute(
                    "INSERT INTO public_ohlc_batches("
                    "pair,from_epoch,to_epoch,count,observed_epoch,"
                    "exchange_epoch,batch_digest,provenance"
                    ") VALUES (?,?,?,?,?,?,?,?)",
                    (history.pair,history.from_epoch,history.to_epoch,
                     len(rows),history.observed_epoch,history.exchange_epoch,
                     batch_digest,history.provenance),
                )
        return {
            "pair": history.pair,
            "from_epoch": history.from_epoch,
            "to_epoch": history.to_epoch,
            "closed_1m_rows_received": len(rows),
            "new_rows_committed": added,
            "repeated_rows_unchanged": len(rows)-added,
            "batch_sha256_local_not_exchange_signed": batch_digest,
            "source_trust_verified": False,
            "g1_verified": False,
        }

    def public_ohlc_archive_summary(self, pair: str) -> dict:
        if not isinstance(pair,str) or not pair.endswith("_idr"):
            raise ValueError("INVALID_RESEARCH_PAIR")
        with self._connect() as con:
            candle = con.execute(
                "SELECT COUNT(*), MIN(start_epoch), MAX(start_epoch) "
                "FROM public_ohlc_1m WHERE pair=?", (pair,),
            ).fetchone()
            batches = con.execute(
                "SELECT COUNT(*) FROM public_ohlc_batches WHERE pair=?",
                (pair,),
            ).fetchone()[0]
        return {
            "pair": pair,
            "stored_1m_rows": candle[0],
            "oldest_1m_start_epoch": candle[1],
            "newest_1m_start_epoch": candle[2],
            "immutable_source_window_count": batches,
            "full_trade_tape_proven": False,
            "source_trust_verified": False,
        }

    def recent_contiguous_public_ohlc_history(
        self, pair: str, *, max_minutes: int = 4320,
    ) -> PublicCandleHistory | None:
        """Return newest contiguous archived suffix, never bridge a gap.

        Rows are checked against locally recorded hashes. This guards
        accidental storage corruption, not malicious replacement of the DB.
        """
        if (not isinstance(pair,str) or not pair.endswith("_idr")
                or type(max_minutes) is not int or not 90 <= max_minutes <= 4320):
            raise ValueError("INVALID_RESEARCH_ARCHIVE_WINDOW")
        with self._connect() as con:
            items = con.execute(
                "SELECT start_epoch,open,high,low,close,volume,row_digest "
                "FROM public_ohlc_1m WHERE pair=? "
                "ORDER BY start_epoch DESC LIMIT ?",(pair,max_minutes),
            ).fetchall()
            origin = con.execute(
                "SELECT observed_epoch,exchange_epoch FROM public_ohlc_batches "
                "WHERE pair=? ORDER BY observed_epoch DESC,to_epoch DESC LIMIT 1",
                (pair,),
            ).fetchone()
        if not items:
            return None
        kept = []
        expected = items[0][0]
        for start,op,high,low,close,volume,digest in items:
            if start != expected:
                break
            candle = Candle(start,60,Decimal(op),Decimal(high),Decimal(low),
                            Decimal(close),Decimal(volume))
            if self._public_ohlc_digest(candle) != digest:
                raise PublicDataError("RESEARCH_ARCHIVE_CANDLE_DIGEST_MISMATCH")
            kept.append(candle)
            expected -= 60
        if len(kept) < 90:
            return None
        if origin is None:
            raise PublicDataError("RESEARCH_ARCHIVE_PROVENANCE_MISSING")
        chronological = tuple(reversed(kept))
        return PublicCandleHistory(
            pair=pair,observed_epoch=origin[0],exchange_epoch=origin[1],
            from_epoch=chronological[0].start,
            to_epoch=chronological[-1].start+60,
            candles=chronological,
        )


    def record_and_assess_public_ticker_momentum(
        self, screening,
    ) -> dict[str, dict]:
        """M03-N: atomically archive ALL eligible IDR first-seen minute quotes.

        No full-minute tape or volume delta from sliding 24h turnover; BUY/SELL
        pressure must be separately observed in existing closed-candle deep path.
        Strictly require successive minute observations for each horizon.
        An intra-minute rescan NEVER overwrites the earliest stored sample.
        """
        from .scanner import ScannerResult, MAIN_EXCLUDED_PAIRS
        if (not isinstance(screening, ScannerResult)
                or screening.scan_context is None
                or not screening.universe
                or not isinstance(screening.observed_epoch, int)
                or screening.observed_epoch <= 0
                or screening.scan_context.observed_epoch != screening.observed_epoch):
            raise PublicDataError("INVALID_MOMENTUM_SCREENING_CONTEXT")
        minute = screening.observed_epoch // 60 * 60
        if abs(screening.scan_context.exchange_epoch-screening.observed_epoch) > 30:
            raise PublicDataError("MOMENTUM_EXCHANGE_CLOCK_SKEW")
        if (len({x.pair for x in screening.universe}) != len(screening.universe)
                or any(x.pair in MAIN_EXCLUDED_PAIRS or
                       not x.pair.endswith("_idr")
                       for x in screening.universe)):
            raise PublicDataError("INVALID_MOMENTUM_UNIVERSE")
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            for item in screening.universe:
                if item.last_idr is None:
                    # Older fixture/previous M03 scanner inputs provide no
                    # last price: skip rather than manufacture midpoint price.
                    continue
                if (item.ticker_epoch // 60 != minute
                        or item.last_idr <= 0
                        or not item.last_idr.is_finite()
                        or item.spread_bps < 0
                        or not item.spread_bps.is_finite()
                        or item.volume_idr_24h <= 0
                        or not item.volume_idr_24h.is_finite()):
                    continue
                con.execute(
                    "INSERT OR IGNORE INTO public_ticker_minute_samples("
                    "pair,minute_epoch,observed_epoch,ticker_epoch,last_idr,"
                    "volume_24h_idr,spread_bps) VALUES (?,?,?,?,?,?,?)",
                    (item.pair,minute,screening.observed_epoch,
                     item.ticker_epoch,str(item.last_idr),
                     str(item.volume_idr_24h),str(item.spread_bps)),
                )
        reports = {}
        with self._connect() as con:
            for item in screening.universe:
                # Never use stale earlier-minute samples as current momentum.
                rows = con.execute(
                    "SELECT minute_epoch,observed_epoch,last_idr,spread_bps "
                    "FROM public_ticker_minute_samples "
                    "WHERE pair=? AND minute_epoch BETWEEN ? AND ? "
                    "ORDER BY minute_epoch DESC LIMIT 61",
                    (item.pair,minute-3600,minute),
                ).fetchall()
                if not rows or rows[0][0] != minute:
                    reports[item.pair] = {
                        "status": "WARMUP_OR_STALE_TICKER",
                        "minute_epoch": minute,
                        "return_1m_pct": None,
                        "return_5m_pct": None,
                        "return_15m_pct": None,
                        "return_60m_pct": None,
                        "source_trust_verified": False,
                        "trade_signal": False,
                    }
                    continue
                consecutive = []
                for row in rows:
                    if row[0] != minute - 60 * len(consecutive):
                        break
                    price = Decimal(row[2])
                    if not price.is_finite() or price <= 0:
                        raise PublicDataError("CORRUPT_MOMENTUM_STORED_PRICE")
                    consecutive.append(price)
                def ret(lag):
                    if len(consecutive) <= lag:
                        return None
                    return (consecutive[0]/consecutive[lag]-1)*Decimal(100)
                r1,r5,r15,r60 = (ret(x) for x in (1,5,15,60))
                previous5 = (
                    (consecutive[5]/consecutive[10]-1)*Decimal(100)
                    if len(consecutive)>10 else None
                )
                last_spread = Decimal(rows[0][3])
                if not last_spread.is_finite() or last_spread < 0:
                    raise PublicDataError("CORRUPT_MOMENTUM_STORED_SPREAD")
                acceleration = r5-previous5 if r5 is not None and previous5 is not None else None
                if r5 is None or r1 is None:
                    status = "WARMUP_OR_GAPPED_MINUTE_SAMPLES"
                elif r5 < 0 and r1 > 0:
                    status = "CONFIRMATION_PENDING"
                elif (acceleration is not None and r5 > 0
                      and r1 > 0 and acceleration > 0
                      and last_spread <= Decimal("80")):
                    status = "EARLY_WATCH_UNCALIBRATED"
                elif (r5 > 0 and (r1 <= 0 or
                      (acceleration is not None and acceleration <= 0))):
                    status = "EXHAUSTION_CAUTION"
                elif r5 > 0 and r1 > 0:
                    status = "CONFIRMATION_PENDING"
                else:
                    status = "OBSERVED_NO_ACCELERATION"
                reports[item.pair] = {
                    "status": status,
                    "minute_epoch": minute,
                    "first_observed_epoch_this_minute": rows[0][1],
                    "consecutive_minute_samples": len(consecutive),
                    "return_1m_pct": None if r1 is None else str(r1),
                    "return_5m_pct": None if r5 is None else str(r5),
                    "return_15m_pct": None if r15 is None else str(r15),
                    "return_60m_pct": None if r60 is None else str(r60),
                    "acceleration_current_5m_vs_prior_5m_pct_points":
                        None if acceleration is None else str(acceleration),
                    "minute_volume_confirmed": False,
                    "volume_24h_is_not_1m_volume": True,
                    "buy_sell_flow_complete": False,
                    "ticker_is_closed_candle": False,
                    "source_trust_verified": False,
                    "g1_verified": False,
                    "trade_signal": False,
                }
        return reports
