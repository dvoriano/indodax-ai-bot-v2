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
                """CREATE TABLE IF NOT EXISTS pipeline_stage_observations (
                    trace_id TEXT PRIMARY KEY,
                    stage TEXT NOT NULL,
                    reason TEXT NOT NULL
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
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_public_ticker_minute_retention "
                "ON public_ticker_minute_samples(minute_epoch)"
            )

            # M03-N: first observed real DEPTH diagnostic per pair/minute.
            # No implied full book, exchange authentication or filled orders.
            con.execute(
                """CREATE TABLE IF NOT EXISTS public_deep_wall_samples (
                   pair TEXT NOT NULL,
                   minute_epoch INTEGER NOT NULL,
                   observed_epoch INTEGER NOT NULL,
                   closed_epoch INTEGER NOT NULL,
                   book_status TEXT NOT NULL,
                   bid_wall_price TEXT NOT NULL,
                   bid_wall_idr TEXT NOT NULL,
                   bid_near_idr TEXT NOT NULL,
                   ask_near_idr TEXT NOT NULL,
                   best_bid TEXT NOT NULL,
                   best_ask TEXT NOT NULL,
                   visible_buy_idr TEXT NOT NULL,
                   visible_sell_idr TEXT NOT NULL,
                   PRIMARY KEY(pair, minute_epoch)
                )"""
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_public_wall_retention "
                "ON public_deep_wall_samples(minute_epoch)"
            )
            # Candidate features are frozen BEFORE future labels arrive.
            # Controls permit comparing winners vs non-winners, not just pumps.
            con.execute(
                """CREATE TABLE IF NOT EXISTS public_surge_cases (
                   pair TEXT NOT NULL,
                   minute_epoch INTEGER NOT NULL,
                   observed_epoch INTEGER NOT NULL,
                   cohort TEXT NOT NULL,
                   entry_price_idr TEXT NOT NULL,
                   features_json TEXT NOT NULL,
                   future_60m_return_pct TEXT,
                   future_24h_return_pct TEXT,
                   PRIMARY KEY(pair, minute_epoch)
                )"""
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_public_surge_retention "
                "ON public_surge_cases(minute_epoch)"
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

    def record_stage_observation(self, trace_id: str, stage: str,
                                 reason: str) -> None:
        """Record a pre-final observation without inventing an owner decision.

        Idempotent by trace ID. REJECT/PASS is reserved for a real Final
        Decision; a missing or degraded stage remains a diagnostic fact.
        """
        if (not trace_id or not trace_id.strip() or not stage
                or not stage.strip() or not reason or not reason.strip()):
            raise ValueError("trace_id, stage and reason required")
        with self._connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO pipeline_stage_observations"
                "(trace_id,stage,reason) VALUES (?,?,?)",
                (trace_id, stage, reason),
            )
            row = con.execute(
                "SELECT stage,reason FROM pipeline_stage_observations "
                "WHERE trace_id=?", (trace_id,),
            ).fetchone()
            if row != (stage, reason):
                raise DecisionConflictError("conflicting stage observation replay")

    def stage_observation_count(self) -> int:
        with self._connect() as con:
            return int(con.execute(
                "SELECT COUNT(*) FROM pipeline_stage_observations"
            ).fetchone()[0])

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
            # Keep a bounded 24h source-sample buffer (61 consecutive
            # minutes suffice for the 60m context); prevent unbounded VPS DB.
            # These are not used as a certified historical trade ledger.
            con.execute(
                "DELETE FROM public_ticker_minute_samples WHERE minute_epoch < ?",
                (minute - 86400,),
            )
            for item in screening.universe:
                if item.last_idr is None:
                    # Older fixture/previous M03 scanner inputs provide no
                    # last price: skip rather than manufacture midpoint price.
                    continue
                # Both operands are epoch seconds aligned to minute start.
                # Comparing ticker_epoch // 60 (minute index) to minute
                # (epoch seconds) silently marked EVERY fresh ticker stale.
                if (item.ticker_epoch // 60 * 60 != minute
                        or item.ticker_epoch > screening.observed_epoch
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
                    "AND observed_epoch <= ? "
                    "ORDER BY minute_epoch DESC LIMIT 61",
                    (item.pair,minute-3600,minute,screening.observed_epoch),
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
        # Frozen early-warning/control cases and only matured future outcomes.
        # Same persistent scanner/journal path. No network/AI/trade side effects.
        self.record_public_surge_experience(screening, reports)
        return reports


    def record_and_compare_public_wall(self, candidate, observation) -> dict:
        """Persist real two-sided depth evidence and compare DISTINCT minutes.

        A moved/disappeared BUY wall is ambiguous: cancel, fill, or refresh
        cannot be distinguished from two public snapshots.
        """
        from .scanner import MAIN_EXCLUDED_PAIRS
        pair = candidate.pair
        source = observation.snapshot.source
        diag = candidate.book_wall_diagnostic
        now = source.observed_epoch
        minute = now // 60 * 60
        base = {
            "status": "BID_WALL_PERSISTENCE_UNPROVEN",
            "pair": pair,
            "minute_epoch": minute,
            "previous_minute_epoch": None,
            "elapsed_seconds": None,
            "bid_wall_retained_observed": False,
            "bid_wall_persistence_proven": False,
            "cancel_vs_fill_distinguishable": False,
            "source_verified": False,
            "trade_signal": False,
            "execution_allowed": False,
        }
        if (not isinstance(diag, dict) or
                not isinstance(pair, str) or not pair.endswith("_idr") or
                pair in MAIN_EXCLUDED_PAIRS or observation.pair != pair or
                source.pair != pair or type(now) is not int or now <= 0 or
                type(source.closed_1m_epoch) is not int or
                now < source.closed_1m_epoch or
                now - source.closed_1m_epoch > 120):
            return dict(base, status="WALL_SOURCE_NOT_ALIGNED")
        # Rejected source still has diagnostic fields, but must never be
        # promoted into reliable wall continuity.
        if (diag.get("status") in (
                "BOOK_LEVELS_NOT_OBSERVED", "BOOK_TICKER_MISMATCH_NO_WATCH",
                "BOOK_LAST_CLOSE_DISLOCATION_NO_WATCH")
                or (candidate.closed_1m_epoch is not None and
                    candidate.closed_1m_epoch != source.closed_1m_epoch)):
            return dict(base, status="WALL_SOURCE_NOT_ALIGNED")
        try:
            values = [Decimal(diag[key]) for key in (
                "largest_bid_wall_price_idr", "largest_bid_wall_notional_idr",
                "bid_near_total_idr", "ask_near_total_idr",
            )]
            bid_price, bid_idr, near_bid, near_ask = values
            best_bid = observation.snapshot.best_bid_idr
            best_ask = observation.snapshot.best_ask_idr
            if (not all(x.is_finite() and x > 0 for x in (
                    *values, best_bid, best_ask))
                    or best_bid >= best_ask):
                return dict(base, status="WALL_INVALID_DEPTH_VALUES")
        except (KeyError, ValueError, TypeError, ArithmeticError):
            return dict(base, status="WALL_INVALID_DEPTH_VALUES")

        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "DELETE FROM public_deep_wall_samples WHERE minute_epoch < ?",
                (minute - 7*86400,),
            )
            # One initial snapshot each minute. Same-minute rescan is NOT
            # independent temporal evidence even when the book changed.
            con.execute(
                """INSERT OR IGNORE INTO public_deep_wall_samples
                (pair,minute_epoch,observed_epoch,closed_epoch,book_status,
                 bid_wall_price,bid_wall_idr,bid_near_idr,ask_near_idr,
                 best_bid,best_ask,visible_buy_idr,visible_sell_idr)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pair,minute,now,source.closed_1m_epoch,diag["status"],
                 str(bid_price),str(bid_idr),str(near_bid),str(near_ask),
                 str(best_bid),str(best_ask),
                 str(candidate.visible_buy_idr),str(candidate.visible_sell_idr)),
            )
            current = con.execute(
                "SELECT observed_epoch,bid_wall_price,bid_wall_idr,book_status,"
                "bid_near_idr,ask_near_idr,best_bid,visible_buy_idr,visible_sell_idr "
                "FROM public_deep_wall_samples WHERE pair=? AND minute_epoch=?",
                (pair,minute),
            ).fetchone()
            previous = con.execute(
                """SELECT minute_epoch,observed_epoch,bid_wall_price,
                          bid_wall_idr,book_status,bid_near_idr,ask_near_idr,
                          best_bid,visible_buy_idr,visible_sell_idr
                   FROM public_deep_wall_samples
                   WHERE pair=? AND minute_epoch < ?
                   ORDER BY minute_epoch DESC LIMIT 1""",
                (pair,minute),
            ).fetchone()
        if not previous:
            return base
        prior_minute, old_epoch, old_price_raw, old_idr_raw, old_status = previous[:5]
        elapsed = current[0] - old_epoch
        extra = {
            "previous_minute_epoch": prior_minute,
            "elapsed_seconds": elapsed,
            "previous_book_status": old_status,
        }
        # A repeated request in the same minute cannot create a second
        # observation, and a future/replayed source never proves persistence.
        if now != current[0]:
            return dict(base, **extra, status="WALL_SAME_MINUTE_REPLAY")
        if elapsed < 60 or elapsed > 300:
            return dict(base, **extra, status="WALL_OBSERVATION_GAP_OR_REPLAY")
        unsafe = {
            "WIDE_SPREAD_WALL_UNACTIONABLE",
            "BOOK_TICKER_MISMATCH_NO_WATCH",
            "BOOK_LAST_CLOSE_DISLOCATION_NO_WATCH",
        }
        if current[3] in unsafe or old_status in unsafe:
            return dict(base, **extra, status="WALL_SPREAD_OR_SOURCE_UNACTIONABLE")
        try:
            old_price, old_idr = Decimal(old_price_raw), Decimal(old_idr_raw)
            new_price, new_idr = Decimal(current[1]), Decimal(current[2])
            old_near_bid, old_near_ask, old_best = (
                Decimal(x) for x in previous[5:8]
            )
            new_near_bid, new_near_ask, new_best = (
                Decimal(x) for x in current[4:7]
            )
            values = (old_price, old_idr, new_price, new_idr,
                      old_near_bid, old_near_ask, old_best,
                      new_near_bid, new_near_ask, new_best)
            if any(not v.is_finite() or v <= 0 for v in values):
                raise ValueError("bad book row")
            old_ratio = old_near_bid/old_near_ask
            new_ratio = new_near_bid/new_near_ask
            observed_price_change = (new_best/old_best-1)*Decimal(100)
        except (ValueError, TypeError, ArithmeticError):
            return dict(base, **extra, status="WALL_INVALID_PRIOR_EVIDENCE")
        old_support = (
            old_status in ("BID_WALL_WATCH_UNCONFIRMED",
                           "PANIC_REBOUND_WATCH_UNCONFIRMED")
            and old_ratio >= Decimal("3")
        )
        new_support = (
            current[3] in ("BID_WALL_WATCH_UNCONFIRMED",
                           "PANIC_REBOUND_WATCH_UNCONFIRMED")
            and new_ratio >= Decimal("3")
        )
        moved_bps = abs(new_price-old_price)*Decimal(10000)/old_price
        retained = (
            old_support and new_support and moved_bps <= Decimal("50")
            and new_idr >= old_idr * Decimal("0.60")
        )
        if not old_support and not new_support:
            state = "NO_DOMINANT_BID_WALL_IN_EITHER_SNAPSHOT"
        elif not old_support and new_support:
            state = "BID_WALL_NEWLY_OBSERVED_UNCONFIRMED"
        elif retained:
            state = "BID_WALL_RETAINED_OBSERVED"
        else:
            # Withdrawal vs match/absorption is not identifiable from two
            # public snapshots and an incomplete visible trade sample.
            state = "BID_WALL_DISAPPEARED_OR_MOVED_UNKNOWN"
        return dict(
            base, **extra, status=state,
            bid_wall_retained_observed=retained,
            observed_wall_notional_change_pct=str((new_idr/old_idr-1)*Decimal(100)),
            wall_price_shift_bps=str(moved_bps),
            observed_best_bid_return_pct=str(observed_price_change),
            old_bid_ask_near_notional_ratio=str(old_ratio),
            new_bid_ask_near_notional_ratio=str(new_ratio),
            old_visible_buy_idr_sampled=previous[8],
            old_visible_sell_idr_sampled=previous[9],
            new_visible_buy_idr_sampled=current[7],
            new_visible_sell_idr_sampled=current[8],
            observations_compared=2,
            future_price_continuation_proven=False,
        )

    def record_public_surge_experience(self, screening, reports: dict) -> None:
        """Time-locked prospective casebook with matched non-pump controls.

        The label is ONLY the actually observed endpoint price at +60m/+24h,
        not the maximum future price nor a made-up 24h rally prediction.
        """
        if not screening.universe or not isinstance(reports, dict):
            return
        minute = screening.observed_epoch // 60 * 60
        # Five-minute source-aligned cohort. Controls prevent learning only
        # from winners. Limit rows and total history for a small VPS.
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "DELETE FROM public_surge_cases WHERE minute_epoch < ?",
                (minute-14*86400,),
            )
            for horizon,col in (
                (60,"future_60m_return_pct"),
                (1440,"future_24h_return_pct"),
            ):
                due = con.execute(
                    f"""SELECT c.pair,c.minute_epoch,c.entry_price_idr,
                               s.last_idr
                        FROM public_surge_cases c
                        JOIN public_ticker_minute_samples s
                          ON s.pair=c.pair AND
                             s.minute_epoch=c.minute_epoch+?
                        WHERE c.{col} IS NULL AND c.minute_epoch=?""",
                    (horizon*60,minute-horizon*60),
                ).fetchall()
                for pair,epoch,entry_raw,future_raw in due:
                    entry, future = Decimal(entry_raw), Decimal(future_raw)
                    if entry > 0 and future > 0 and all(
                        x.is_finite() for x in (entry,future)
                    ):
                        con.execute(
                            f"UPDATE public_surge_cases SET {col}=? "
                            "WHERE pair=? AND minute_epoch=?",
                            (str((future/entry-1)*Decimal(100)),pair,epoch),
                        )
            if minute % 300 != 0:
                # Scanner is normally at second 10, minute still 5m boundary.
                return
            chosen = []
            for item in screening.universe:
                m = reports.get(item.pair) or {}
                if (m.get("status") == "EARLY_WATCH_UNCALIBRATED"
                        and m.get("consecutive_minute_samples",0) >= 11):
                    chosen.append((item,m,"EARLY_WATCH"))
                    if len(chosen) == 20:
                        break
            # Avoid selecting only winners. 3 turnover-ranked non-alerts
            # each 5m form a time-matched baseline, not a trading signal.
            controls = 0
            for item in screening.universe:
                m = reports.get(item.pair) or {}
                if (controls >= 3 or item.pair in {x[0].pair for x in chosen}):
                    continue
                if (m.get("consecutive_minute_samples",0) >= 11 and
                        m.get("status") != "EARLY_WATCH_UNCALIBRATED"):
                    chosen.append((item,m,"CONTROL"))
                    controls += 1
            for item,m,cohort in chosen:
                if item.last_idr is None or item.last_idr <= 0 or not item.last_idr.is_finite():
                    continue
                # Quote must actually have been captured in THIS minute; not
                # a previous/out-of-sync ticker.
                observed = con.execute(
                    "SELECT last_idr,observed_epoch FROM public_ticker_minute_samples "
                    "WHERE pair=? AND minute_epoch=?",
                    (item.pair,minute),
                ).fetchone()
                if not observed:
                    continue
                from .scanner import surge_pattern_signature
                frozen = {
                    "label_asof_minute_epoch": minute,
                    "pattern_signature": surge_pattern_signature(m,item.spread_bps),
                    "cohort": cohort,
                    "momentum_status": m.get("status"),
                    "return_1m_pct": m.get("return_1m_pct"),
                    "return_5m_pct": m.get("return_5m_pct"),
                    "return_15m_pct": m.get("return_15m_pct"),
                    "return_60m_pct": m.get("return_60m_pct"),
                    "acceleration_5m_pct_points":
                        m.get("acceleration_current_5m_vs_prior_5m_pct_points"),
                    "spread_bps": str(item.spread_bps),
                    "volume_24h_idr": str(item.volume_idr_24h),
                    "true_minute_volume_known": False,
                    "complete_trade_tape_known": False,
                    "orderbook_wall_persistence_known": False,
                    "btc_regime_proven": False,
                    "future_return_known_at_entry": False,
                }
                con.execute(
                    """INSERT OR IGNORE INTO public_surge_cases
                       (pair,minute_epoch,observed_epoch,cohort,entry_price_idr,
                        features_json) VALUES (?,?,?,?,?,?)""",
                    (item.pair,minute,observed[1],cohort,observed[0],
                     json.dumps(frozen,sort_keys=True)),
                )

    def public_surge_casebook_summary(self) -> dict:
        """Describe forward outcomes for prospective as-of cohorts.

        Historical feature-pattern matches are NOT evidence of a repeatable
        advantage until independent walk-forward evaluation and fees.
        Missing +60m/+24h snapshots remain UNKNOWN, never negative labels.
        """
        from .scanner import surge_pattern_signature
        with self._connect() as con:
            rows = con.execute(
                """SELECT cohort,future_60m_return_pct,future_24h_return_pct,
                          features_json,minute_epoch,pair
                   FROM public_surge_cases
                   ORDER BY minute_epoch ASC,pair ASC"""
            ).fetchall()
        groups: dict[str, dict] = {}
        patterns: dict[str, dict] = {}
        for cohort,p60,p24,features_json,minute_epoch,pair in rows:
            g=groups.setdefault(cohort,{"cases":0,"matured_60m":0,
                                        "matured_24h":0,"up_20pct_24h":0,
                                        "up_50pct_24h":0,"up_100pct_24h":0,
                                        "down_10pct_24h":0,
                                        "up_2pct_60m":0,"up_8pct_60m":0,
                                        "up_11pct_60m":0,"down_2pct_60m":0,
                                        "_sum_return_60m":Decimal("0")})
            g["cases"]+=1
            try:
                features = json.loads(features_json)
                if not isinstance(features, dict):
                    features = {}
                signature = features.get("pattern_signature")
                if not isinstance(signature, str):
                    signature = surge_pattern_signature(
                        {
                            "return_5m_pct": features.get("return_5m_pct"),
                            "acceleration_current_5m_vs_prior_5m_pct_points":
                                features.get("acceleration_5m_pct_points"),
                        },
                        Decimal(features.get("spread_bps","NaN")),
                    )
            except (ValueError, TypeError, ArithmeticError):
                signature = None
            pattern = None
            if signature is not None:
                pattern = patterns.setdefault(signature, {
                    "cases":0,"matured_60m":0,"matured_24h":0,
                    "up_20pct_24h":0,"up_50pct_24h":0,"up_100pct_24h":0,
                    "down_10pct_24h":0,
                    "up_2pct_60m":0,"up_8pct_60m":0,
                    "up_11pct_60m":0,"down_2pct_60m":0,
                    "_sum_return_60m":Decimal("0"),
                    "_pair_set":set(),"_day_set":set(),
                    "_mature_pair_set":set(),"_mature_day_set":set(),
                })
                pattern["cases"] += 1
                pattern["_pair_set"].add(pair)
                pattern["_day_set"].add(minute_epoch//86400)
            if p60 is not None:
                # +60m endpoint is an observed quote, NOT an intrahour
                # peak or fee-adjusted filled exit.
                value60=Decimal(p60)
                if not value60.is_finite():
                    raise ValueError("INVALID_STORED_60M_OUTCOME")
                g["matured_60m"]+=1
                g["_sum_return_60m"]+=value60
                if pattern is not None:
                    pattern["matured_60m"]+=1
                    pattern["_sum_return_60m"]+=value60
                for threshold,field in (
                    (Decimal("2"),"up_2pct_60m"),
                    (Decimal("8"),"up_8pct_60m"),
                    (Decimal("11"),"up_11pct_60m"),
                ):
                    if value60 >= threshold:
                        g[field]+=1
                        if pattern is not None:
                            pattern[field]+=1
                if value60 <= Decimal("-2"):
                    g["down_2pct_60m"]+=1
                    if pattern is not None:
                        pattern["down_2pct_60m"]+=1
            if p24 is not None:
                g["matured_24h"]+=1
                if pattern is not None:
                    pattern["matured_24h"]+=1
                    pattern["_mature_pair_set"].add(pair)
                    pattern["_mature_day_set"].add(minute_epoch//86400)
                value=Decimal(p24)
                for threshold,field in (
                    (Decimal("20"),"up_20pct_24h"),
                    (Decimal("50"),"up_50pct_24h"),
                    (Decimal("100"),"up_100pct_24h"),
                ):
                    if value >= threshold:
                        g[field]+=1
                        if pattern is not None:
                            pattern[field]+=1
                if value <= Decimal("-10"):
                    g["down_10pct_24h"]+=1
                    if pattern is not None:
                        pattern["down_10pct_24h"]+=1
        for item in groups.values():
            total=item.pop("_sum_return_60m")
            n=item["matured_60m"]
            item["mean_return_60m_pct"]=(
                str((total/Decimal(n)).quantize(Decimal("0.0001")))
                if n else None
            )
        for item in patterns.values():
            total=item.pop("_sum_return_60m")
            n=item["matured_60m"]
            item["mean_return_60m_pct"]=(
                str((total/Decimal(n)).quantize(Decimal("0.0001")))
                if n else None
            )
            item["scalp_60m_descriptive_status"]=(
                "OBSERVED_ENDPOINT_HISTORY_NOT_TRADE_VALIDATED"
                if n else "NO_MATURED_60M_OUTCOMES"
            )
            item["distinct_pairs"]=len(item.pop("_pair_set"))
            item["distinct_days"]=len(item.pop("_day_set"))
            item["matured_distinct_pairs"]=len(item.pop("_mature_pair_set"))
            item["matured_distinct_days"]=len(item.pop("_mature_day_set"))
            # Even if sample counts rise, this remains descriptive because
            # there is no independent holdout or all-in-fee outcome yet.
            item["status"]=(
                "DESCRIPTIVE_HISTORY_NOT_VALIDATED"
                if item["matured_24h"] >= 30 and item["matured_distinct_pairs"] >= 5
                   and item["matured_distinct_days"] >= 3
                else "INSUFFICIENT_MATURED_COMPARABLE_CASES"
            )
            item["out_of_sample_validated"]=False
            item["net_fee_adjusted_edge_proven"]=False
        return {
            "status":"DESCRIPTIVE_EXPERIENCE_NOT_PREDICTIVE",
            "cohorts":groups,
            "pattern_library":patterns,
            "casebook_rows":len(rows),
            "forward_24h_endpoint_not_maximum":True,
            "forward_60m_endpoint_not_intrahour_maximum":True,
            "return_60m_is_gross_ticker_quote_not_position_net_roi":True,
            "only_asof_frozen_features":True,
            "control_cohort_available":"CONTROL" in groups,
            "learning_guard":"NEED_MATURED_CONTROLS_OUT_OF_SAMPLE_AND_FEES",
            "trainable_model_authorized":False,
            "g1_verified":False,
            "trade_authorized":False,
        }
