"""Persistent and idempotent REJECT audit log. No private/account information."""
from __future__ import annotations

import sqlite3
import hashlib
import os
import stat
import json
import re
from decimal import Decimal
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from contextlib import contextmanager
from typing import Iterator
from pathlib import Path

from .contracts import FinalDecision
from .m07_order_adapter import M07OrderDraft, M07OfflineStatusObservation
from .m07_private_order_readonly import M07ReadOnlyOrder
from .m07_readonly_fills import M07VerifiedReadOnlyFills
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
        self._m11_readonly_fixture = False
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
            # M10-05: bounded original M03 DEEP20 pre-final observation.
            # No owner Final, quote, position, private/account data or order.
            con.execute(
                """CREATE TABLE IF NOT EXISTS m03_compact_tick (
                    trace_id TEXT PRIMARY KEY,
                    lane TEXT NOT NULL
                        CHECK(lane='DEEP20_EVERY_FIVE_MINUTES'),
                    summary_json TEXT NOT NULL,
                    stage_disposition TEXT NOT NULL
                        CHECK(stage_disposition='ADVISORY_ONLY_NO_OWNER_DECISION'),
                    execution_allowed INTEGER NOT NULL CHECK(execution_allowed=0)
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
            # M11-04: ONE original DecisionJournal SQLite, NEVER a second
            # risk database, owner authorization, account source, or BUY gate.
            # Missing/ambiguous policy is denied. The halt is *born engaged*
            # and has NO clear/disengage API in the offline source contract.
            con.execute(
                """CREATE TABLE IF NOT EXISTS m11_offline_risk_limits (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    owner_user_id INTEGER NOT NULL,
                    policy_id TEXT NOT NULL UNIQUE,
                    policy_sha256 TEXT NOT NULL,
                    capital_ceiling_idr TEXT NOT NULL,
                    max_open_orders INTEGER NOT NULL,
                    daily_loss_ceiling_idr TEXT NOT NULL,
                    recorded_epoch INTEGER NOT NULL,
                    source_kind TEXT NOT NULL
                        CHECK(source_kind='OWNER_ENTERED_OFFLINE_UNVERIFIED'),
                    execution_allowed INTEGER NOT NULL
                        CHECK(execution_allowed=0)
                )"""
            )
            con.execute(
                """CREATE TABLE IF NOT EXISTS m11_offline_risk_halt (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    state TEXT NOT NULL CHECK(state='ENGAGED'),
                    event_count INTEGER NOT NULL CHECK(event_count>=0),
                    execution_allowed INTEGER NOT NULL
                        CHECK(execution_allowed=0)
                )"""
            )
            con.execute(
                "INSERT OR IGNORE INTO m11_offline_risk_halt "
                "(singleton,state,event_count,execution_allowed) "
                "VALUES (1,'ENGAGED',0,0)"
            )
            con.execute(
                """CREATE TABLE IF NOT EXISTS m11_offline_risk_halt_events (
                    event_id TEXT PRIMARY KEY,
                    owner_user_id INTEGER NOT NULL,
                    reason_code TEXT NOT NULL CHECK(reason_code IN (
                        'OWNER_EMERGENCY','LIMIT_REVIEW',
                        'UNKNOWN_ORDER','RECOVERY_UNVERIFIED'
                    )),
                    observed_epoch INTEGER NOT NULL,
                    event_digest TEXT NOT NULL,
                    source_kind TEXT NOT NULL
                        CHECK(source_kind='OFFLINE_UNVERIFIED'),
                    execution_allowed INTEGER NOT NULL
                        CHECK(execution_allowed=0)
                )"""
            )
            # M07 MANUAL sender boundary. A Telegram preview press and a
            # MOCK TAPI POST result are never source-attested order issuer
            # evidence. Hold all staged client IDs across restart; NO
            # trade-specific proof, release, ownership or settlement exists.
            con.execute(
                """CREATE TABLE IF NOT EXISTS m07_manual_sender_nonlive (
                    client_order_id TEXT PRIMARY KEY,
                    preview_id TEXT NOT NULL UNIQUE,
                    owner_user_id INTEGER NOT NULL,
                    account_fingerprint TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
                    draft_sha256 TEXT NOT NULL,
                    created_epoch INTEGER NOT NULL,
                    last_epoch INTEGER NOT NULL,
                    state TEXT NOT NULL CHECK(state IN
                        ('OFFLINE_CLICK_BOUND_NO_SUBMIT',
                         'OFFLINE_UNKNOWN_HOLD_NO_RETRY',
                         'OFFLINE_MOCK_RESPONSE_HOLD_NO_ISSUER')),
                    mock_response_sha256 TEXT,
                    mock_status TEXT,
                    source TEXT NOT NULL CHECK(
                        source='M05_PREVIEW_NO_EXECUTION_AUTHORITY'),
                    execution_allowed INTEGER NOT NULL
                        CHECK(execution_allowed=0)
                )"""
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_m07_manual_hold "
                "ON m07_manual_sender_nonlive(symbol,state)"
            )
            # M07 P1: local pre-submit commitment on the SAME original V2
            # DecisionJournal. PREPARED/UNCERTAIN are strictly synthetic,
            # never exchange-submitted order provenance, capital reserve,
            # ownership, live permission or a separately issued order event.
            con.execute(
                """CREATE TABLE IF NOT EXISTS m07_prepared_origin_no_submit (
                    client_order_id TEXT PRIMARY KEY,
                    account_uid INTEGER NOT NULL,
                    account_fingerprint TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK(side='BUY'),
                    order_type TEXT NOT NULL CHECK(order_type='LIMIT'),
                    price_idr TEXT NOT NULL,
                    quantity_base TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    created_epoch INTEGER NOT NULL,
                    last_epoch INTEGER NOT NULL,
                    state TEXT NOT NULL CHECK(state IN
                        ('OFFLINE_PREPARED_NO_SUBMIT',
                         'OFFLINE_UNCERTAIN_HOLD_NO_RETRY')),
                    provenance TEXT NOT NULL CHECK(
                        provenance='LOCAL_DRAFT_NO_EXCHANGE_SUBMIT'),
                    execution_allowed INTEGER NOT NULL CHECK(execution_allowed=0)
                )"""
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_m07_origin_local_hold "
                "ON m07_prepared_origin_no_submit(symbol,state)"
            )
            # M07-01/03: original V2 journal owns one durable OFFLINE-only
            # order-intent reservation namespace. This table is NOT a
            # signed exchange execution log or authenticated ownership.
            con.execute(
                """CREATE TABLE IF NOT EXISTS m07_offline_order_intents (
                    intent_id TEXT PRIMARY KEY,
                    client_order_id TEXT NOT NULL UNIQUE,
                    draft_sha256 TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
                    state TEXT NOT NULL CHECK(state IN
                        ('OFFLINE_RESERVED','OFFLINE_UNKNOWN_HOLD')),
                    recorded_epoch INTEGER NOT NULL,
                    last_observed_epoch INTEGER NOT NULL,
                    schema_version INTEGER NOT NULL CHECK(schema_version=1)
                )"""
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_m07_offline_intent_active "
                "ON m07_offline_order_intents(state,symbol)"
            )
            con.execute(
                """CREATE TABLE IF NOT EXISTS m07_offline_status_observations (
                    observation_id TEXT PRIMARY KEY,
                    intent_id TEXT NOT NULL,
                    client_order_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    status TEXT NOT NULL,
                    cumulative_base TEXT NOT NULL,
                    cumulative_quote TEXT NOT NULL,
                    observed_epoch INTEGER NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    source_kind TEXT NOT NULL CHECK(
                        source_kind='SYNTHETIC_OFFLINE_ONLY'
                    ),
                    FOREIGN KEY(intent_id) REFERENCES m07_offline_order_intents(intent_id)
                )"""
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_m07_offline_status_order "
                "ON m07_offline_status_observations(intent_id,observed_epoch)"
            )
            # Authenticated PRIVATE READ ONLY observations are kept apart from
            # synthetic intent events. Neither table grants order ownership.
            con.execute(
                """CREATE TABLE IF NOT EXISTS m07_exchange_readonly_orders (
                    account_fingerprint TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    client_order_id TEXT NOT NULL,
                    exchange_order_id INTEGER NOT NULL,
                    full_order_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    original_qty TEXT NOT NULL,
                    executed_qty TEXT NOT NULL,
                    created_ms INTEGER NOT NULL,
                    observed_ms INTEGER NOT NULL,
                    provenance TEXT NOT NULL CHECK (
                       provenance='INDODAX_TAPIV2_GET_ACCOUNT_AND_ORDER'
                    ),
                    PRIMARY KEY(account_fingerprint,symbol,client_order_id)
                )"""
            )
            con.execute(
                """CREATE TABLE IF NOT EXISTS m07_exchange_readonly_fill_summaries (
                    account_fingerprint TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    client_order_id TEXT NOT NULL,
                    full_order_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    qty_base TEXT NOT NULL,
                    gross_quote_idr TEXT NOT NULL,
                    commission_including_tax_idr TEXT NOT NULL,
                    net_quote_idr TEXT NOT NULL,
                    fill_count INTEGER NOT NULL,
                    fill_digest TEXT NOT NULL,
                    verified_at_ms INTEGER NOT NULL,
                    provenance TEXT NOT NULL CHECK (
                        provenance='INDODAX_TAPIV2_MYTRADES_ACCOUNT_ONLY'
                    ),
                    PRIMARY KEY(account_fingerprint,symbol,client_order_id)
                )"""
            )
            # M08-02: the SAME original journal, account-only myTrades
            # partial-fill timestamps, bound to the immutable fill digest.
            # No synthetic/later observation may create bot-owned inventory.
            con.execute(
                """CREATE TABLE IF NOT EXISTS m08_account_buy_trade_times (
                    account_fingerprint TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    client_order_id TEXT NOT NULL,
                    full_order_id TEXT NOT NULL,
                    fill_digest TEXT NOT NULL,
                    first_trade_ms INTEGER NOT NULL,
                    last_trade_ms INTEGER NOT NULL,
                    provenance TEXT NOT NULL CHECK(
                        provenance='INDODAX_TAPIV2_MYTRADES_ACCOUNT_ONLY'
                    ),
                    PRIMARY KEY(account_fingerprint,symbol,client_order_id),
                    FOREIGN KEY(account_fingerprint,symbol,client_order_id)
                      REFERENCES m07_exchange_readonly_fill_summaries(
                        account_fingerprint,symbol,client_order_id)
                )"""
            )
            # M07-04: immutable OFFLINE draft facts for new synthetic intents.
            # Older intents have no row and MUST remain unlinked/UNKNOWN.
            # Never infer ownership from an arbitrary matching client ID.
            con.execute(
                """CREATE TABLE IF NOT EXISTS m07_offline_intent_draft_facts (
                    intent_id TEXT PRIMARY KEY,
                    order_type TEXT NOT NULL CHECK(order_type='LIMIT'),
                    original_qty TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK(side='BUY'),
                    FOREIGN KEY(intent_id) REFERENCES m07_offline_order_intents(intent_id)
                )"""
            )
            # Account-scoped READ ONLY correlation with the original synthetic
            # order-intent namespace. This is not an exchange submit ledger.
            # Neither FILLED nor CANCELLED here releases BUY budget, marks a
            # bot-owned lot, or creates a retry right.
            con.execute(
                """CREATE TABLE IF NOT EXISTS m07_readonly_intent_recovery_shadow (
                    intent_id TEXT PRIMARY KEY,
                    account_fingerprint TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    client_order_id TEXT NOT NULL,
                    exchange_order_id INTEGER NOT NULL,
                    full_order_id TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    original_qty TEXT NOT NULL,
                    executed_qty TEXT NOT NULL,
                    order_status TEXT NOT NULL,
                    observed_ms INTEGER NOT NULL,
                    provenance TEXT NOT NULL CHECK(
                        provenance='ACCOUNT_VIEWONLY_CORRELATION_NOT_OWNERSHIP'
                    ),
                    FOREIGN KEY(intent_id) REFERENCES m07_offline_order_intents(intent_id)
                )"""
            )
            # M08-05: same ORIGINAL SQLite journal, non-executing SELL
            # preflight receipts only. Neither a reservation nor ownership.
            con.execute(
                """CREATE TABLE IF NOT EXISTS m08_offline_sell_preflight_receipts (
                    sell_client_order_id TEXT PRIMARY KEY,
                    account_fingerprint TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    source_buy_client_order_id TEXT NOT NULL,
                    draft_digest TEXT NOT NULL,
                    scenario_digest TEXT NOT NULL,
                    exchange_state_digest TEXT NOT NULL,
                    observed_epoch INTEGER NOT NULL,
                    state TEXT NOT NULL CHECK(
                        state='NONEXECUTING_REVIEW_ONLY'
                    )
                )"""
            )
            # Owner Strategy A shadow only: source is caller-entered OFFLINE
            # scenario. Latching survives restart and parallel review, but
            # cannot mint a bot-owned lot, SELL authorization or settlement.
            con.execute(
                """CREATE TABLE IF NOT EXISTS m08_strategy_a_stop_shadow (
                    account_fingerprint TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    source_buy_client_order_id TEXT NOT NULL,
                    first_observed_epoch INTEGER NOT NULL
                        CHECK(first_observed_epoch > 0),
                    trigger_net_roi_pct TEXT NOT NULL,
                    trigger_source_digest TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(
                        state='OFFLINE_STOP_LATCHED'
                    ),
                    source_kind TEXT NOT NULL CHECK(
                        source_kind='SYNTHETIC_STOP_REVIEW_ONLY'
                    ),
                    execution_allowed INTEGER NOT NULL
                        CHECK(execution_allowed=0),
                    PRIMARY KEY(account_fingerprint,symbol,
                                source_buy_client_order_id)
                )"""
            )
            # M08-07: an event is NOT issued here. Only a future M08-06
            # verified bot-owned SELL/fill/fee/wallet reconciler may record
            # POSITION_SETTLED_IDR in this same journal. M08-07 consumes it
            # once and only starts the ORIGINAL public scanner, not BUY.
            con.execute(
                """CREATE TABLE IF NOT EXISTS m08_position_settled_idr_events (
                    event_id TEXT PRIMARY KEY,
                    account_fingerprint TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    source_buy_client_order_id TEXT NOT NULL,
                    sell_client_order_id TEXT NOT NULL UNIQUE,
                    sell_full_order_id TEXT NOT NULL,
                    sell_fill_digest TEXT NOT NULL,
                    bot_owned_execution_digest TEXT NOT NULL,
                    idr_wallet_settlement_digest TEXT NOT NULL,
                    net_settled_idr TEXT NOT NULL,
                    settled_at_ms INTEGER NOT NULL,
                    source TEXT NOT NULL CHECK(
                        source='M08_RECONCILED_BOT_OWNED_SELL_IDR'
                    ),
                    status TEXT NOT NULL CHECK(status='POSITION_SETTLED_IDR')
                )"""
            )
            con.execute(
                """CREATE TABLE IF NOT EXISTS m08_post_sell_discovery_claims (
                    event_id TEXT PRIMARY KEY,
                    scan_trace_id TEXT NOT NULL UNIQUE,
                    claimed_at_epoch INTEGER NOT NULL,
                    scanner_name TEXT NOT NULL CHECK(
                        scanner_name='ORIGINAL_M03_BULK_DEEP'
                    ),
                    FOREIGN KEY(event_id)
                      REFERENCES m08_position_settled_idr_events(event_id)
                )"""
            )
            # M09-04: PAPER trades are a distinct, visibly SIMULATED
            # namespace in THIS original journal. No exchange order UID,
            # clientOrderId, wallet/account fingerprint, owner Final, or
            # POSITION_SETTLED_IDR can be recorded through these tables.
            con.execute(
                """CREATE TABLE IF NOT EXISTS m09_paper_simulated_buys (
                    paper_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    buy_qty TEXT NOT NULL,
                    buy_price_idr TEXT NOT NULL,
                    buy_all_in_fee_idr TEXT NOT NULL,
                    buy_epoch INTEGER NOT NULL,
                    source TEXT NOT NULL CHECK(
                        source='M09_PAPER_SCENARIO_ONLY'
                    )
                )"""
            )
            con.execute(
                """CREATE TABLE IF NOT EXISTS m09_paper_simulated_sells (
                    paper_exit_id TEXT PRIMARY KEY,
                    paper_id TEXT NOT NULL,
                    sell_qty TEXT NOT NULL,
                    sell_price_idr TEXT NOT NULL,
                    sell_all_in_fee_idr TEXT NOT NULL,
                    incremental_slippage_idr TEXT NOT NULL,
                    paper_net_pnl_idr TEXT NOT NULL,
                    sell_epoch INTEGER NOT NULL,
                    source TEXT NOT NULL CHECK(
                        source='M09_PAPER_SCENARIO_ONLY'
                    ),
                    FOREIGN KEY(paper_id) REFERENCES m09_paper_simulated_buys(paper_id)
                )"""
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS m09_paper_sell_parent "
                "ON m09_paper_simulated_sells(paper_id,sell_epoch)"
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



    @classmethod
    def open_m11_offline_fixture_readonly(
        cls, db_path: str | Path,
    ) -> "DecisionJournal":
        """Open one existing private /tmp M11 fixture without schema writes.

        This is the SAME DecisionJournal and review implementation. It is
        intentionally unavailable for normal runtime SQLite, Telegram,
        private account, owner Final, M07 execution or LIVE authorization.
        """
        path = Path(db_path)
        if not path.is_absolute() or path.name != "m11_offline_fixture.sqlite3":
            raise ValueError("M11_SHADOW_FIXTURE_SCOPE_INVALID")
        try:
            resolved = path.resolve(strict=True)
            file_st = path.lstat()
            parent_st = path.parent.stat()
        except (OSError, RuntimeError):
            raise ValueError("M11_SHADOW_FIXTURE_UNAVAILABLE") from None
        if (resolved != path or path.is_symlink()
                or path.parent == Path("/tmp")
                or not path.parent.is_relative_to(Path("/tmp"))
                or not stat.S_ISREG(file_st.st_mode)
                or not stat.S_ISDIR(parent_st.st_mode)
                or file_st.st_uid != os.getuid()
                or parent_st.st_uid != os.getuid()
                or stat.S_IMODE(file_st.st_mode) & 0o077
                or not stat.S_IMODE(file_st.st_mode) & 0o400
                or stat.S_IMODE(parent_st.st_mode) & 0o077):
            raise ValueError("M11_SHADOW_FIXTURE_SCOPE_INVALID")
        # A WAL/SHM/journal sidecar could alter which version is read.
        # Treat it as unverified rather than promoting a stale snapshot.
        if any(Path(str(path) + suffix).exists()
               for suffix in ("-wal", "-shm", "-journal")):
            raise ValueError("M11_SHADOW_FIXTURE_SIDECAR_UNVERIFIED")
        instance = cls.__new__(cls)
        instance._db_path = path
        instance._m11_readonly_fixture = True
        return instance

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if getattr(self, "_m11_readonly_fixture", False):
            con = sqlite3.connect(
                self._db_path.as_uri() + "?mode=ro", uri=True, timeout=10,
            )
            con.execute("PRAGMA query_only=ON")
        else:
            con = sqlite3.connect(self._db_path, timeout=10)
        try:
            with con:
                yield con
        finally:
            con.close()

    @staticmethod
    def _m08_strategy_a_scope(
            account_fingerprint: str, symbol: str,
            source_buy_client_order_id: str) -> tuple[str, str, str]:
        """Strict offline namespace, NOT proof of account/order ownership."""
        if (type(account_fingerprint) is not str or
                re.fullmatch(r"[0-9a-f]{64}", account_fingerprint) is None
                or type(symbol) is not str
                or re.fullmatch(r"[A-Z0-9]{2,20}IDR", symbol) is None
                or type(source_buy_client_order_id) is not str
                or re.fullmatch(r"[A-Za-z0-9_-]{1,36}",
                                source_buy_client_order_id) is None):
            raise DecisionConflictError("M08_STRATEGY_A_SHADOW_SCOPE_INVALID")
        return (account_fingerprint, symbol, source_buy_client_order_id)

    @staticmethod
    def _m08_strategy_a_stop_row(row: tuple | None) -> dict:
        if row is None:
            return {
                "latched": False, "first_observed_epoch": None,
                "trigger_net_roi_pct": None, "trigger_source_digest": None,
                "state": "NO_OFFLINE_STOP_LATCH",
                "source_authenticated": False,
                "bot_owned_position_verified": False,
                "execution_allowed": False,
            }
        epoch, roi_raw, digest, state, source_kind, allowed = row
        try:
            roi = Decimal(roi_raw)
        except (ArithmeticError, ValueError, TypeError):
            raise DecisionConflictError(
                "M08_STRATEGY_A_SHADOW_CORRUPT_ROI") from None
        if (type(epoch) is not int or not 1 <= epoch <= 9999999999
                or not roi.is_finite() or roi > Decimal("-5")
                or type(digest) is not str
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                or state != "OFFLINE_STOP_LATCHED"
                or source_kind != "SYNTHETIC_STOP_REVIEW_ONLY"
                or allowed != 0):
            raise DecisionConflictError("M08_STRATEGY_A_SHADOW_CORRUPT_LATCH")
        return {
            "latched": True, "first_observed_epoch": epoch,
            "trigger_net_roi_pct": str(roi),
            "trigger_source_digest": digest,
            "state": state, "source_authenticated": False,
            "bot_owned_position_verified": False,
            "execution_allowed": False,
        }

    def read_m08_strategy_a_stop_shadow_nonlive(
            self, *, account_fingerprint: str, symbol: str,
            source_buy_client_order_id: str) -> dict:
        scope = self._m08_strategy_a_scope(
            account_fingerprint, symbol, source_buy_client_order_id)
        with self._connect() as con:
            row = con.execute(
                "SELECT first_observed_epoch,trigger_net_roi_pct,"
                "trigger_source_digest,state,source_kind,execution_allowed "
                "FROM m08_strategy_a_stop_shadow "
                "WHERE account_fingerprint=? AND symbol=? AND "
                "source_buy_client_order_id=?", scope).fetchone()
        return self._m08_strategy_a_stop_row(row)

    def latch_m08_strategy_a_stop_shadow_nonlive_once(
            self, *, account_fingerprint: str, symbol: str,
            source_buy_client_order_id: str, observed_epoch: int,
            assessed_exit: dict) -> dict:
        """Atomic sticky STOP shadow. Never creates real order/fill rights.

        First trigger must have a *complete offline* executable bid-depth
        estimate at <=-5% net. Future market recovery cannot clear latch.
        Same-position subsequent trigger remains inert, not a new claim.
        """
        scope = self._m08_strategy_a_scope(
            account_fingerprint, symbol, source_buy_client_order_id)
        if (type(observed_epoch) is not int
                or not 1 <= observed_epoch <= 9999999999
                or type(assessed_exit) is not dict
                or assessed_exit.get("status") not in (
                    "STOP_TAKER_PREVIEW_ONLY",
                    "STOP_CANCEL_RECONCILE_FIRST",
                )
                or assessed_exit.get("stop_latched") is not True
                or assessed_exit.get("execution_allowed") is not False
                or assessed_exit.get("order_sent") is not False
                or assessed_exit.get("source_verified_for_execution") is not False
                or assessed_exit.get("bot_owned_inventory_attested") is not False
                or assessed_exit.get("assumed_bid_depth_covers_full_qty") is not True):
            raise DecisionConflictError("M08_STRATEGY_A_TRIGGER_UNVERIFIED")
        raw_roi = assessed_exit.get("projected_full_exit_net_roi_pct")
        if type(raw_roi) is not str:
            raise DecisionConflictError("M08_STRATEGY_A_TRIGGER_ROI_INVALID")
        try:
            roi = Decimal(raw_roi)
        except (ArithmeticError, ValueError):
            raise DecisionConflictError(
                "M08_STRATEGY_A_TRIGGER_ROI_INVALID") from None
        if not roi.is_finite() or roi > Decimal("-5"):
            # Do not create the initial latch from a rebound or a caller's
            # stop_latched=True alone. Existing latch is read separately.
            raise DecisionConflictError(
                "M08_STRATEGY_A_FIRST_TRIGGER_MUST_BE_NET_MINUS_FIVE")
        trigger = (
            *scope, observed_epoch, str(roi),
            assessed_exit["status"],
        )
        digest = hashlib.sha256(
            json.dumps(trigger, separators=(",", ":"), ensure_ascii=True
                       ).encode("ascii")
        ).hexdigest()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            prior = con.execute(
                "SELECT first_observed_epoch,trigger_net_roi_pct,"
                "trigger_source_digest,state,source_kind,execution_allowed "
                "FROM m08_strategy_a_stop_shadow "
                "WHERE account_fingerprint=? AND symbol=? AND "
                "source_buy_client_order_id=?", scope).fetchone()
            if prior is not None:
                state = self._m08_strategy_a_stop_row(prior)
                return {**state, "replayed": True, "new_latch": False}
            con.execute(
                "INSERT INTO m08_strategy_a_stop_shadow ("
                "account_fingerprint,symbol,source_buy_client_order_id,"
                "first_observed_epoch,trigger_net_roi_pct,"
                "trigger_source_digest,state,source_kind,execution_allowed"
                ") VALUES(?,?,?,?,?,?,'OFFLINE_STOP_LATCHED',"
                "'SYNTHETIC_STOP_REVIEW_ONLY',0)",
                (*scope, observed_epoch, str(roi), digest),
            )
        return {
            "latched": True, "first_observed_epoch": observed_epoch,
            "trigger_net_roi_pct": str(roi),
            "trigger_source_digest": digest, "state": "OFFLINE_STOP_LATCHED",
            "source_authenticated": False,
            "bot_owned_position_verified": False,
            "execution_allowed": False,
            "replayed": False, "new_latch": True,
        }

    @staticmethod
    def _m07_origin_local_params(
            *, account_uid: int, account_fingerprint: str,
            draft: M07OrderDraft, observed_epoch: int) -> tuple:
        """Canonical local BUY draft identity, not authentication of owner."""
        if (type(account_uid) is not int or not 1 <= account_uid <= 9999999999
                or type(account_fingerprint) is not str
                or re.fullmatch(r"[a-f0-9]{64}", account_fingerprint) is None
                or type(draft) is not M07OrderDraft
                or draft.side != "BUY" or draft.order_type != "LIMIT"
                or type(observed_epoch) is not int
                or not 1 <= observed_epoch <= 9999999999):
            raise DecisionConflictError("M07_ORIGIN_LOCAL_SCOPE_INVALID")
        params = draft.parameters()  # Original OfficialV2OrderAdapter contract.
        digest = hashlib.sha256(json.dumps(
            params, separators=(",", ":"), ensure_ascii=True,
        ).encode("ascii")).hexdigest()
        return (
            draft.client_order_id, account_uid, account_fingerprint,
            draft.symbol, draft.side, draft.order_type,
            format(draft.price, "f"), format(draft.quantity, "f"), digest,
        )

    def prepare_m07_order_origin_commitment_offline(
            self, *, account_uid: int, account_fingerprint: str,
            draft: M07OrderDraft, observed_epoch: int) -> dict:
        """One durable, idempotent pre-SEND draft on original journal only.

        We cannot prove a bot-submitted order without a separately
        authenticated, durable sender-side exchange receipt. Local drafts,
        client IDs and View Only orders must NEVER mint one. Permanent
        unresolved/uncertain holds are conservative, not spendable budget.
        """
        if getattr(self, "_m11_readonly_fixture", False):
            raise DecisionConflictError("M07_ORIGIN_WRITABLE_LOCAL_SCOPE_REQUIRED")
        item = self._m07_origin_local_params(
            account_uid=account_uid, account_fingerprint=account_fingerprint,
            draft=draft, observed_epoch=observed_epoch,
        )
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            old = con.execute(
                "SELECT account_uid,account_fingerprint,symbol,side,"
                "order_type,price_idr,quantity_base,request_sha256,"
                "created_epoch,state,provenance,execution_allowed "
                "FROM m07_prepared_origin_no_submit WHERE client_order_id=?",
                (draft.client_order_id,),
            ).fetchone()
            if old is not None:
                expected = (*item[1:], old[8], old[9],
                            "LOCAL_DRAFT_NO_EXCHANGE_SUBMIT", 0)
                if old != expected or observed_epoch < old[8]:
                    raise DecisionConflictError("M07_ORIGIN_IMMUTABLE_REPLAY_CONFLICT")
                status, replay = old[9], True
            else:
                # A local synthetic M07 intent uses the same global client
                # identity namespace. It cannot be promoted into bot origin.
                if con.execute(
                    "SELECT 1 FROM m07_offline_order_intents "
                    "WHERE client_order_id=? LIMIT 1",
                    (draft.client_order_id,),
                ).fetchone() is not None:
                    raise DecisionConflictError("M07_ORIGIN_SYNTHETIC_ID_COLLISION")
                if con.execute(
                    "SELECT 1 FROM m07_manual_sender_nonlive "
                    "WHERE state='OFFLINE_UNKNOWN_HOLD_NO_RETRY' LIMIT 1"
                ).fetchone() is not None:
                    raise DecisionConflictError("M07_MANUAL_GLOBAL_UNKNOWN_HOLD")
                if con.execute(
                    "SELECT 1 FROM m07_prepared_origin_no_submit "
                    "WHERE state='OFFLINE_UNCERTAIN_HOLD_NO_RETRY' LIMIT 1"
                ).fetchone() is not None:
                    raise DecisionConflictError("M07_ORIGIN_UNCERTAIN_BLOCKS_BUY")
                if con.execute(
                    "SELECT 1 FROM m07_offline_order_intents "
                    "WHERE state='OFFLINE_UNKNOWN_HOLD' LIMIT 1"
                ).fetchone() is not None:
                    raise DecisionConflictError("M07_ORIGIN_M07_UNKNOWN_BLOCKS_BUY")
                if (con.execute(
                    "SELECT 1 FROM m07_prepared_origin_no_submit "
                    "WHERE symbol=? LIMIT 1", (draft.symbol,),
                ).fetchone() is not None or con.execute(
                    "SELECT 1 FROM m07_offline_order_intents "
                    "WHERE symbol=? LIMIT 1", (draft.symbol,),
                ).fetchone() is not None or con.execute(
                    "SELECT 1 FROM m07_manual_sender_nonlive "
                    "WHERE symbol=? LIMIT 1", (draft.symbol,),
                ).fetchone() is not None):
                    raise DecisionConflictError("M07_ORIGIN_PAIR_UNRESOLVED")
                origin_count = con.execute(
                    "SELECT COUNT(*) FROM m07_prepared_origin_no_submit"
                ).fetchone()[0]
                synthetic_count = con.execute(
                    "SELECT COUNT(*) FROM m07_offline_order_intents"
                ).fetchone()[0]
                manual_count = con.execute(
                    "SELECT COUNT(*) FROM m07_manual_sender_nonlive"
                ).fetchone()[0]
                if origin_count + synthetic_count + manual_count >= 2:
                    raise DecisionConflictError("M07_ORIGIN_MAX_TWO_LOCAL_HOLDS")
                con.execute(
                    "INSERT INTO m07_prepared_origin_no_submit "
                    "(client_order_id,account_uid,account_fingerprint,"
                    "symbol,side,order_type,price_idr,quantity_base,"
                    "request_sha256,created_epoch,last_epoch,state,"
                    "provenance,execution_allowed) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,"
                    "'OFFLINE_PREPARED_NO_SUBMIT',"
                    "'LOCAL_DRAFT_NO_EXCHANGE_SUBMIT',0)",
                    (*item, observed_epoch, observed_epoch),
                )
                status, replay = "OFFLINE_PREPARED_NO_SUBMIT", False
        return {
            "stage": "M07_ORIGINAL_DURABLE_LOCAL_ORIGIN_COMMITMENT",
            "status": status, "replay": replay,
            "request_draft_sha256": item[8],
            "local_draft_immutable": True,
            "account_uid_authenticated": False,
            "owner_order_final_authenticated": False,
            "bot_exchange_submit_receipt_verified": False,
            "exchange_order_state_unknown_not_retryable": True,
            "bot_owned_quantity_verified": False,
            "m08_position_admitted": False,
            "created_order_intent_for_execution": False,
            "order_sent": False, "execution_allowed": False,
            "live_trading_authorized": False,
        }

    def mark_m07_order_origin_uncertain_offline(
            self, *, account_uid: int, account_fingerprint: str,
            draft: M07OrderDraft, observed_epoch: int) -> dict:
        """Synthetic lost-result/UNKNOWN hold. Cannot release or retry."""
        item = self._m07_origin_local_params(
            account_uid=account_uid, account_fingerprint=account_fingerprint,
            draft=draft, observed_epoch=observed_epoch,
        )
        if getattr(self, "_m11_readonly_fixture", False):
            raise DecisionConflictError("M07_ORIGIN_WRITABLE_LOCAL_SCOPE_REQUIRED")
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT account_uid,account_fingerprint,symbol,side,"
                "order_type,price_idr,quantity_base,request_sha256,"
                "created_epoch,last_epoch,state "
                "FROM m07_prepared_origin_no_submit WHERE client_order_id=?",
                (draft.client_order_id,),
            ).fetchone()
            if row is None:
                raise DecisionConflictError("M07_ORIGIN_NOT_PREPARED")
            if (row[:8] != item[1:] or observed_epoch < row[8]
                    or observed_epoch < row[9]):
                raise DecisionConflictError("M07_ORIGIN_UNKNOWN_REPLAY_CONFLICT")
            if row[10] == "OFFLINE_PREPARED_NO_SUBMIT":
                con.execute(
                    "UPDATE m07_prepared_origin_no_submit "
                    "SET state='OFFLINE_UNCERTAIN_HOLD_NO_RETRY',last_epoch=? "
                    "WHERE client_order_id=? AND state='OFFLINE_PREPARED_NO_SUBMIT'",
                    (observed_epoch, draft.client_order_id),
                )
                replay = False
            elif row[10] == "OFFLINE_UNCERTAIN_HOLD_NO_RETRY":
                replay = True
            else:
                raise DecisionConflictError("M07_ORIGIN_CORRUPT_STATE")
        return {
            "stage": "M07_ORIGINAL_DURABLE_LOCAL_ORIGIN_COMMITMENT",
            "status": "OFFLINE_UNCERTAIN_HOLD_NO_RETRY",
            "replay": replay,
            "exchange_submit_attempt_verified": False,
            "bot_exchange_submit_receipt_verified": False,
            "exchange_order_state_authenticated": False,
            "local_hold_permanent_without_verified_future_issuer": True,
            "retry_authorized": False, "release_authorized": False,
            "bot_owned_quantity_verified": False,
            "m08_position_admitted": False,
            "order_sent": False, "execution_allowed": False,
            "live_trading_authorized": False,
        }

    def review_m07_order_origin_handoff_readonly(
            self, *, account_uid: int, account_fingerprint: str,
            draft: M07OrderDraft, observed_epoch: int) -> dict:
        """SAME journal, atomic read-only correlation, no source promotion.

        Even a FILLED account-only View Only receipt matching every field
        cannot prove THIS V2 instance sent the BUY; no owner/issuer Final,
        bot-owned lot, executable SELL or M08-06 wallet event is minted.
        """
        if not getattr(self, "_m11_readonly_fixture", False):
            raise DecisionConflictError("M07_ORIGIN_REVIEW_READONLY_REQUIRED")
        item = self._m07_origin_local_params(
            account_uid=account_uid, account_fingerprint=account_fingerprint,
            draft=draft, observed_epoch=observed_epoch,
        )
        state = None
        order_correlated = fill_correlated = False
        with self._connect() as con:
            con.execute("BEGIN")
            row = con.execute(
                "SELECT account_uid,account_fingerprint,symbol,side,"
                "order_type,price_idr,quantity_base,request_sha256,"
                "created_epoch,state,provenance,execution_allowed "
                "FROM m07_prepared_origin_no_submit WHERE client_order_id=?",
                (draft.client_order_id,),
            ).fetchone()
            if row is not None:
                expected = (*item[1:], row[8], row[9],
                            "LOCAL_DRAFT_NO_EXCHANGE_SUBMIT", 0)
                if (row != expected or observed_epoch < row[8]):
                    raise DecisionConflictError("M07_ORIGIN_REVIEW_IDENTITY_CONFLICT")
                state = row[9]
                order = con.execute(
                    "SELECT full_order_id,side,order_type,original_qty,"
                    "executed_qty,status,provenance FROM "
                    "m07_exchange_readonly_orders WHERE "
                    "account_fingerprint=? AND symbol=? AND client_order_id=?",
                    (account_fingerprint, draft.symbol,
                     draft.client_order_id),
                ).fetchone()
                if order is not None:
                    try:
                        order_correlated = (
                            order[1:3] == ("BUY","LIMIT")
                            and order[5] == "FILLED"
                            and order[6] == "INDODAX_TAPIV2_GET_ACCOUNT_AND_ORDER"
                            and Decimal(order[3]) == draft.quantity
                            and Decimal(order[4]) == draft.quantity
                        )
                    except (ArithmeticError, ValueError, TypeError):
                        order_correlated = False
                    if order_correlated:
                        fills = con.execute(
                            "SELECT full_order_id,side,qty_base,fill_digest,"
                            "provenance FROM "
                            "m07_exchange_readonly_fill_summaries WHERE "
                            "account_fingerprint=? AND symbol=? "
                            "AND client_order_id=?",
                            (account_fingerprint,draft.symbol,
                             draft.client_order_id),
                        ).fetchone()
                        if fills is not None:
                            try:
                                fill_correlated = (
                                    fills[0:2] == (order[0],"BUY")
                                    and Decimal(fills[2]) == draft.quantity
                                    and type(fills[3]) is str
                                    and re.fullmatch(r"[a-f0-9]{64}",
                                                     fills[3]) is not None
                                    and fills[4] ==
                                        "INDODAX_TAPIV2_MYTRADES_ACCOUNT_ONLY"
                                )
                            except (ArithmeticError, ValueError, TypeError):
                                fill_correlated = False
            total, uncertain = con.execute(
                "SELECT COUNT(*), COALESCE(SUM(CASE WHEN "
                "state='OFFLINE_UNCERTAIN_HOLD_NO_RETRY' THEN 1 ELSE 0 END),0)"
                " FROM m07_prepared_origin_no_submit"
            ).fetchone()
        return {
            "stage": "M07_ORIGINAL_ORDER_ORIGIN_READONLY_HANDOFF",
            "status": "M07_ORIGIN_BLOCKED_NO_AUTHENTICATED_SUBMIT_ISSUER",
            "origin_local_state": state,
            "original_local_draft_present": state is not None,
            "account_only_buy_order_correlated_not_owned": order_correlated,
            "account_only_buy_fill_correlated_not_owned": fill_correlated,
            "origin_local_holds_observed": total,
            "origin_uncertain_holds_observed": uncertain,
            "account_uid_authenticated": False,
            "bot_exchange_submit_receipt_verified": False,
            "verified_bot_owned_lots": False,
            "verified_bot_owned_base_qty": None,
            "m08_position_manager_admission": False,
            "m08_06_settlement_issuer_allowed": False,
            "m07_sell_quantity_authorized": None,
            "retry_authorized": False, "release_authorized": False,
            "owner_final_decision": None,
            "order_sent": False, "execution_allowed": False,
            "live_trading_authorized": False,
        }

    def record_m07_manual_sender_nonlive_once(
            self, *, preview_id: str, owner_user_id: int,
            account_fingerprint: str, draft: M07OrderDraft,
            owner_review: dict, recorded_epoch: int) -> dict:
        """Atomic MANUAL button-bound no-submit hold on SAME V2 journal.

        SOURCE is existing M05 PREVIEW only: a click can prepare a future
        manual send, but never grant live authority, fill or ownership.
        All holds are durable and cannot be released by mock outcomes.
        """
        if getattr(self, "_m11_readonly_fixture", False):
            raise DecisionConflictError("M07_MANUAL_SENDER_WRITABLE_REQUIRED")
        if (type(preview_id) is not str
                or re.fullmatch(r"[0-9a-f]{24}", preview_id) is None
                or type(owner_user_id) is not int or owner_user_id <= 0
                or type(account_fingerprint) is not str
                or re.fullmatch(r"[a-f0-9]{64}", account_fingerprint) is None
                or type(draft) is not M07OrderDraft
                or draft.order_type != "LIMIT"
                or type(recorded_epoch) is not int
                or not 1 <= recorded_epoch <= 9999999999):
            raise DecisionConflictError("M07_MANUAL_SENDER_SCOPE_INVALID")
        params = draft.parameters()
        req = hashlib.sha256(json.dumps(
            params, separators=(",", ":"), ensure_ascii=True,
        ).encode("ascii")).hexdigest()
        if (type(owner_review) is not dict
                or owner_review.get("status") !=
                    "M05_MANUAL_CLICK_RECORDED_PREVIEW_ONLY"
                or owner_review.get("owner_button_recorded") is not True
                or owner_review.get("draft_sha256") != req
                or owner_review.get("preview_id") != preview_id
                or owner_review.get("execution_allowed") is not False
                or owner_review.get("owner_final_execution_authenticated") is not False):
            raise DecisionConflictError("M07_MANUAL_OWNER_PREVIEW_UNVERIFIED")
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT preview_id,owner_user_id,account_fingerprint,"
                "symbol,side,draft_sha256,created_epoch,state "
                "FROM m07_manual_sender_nonlive WHERE client_order_id=?",
                (draft.client_order_id,),
            ).fetchone()
            if row is not None:
                if (row[:6] != (
                        preview_id,owner_user_id,account_fingerprint,
                        draft.symbol,draft.side,req)
                        or recorded_epoch < row[6]):
                    raise DecisionConflictError("M07_MANUAL_SENDER_REPLAY_CONFLICT")
                state,replayed = row[7],True
            else:
                if con.execute(
                    "SELECT 1 FROM m07_manual_sender_nonlive "
                    "WHERE preview_id=? LIMIT 1",
                    (preview_id,),
                ).fetchone():
                    raise DecisionConflictError("M07_MANUAL_SENDER_PREVIEW_REUSED")
                if con.execute(
                    "SELECT 1 FROM m07_offline_order_intents "
                    "WHERE client_order_id=? LIMIT 1",
                    (draft.client_order_id,),
                ).fetchone() or con.execute(
                    "SELECT 1 FROM m07_prepared_origin_no_submit "
                    "WHERE client_order_id=? LIMIT 1",
                    (draft.client_order_id,),
                ).fetchone():
                    raise DecisionConflictError("M07_MANUAL_SENDER_CLIENT_ID_COLLISION")
                if (con.execute(
                    "SELECT 1 FROM m07_offline_order_intents "
                    "WHERE state='OFFLINE_UNKNOWN_HOLD' LIMIT 1",
                ).fetchone() or con.execute(
                    "SELECT 1 FROM m07_prepared_origin_no_submit "
                    "WHERE state='OFFLINE_UNCERTAIN_HOLD_NO_RETRY' LIMIT 1",
                ).fetchone() or con.execute(
                    "SELECT 1 FROM m07_manual_sender_nonlive "
                    "WHERE state='OFFLINE_UNKNOWN_HOLD_NO_RETRY' LIMIT 1",
                ).fetchone()):
                    raise DecisionConflictError("M07_MANUAL_SENDER_GLOBAL_UNKNOWN_HOLD")
                if (con.execute(
                    "SELECT 1 FROM m07_offline_order_intents WHERE symbol=? LIMIT 1",
                    (draft.symbol,),
                ).fetchone() or con.execute(
                    "SELECT 1 FROM m07_prepared_origin_no_submit "
                    "WHERE symbol=? LIMIT 1",
                    (draft.symbol,),
                ).fetchone() or con.execute(
                    "SELECT 1 FROM m07_manual_sender_nonlive "
                    "WHERE symbol=? LIMIT 1",
                    (draft.symbol,),
                ).fetchone()):
                    raise DecisionConflictError("M07_MANUAL_SENDER_PAIR_HOLD")
                n = con.execute(
                    "SELECT (SELECT COUNT(*) FROM m07_offline_order_intents)"
                    "+(SELECT COUNT(*) FROM m07_prepared_origin_no_submit)"
                    "+(SELECT COUNT(*) FROM m07_manual_sender_nonlive)"
                ).fetchone()[0]
                if n >= 2:
                    raise DecisionConflictError("M07_MANUAL_SENDER_MAX_TWO")
                # SELL can be inspected as a clicked preview but must NOT
                # create even a pending SELL-origin hold absent owned lots.
                if draft.side == "SELL":
                    raise DecisionConflictError(
                        "M07_MANUAL_SELL_REQUIRES_VERIFIED_BOT_OWNED_LOT")
                con.execute(
                    "INSERT INTO m07_manual_sender_nonlive "
                    "(client_order_id,preview_id,owner_user_id,"
                    "account_fingerprint,symbol,side,draft_sha256,"
                    "created_epoch,last_epoch,state,source,execution_allowed)"
                    " VALUES (?,?,?,?,?,?,?,?,?,"
                    "'OFFLINE_CLICK_BOUND_NO_SUBMIT',"
                    "'M05_PREVIEW_NO_EXECUTION_AUTHORITY',0)",
                    (draft.client_order_id,preview_id,owner_user_id,
                     account_fingerprint,draft.symbol,draft.side,req,
                     recorded_epoch,recorded_epoch),
                )
                state,replayed = "OFFLINE_CLICK_BOUND_NO_SUBMIT",False
        return {
            "stage": "M07_ORIGINAL_MANUAL_SENDER_NONLIVE_HANDOFF",
            "status": state, "replay": replayed,
            "draft_sha256": req,
            "manual_owner_preview_id_bound": preview_id,
            "manual_owner_telegram_press_recorded": True,
            "owner_final_execution_authenticated": False,
            "post_order_issuer_verified": False,
            "authenticated_sellable_lot_verified": False,
            "retry_authorized": False,
            "order_sent": False, "execution_allowed": False,
        }

    def observe_m07_manual_sender_mock_result_once(
            self, *, client_order_id: str, preview_id: str,
            draft: M07OrderDraft, result: dict,
            observed_epoch: int) -> dict:
        """Persist mock result hash; NEVER promote to actual order state."""
        if getattr(self, "_m11_readonly_fixture", False):
            raise DecisionConflictError("M07_MANUAL_SENDER_READONLY")
        if (type(client_order_id) is not str
                or client_order_id != draft.client_order_id
                or type(preview_id) is not str
                or re.fullmatch(r"[0-9a-f]{24}", preview_id) is None
                or type(observed_epoch) is not int or observed_epoch <= 0
                or type(result) is not dict
                or result.get("draft_sha256") is None
                or result.get("real_post_response_authenticated") is not False
                or result.get("order_sent") is not False
                or result.get("execution_allowed") is not False
                or result.get("retry_authorized") is not False
                or result.get("source_status") not in (
                    "OFFLINE_TIMEOUT_UNKNOWN_NO_RETRY",
                    "OFFLINE_FAKE_POST_RESULT_NOT_AUTHENTICATED",
                    "OFFLINE_FAKE_GET_STATUS_NOT_AUTHENTICATED")):
            raise DecisionConflictError("M07_MANUAL_SENDER_MOCK_SOURCE_INVALID")
        expected = hashlib.sha256(json.dumps(
            draft.parameters(), separators=(",", ":"), ensure_ascii=True,
        ).encode("ascii")).hexdigest()
        if result.get("draft_sha256") != expected:
            raise DecisionConflictError("M07_MANUAL_SENDER_MOCK_DRAFT_MISMATCH")
        status = ("OFFLINE_UNKNOWN_HOLD_NO_RETRY"
                  if result["source_status"] == "OFFLINE_TIMEOUT_UNKNOWN_NO_RETRY"
                  else "OFFLINE_MOCK_RESPONSE_HOLD_NO_ISSUER")
        fingerprint = result.get("mock_response_sha256")
        if (fingerprint is not None and (
                type(fingerprint) is not str
                or re.fullmatch(r"[a-f0-9]{64}", fingerprint) is None)
                or (status == "OFFLINE_UNKNOWN_HOLD_NO_RETRY"
                    and fingerprint is not None)
                or (status != "OFFLINE_UNKNOWN_HOLD_NO_RETRY"
                    and fingerprint is None)):
            raise DecisionConflictError("M07_MANUAL_SENDER_MOCK_DIGEST_INVALID")
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT preview_id,draft_sha256,created_epoch,last_epoch,"
                "state,mock_response_sha256 FROM m07_manual_sender_nonlive "
                "WHERE client_order_id=?", (client_order_id,),
            ).fetchone()
            if row is None:
                raise DecisionConflictError("M07_MANUAL_SENDER_UNSTAGED")
            if (row[:2] != (preview_id,expected)
                    or observed_epoch < row[2]
                    or observed_epoch < row[3]):
                raise DecisionConflictError("M07_MANUAL_SENDER_MOCK_REPLAY_CONFLICT")
            if row[4] == "OFFLINE_CLICK_BOUND_NO_SUBMIT":
                con.execute(
                    "UPDATE m07_manual_sender_nonlive "
                    "SET state=?,last_epoch=?,mock_response_sha256=?,"
                    "mock_status=? WHERE client_order_id=? AND "
                    "state='OFFLINE_CLICK_BOUND_NO_SUBMIT'",
                    (status,observed_epoch,fingerprint,
                     result.get("mock_status"),client_order_id),
                )
                replay=False
            elif row[4] == status and row[5] == fingerprint:
                replay=True
            else:
                raise DecisionConflictError("M07_MANUAL_SENDER_ALREADY_TERMINAL_HOLD")
        return {
            "stage": "M07_ORIGINAL_MANUAL_SENDER_MOCK_RESULT",
            "status": status, "replay": replay,
            "sender_response_synthetic_only": True,
            "bot_owned_lot_verified": False,
            "exchange_response_authenticated": False,
            "real_exchange_order_id": None,
            "retry_authorized": False,
            "m08_settlement_issuer_allowed": False,
            "order_sent": False, "execution_allowed": False,
        }

    def reserve_m07_offline_order_intent(
        self, *, intent_id: str, draft: M07OrderDraft,
        recorded_epoch: int,
    ) -> dict:
        """Atomically reserve a UNIQUE original V2 OFFLINE simulation intent.

        Replayed identical ID and digest never yields new submit permission.
        Colliding different payload, client ID, same-pair unresolved slot,
        unknown anywhere in MAIN_IDR, or >2 unresolved slots blocks new
        reservation, including after restart or concurrent processes.

        NO method can release, submit or claim a filled exchange order.
        """
        if (type(draft) is not M07OrderDraft
                or type(intent_id) is not str
                or not 1 <= len(intent_id) <= 36
                or type(recorded_epoch) is not int
                or not 1 <= recorded_epoch <= 9999999999):
            raise ValueError("M07_OFFLINE_INTENT_INVALID")
        # Validate the same original official adapter draft contract.
        params = draft.parameters()
        if draft.side != "BUY":
            # M08 verified bot-owned position/fills remain unavailable.
            raise DecisionConflictError("M07_OFFLINE_SELL_OWNERSHIP_UNKNOWN")
        # Make intent ID and clientOrderID the immutable one-to-one key.
        if draft.client_order_id != intent_id:
            raise DecisionConflictError("M07_INTENT_CLIENT_ID_MISMATCH")
        fingerprint = hashlib.sha256(
            json.dumps(params, separators=(",", ":"),
                       ensure_ascii=True).encode("ascii")
        ).hexdigest()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            prior = con.execute(
                "SELECT client_order_id,draft_sha256,symbol,side,state "
                "FROM m07_offline_order_intents WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
            if prior is not None:
                if prior[:4] != (
                    draft.client_order_id, fingerprint,
                    draft.symbol, draft.side,
                ):
                    raise DecisionConflictError("M07_INTENT_REPLAY_CONFLICT")
                return {
                    "status": prior[4], "intent_id": intent_id,
                    "replay": True, "newly_reserved": False,
                    "unknown_order_state_must_not_retry": True,
                    "exchange_order_id": None,
                    "order_sent": False, "execution_allowed": False,
                }
            # Even a brand-new UUID with the same old client ID must fail.
            used = con.execute(
                "SELECT 1 FROM m07_offline_order_intents "
                "WHERE client_order_id=?",
                (draft.client_order_id,),
            ).fetchone()
            if used is not None:
                raise DecisionConflictError("M07_CLIENT_ID_ALREADY_RESERVED")
            if con.execute(
                "SELECT 1 FROM m07_manual_sender_nonlive "
                "WHERE state='OFFLINE_UNKNOWN_HOLD_NO_RETRY' LIMIT 1"
            ).fetchone() is not None:
                raise DecisionConflictError("M07_MANUAL_GLOBAL_UNKNOWN_HOLD")
            if con.execute(
                "SELECT 1 FROM m07_prepared_origin_no_submit "
                "WHERE state='OFFLINE_UNCERTAIN_HOLD_NO_RETRY' LIMIT 1"
            ).fetchone() is not None:
                raise DecisionConflictError("M07_ORIGIN_UNCERTAIN_BLOCKS_BUY")
            if con.execute(
                "SELECT 1 FROM m07_offline_order_intents "
                "WHERE state='OFFLINE_UNKNOWN_HOLD' LIMIT 1"
            ).fetchone() is not None:
                # No proof which exchange commitment consumed IDR.
                raise DecisionConflictError("M07_UNKNOWN_BLOCKS_ALL_NEW_BUYS")
            if con.execute(
                "SELECT 1 FROM m07_manual_sender_nonlive "
                "WHERE client_order_id=? LIMIT 1", (draft.client_order_id,)
            ).fetchone() is not None:
                raise DecisionConflictError("M07_MANUAL_CLIENT_ID_IN_USE")
            if con.execute(
                "SELECT 1 FROM m07_prepared_origin_no_submit "
                "WHERE client_order_id=? LIMIT 1", (draft.client_order_id,)
            ).fetchone() is not None:
                raise DecisionConflictError("M07_ORIGIN_CLIENT_ID_IN_USE")
            if (con.execute(
                "SELECT 1 FROM m07_offline_order_intents "
                "WHERE symbol=? LIMIT 1", (draft.symbol,),
            ).fetchone() is not None or con.execute(
                "SELECT 1 FROM m07_prepared_origin_no_submit "
                "WHERE symbol=? LIMIT 1", (draft.symbol,),
            ).fetchone() is not None or con.execute(
                "SELECT 1 FROM m07_manual_sender_nonlive "
                "WHERE symbol=? LIMIT 1", (draft.symbol,),
            ).fetchone() is not None):
                raise DecisionConflictError("M07_UNRESOLVED_PAIR_INTENT")
            count = con.execute(
                "SELECT COUNT(*) FROM m07_offline_order_intents"
            ).fetchone()[0]
            other = con.execute(
                "SELECT COUNT(*) FROM m07_prepared_origin_no_submit"
            ).fetchone()[0]
            manual = con.execute(
                "SELECT COUNT(*) FROM m07_manual_sender_nonlive"
            ).fetchone()[0]
            if count + other + manual >= 2:
                raise DecisionConflictError("M07_MAX_TWO_UNRESOLVED_INTENTS")
            con.execute(
                "INSERT INTO m07_offline_order_intents "
                "(intent_id,client_order_id,draft_sha256,symbol,side,"
                "state,recorded_epoch,last_observed_epoch,schema_version) "
                "VALUES (?,?,?,?,?,'OFFLINE_RESERVED',?,?,1)",
                (intent_id,draft.client_order_id,fingerprint,
                 draft.symbol,draft.side,recorded_epoch,recorded_epoch),
            )
            # Store only the immutable facts that a future View Only status
            # can independently compare. No order was ever transmitted here.
            if draft.order_type == "LIMIT" and draft.quantity is not None:
                con.execute(
                    "INSERT INTO m07_offline_intent_draft_facts "
                    "(intent_id,order_type,original_qty,symbol,side) "
                    "VALUES (?,?,?,?,?)",
                    (intent_id,draft.order_type,format(draft.quantity,"f"),
                     draft.symbol,draft.side),
                )
        return {
            "status": "OFFLINE_RESERVED", "intent_id": intent_id,
            "replay": False, "newly_reserved": True,
            "unknown_order_state_must_not_retry": True,
            "exchange_order_id": None,
            "order_sent": False, "execution_allowed": False,
        }

    def mark_m07_offline_unknown_hold(
        self, *, intent_id: str, observed_epoch: int,
    ) -> dict:
        """Simulated timeout/ambiguity is PERMANENTLY blocking, not canceled.

        This endpoint is OFFLINE-only; no client may mark FILLED/CANCELED/
        FAILED or retry. Future source-attested reconciliation must be
        implemented separately with independent owner approval.
        """
        if (type(intent_id) is not str or not 1 <= len(intent_id) <= 36
                or type(observed_epoch) is not int
                or not 1 <= observed_epoch <= 9999999999):
            raise ValueError("M07_UNKNOWN_HOLD_INPUT_INVALID")
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            state = con.execute(
                "SELECT state,recorded_epoch,last_observed_epoch "
                "FROM m07_offline_order_intents WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
            if state is None:
                raise DecisionConflictError("M07_UNKNOWN_INTENT_NOT_RESERVED")
            if observed_epoch < state[1]:
                raise DecisionConflictError("M07_UNKNOWN_EPOCH_BEFORE_RESERVE")
            if observed_epoch < state[2]:
                raise DecisionConflictError("M07_UNKNOWN_EPOCH_REGRESSION")
            con.execute(
                "UPDATE m07_offline_order_intents SET "
                "state='OFFLINE_UNKNOWN_HOLD', last_observed_epoch=? "
                "WHERE intent_id=? AND state IN "
                "('OFFLINE_RESERVED','OFFLINE_UNKNOWN_HOLD')",
                (observed_epoch, intent_id),
            )
        return {
            "status": "OFFLINE_UNKNOWN_HOLD", "intent_id": intent_id,
            "unknown_order_state_must_not_retry": True,
            "release_requires_authenticated_exchange_reconciliation": True,
            "exchange_order_id": None, "order_sent": False,
            "execution_allowed": False,
        }

    def record_m07_offline_status_observation(
        self, observation: M07OfflineStatusObservation,
    ) -> dict:
        """Durable untrusted status timeline; neither release nor fill credit.

        The original order-intent state stays reserved/UNKNOWN even after
        synthetic FILLED/CANCELED. Any non-identical replay, regressed
        cumulative fills or terminal transition is refused atomically.
        This method CANNOT reconcile a real exchange order.
        """
        if type(observation) is not M07OfflineStatusObservation:
            raise ValueError("M07_OBSERVATION_TYPE_INVALID")
        record = observation.validated_record()
        fingerprint = hashlib.sha256(
            json.dumps(record, separators=(",", ":")).encode("ascii")
        ).hexdigest()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            intent = con.execute(
                "SELECT client_order_id,symbol,recorded_epoch,state,"
                "last_observed_epoch FROM m07_offline_order_intents "
                "WHERE intent_id=?", (observation.intent_id,),
            ).fetchone()
            if intent is None:
                raise DecisionConflictError("M07_OBSERVATION_INTENT_NOT_RESERVED")
            if (observation.client_order_id, observation.symbol) != intent[:2]:
                raise DecisionConflictError("M07_OBSERVATION_ORDER_MISMATCH")
            old = con.execute(
                "SELECT intent_id,payload_sha256 FROM "
                "m07_offline_status_observations WHERE observation_id=?",
                (observation.observation_id,),
            ).fetchone()
            if old is not None:
                if old != (observation.intent_id, fingerprint):
                    raise DecisionConflictError("M07_OBSERVATION_REPLAY_CONFLICT")
                return self._m07_offline_observation_report(
                    observation, replay=True, intent_state=intent[3],
                )
            if observation.observed_epoch < max(intent[2], intent[4]):
                raise DecisionConflictError("M07_OBSERVATION_STALE")
            latest = con.execute(
                "SELECT status,cumulative_base,cumulative_quote,observed_epoch "
                "FROM m07_offline_status_observations WHERE intent_id=? "
                "ORDER BY observed_epoch DESC, rowid DESC LIMIT 1",
                (observation.intent_id,),
            ).fetchone()
            if latest is not None:
                status, base, quote, epoch = latest
                if status in ("FILLED","CANCELED","EXPIRED","REJECTED"):
                    raise DecisionConflictError("M07_OBSERVATION_TERMINAL_IS_IMMUTABLE")
                if observation.observed_epoch <= epoch:
                    raise DecisionConflictError("M07_OBSERVATION_NOT_NEWER")
                if (observation.cumulative_base < Decimal(base)
                        or observation.cumulative_quote < Decimal(quote)):
                    raise DecisionConflictError("M07_OBSERVATION_CUMULATIVE_REGRESSION")
                if (status == "PARTIALLY_FILLED"
                        and observation.status in ("NEW","REJECTED")):
                    raise DecisionConflictError("M07_OBSERVATION_ILLEGAL_REGRESSION")
            con.execute(
                "INSERT INTO m07_offline_status_observations "
                "(observation_id,intent_id,client_order_id,symbol,status,"
                "cumulative_base,cumulative_quote,observed_epoch,"
                "payload_sha256,source_kind) "
                "VALUES (?,?,?,?,?,?,?,?,?,'SYNTHETIC_OFFLINE_ONLY')",
                (
                    observation.observation_id, observation.intent_id,
                    observation.client_order_id, observation.symbol,
                    observation.status, record[5], record[6],
                    observation.observed_epoch, fingerprint,
                ),
            )
            next_state = intent[3]
            if observation.status == "UNKNOWN" and intent[3] != "OFFLINE_UNKNOWN_HOLD":
                # A simulation of ambiguous submit permanently locks new
                # BUY; later fixtures may NOT clear the lock.
                con.execute(
                    "UPDATE m07_offline_order_intents SET "
                    "state='OFFLINE_UNKNOWN_HOLD',last_observed_epoch=? "
                    "WHERE intent_id=?",
                    (observation.observed_epoch, observation.intent_id),
                )
                next_state = "OFFLINE_UNKNOWN_HOLD"
            elif observation.observed_epoch > intent[4]:
                con.execute(
                    "UPDATE m07_offline_order_intents "
                    "SET last_observed_epoch=? WHERE intent_id=?",
                    (observation.observed_epoch, observation.intent_id),
                )
        return self._m07_offline_observation_report(
            observation, replay=False, intent_state=next_state,
        )

    @staticmethod
    def _m07_offline_observation_report(
        observation: M07OfflineStatusObservation, *,
        replay: bool, intent_state: str,
    ) -> dict:
        return {
            "status": "OFFLINE_UNTRUSTED_STATUS_RECORDED",
            "observation_id": observation.observation_id,
            "simulated_order_status": observation.status,
            "intent_state": intent_state,
            "replay": replay,
            "cumulative_base_unverified": format(observation.cumulative_base, "f"),
            "cumulative_quote_unverified": format(observation.cumulative_quote, "f"),
            "release_permitted": False,
            "retry_permitted": False,
            "exchange_order_state_authenticated": False,
            "bot_owned_fills_verified": False,
            "order_sent": False,
            "execution_allowed": False,
        }

    def record_m07_authenticated_readonly_order_status(
        self, *, account_fingerprint: str, order: M07ReadOnlyOrder,
        observed_ms: int,
    ) -> dict:
        """Atomic, strict, separately scoped EXCHANGE READ ONLY observation.

        No mutation of synthetic intents, no order release or asset ownership.
        Missing fill fees/trade-history means no SELL quantity is trusted.
        """
        if (type(account_fingerprint) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", account_fingerprint)
                or type(order) is not M07ReadOnlyOrder
                or type(observed_ms) is not int
                or not 1_000_000_000_000 <= observed_ms <= 9_999_999_999_999):
            raise DecisionConflictError("M07_READONLY_JOURNAL_INPUT_DENIED")
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            old = con.execute(
                "SELECT exchange_order_id,full_order_id,side,order_type,"
                "status,original_qty,executed_qty,created_ms,observed_ms "
                "FROM m07_exchange_readonly_orders "
                "WHERE account_fingerprint=? AND symbol=? AND client_order_id=?",
                (account_fingerprint, order.symbol, order.client_order_id),
            ).fetchone()
            old_qty = old[6] if old is not None else None
            if old is not None:
                if old[:4] != (
                        order.order_id, order.full_order_id,
                        order.side, order.order_type) or old[7] != order.created_ms:
                    raise DecisionConflictError("M07_READONLY_ORDER_IDENTITY_CHANGED")
                if Decimal(old[5]) != order.original_qty:
                    raise DecisionConflictError("M07_READONLY_ORDER_SIZE_CHANGED")
                if order.executed_qty < Decimal(old_qty):
                    raise DecisionConflictError("M07_READONLY_EXECUTED_REGRESSION")
                if old[4] in ("FILLED", "CANCELLED", "REJECTED") and (
                        order.status != old[4] or
                        order.executed_qty != Decimal(old_qty)):
                    raise DecisionConflictError("M07_READONLY_TERMINAL_CONFLICT")
                if old[4] == "PARTIALLY_FILLED" and order.status in (
                        "NEW", "REJECTED"):
                    raise DecisionConflictError("M07_READONLY_STATE_REGRESSION")
                if old[4] == "NEW" and order.status == "REJECTED":
                    # Rejected after NEW is not automatically a zero-fill release.
                    pass
                if old[4] == order.status and Decimal(old_qty) == order.executed_qty:
                    # Full authenticated order row already immutable. A
                    # same-source replay may re-inspect shadow correlation,
                    # but cannot upgrade execution/ownership permissions.
                    recovery = self._m07_attach_readonly_recovery_in_txn(
                        con, account_fingerprint=account_fingerprint,
                        order=order, observed_ms=old[8],
                    )
                    return {
                        "replay": True, "execution_allowed": False,
                        "recovery_reference_status": recovery,
                    }
                if observed_ms <= old[8]:
                    raise DecisionConflictError("M07_READONLY_OBSERVATION_NOT_NEWER")
            values = (
                account_fingerprint, order.symbol, order.client_order_id,
                order.order_id, order.full_order_id, order.side,
                order.order_type, order.status,
                format(order.original_qty, "f"),
                format(order.executed_qty, "f"),
                order.created_ms, observed_ms,
                "INDODAX_TAPIV2_GET_ACCOUNT_AND_ORDER",
            )
            if old is None:
                con.execute(
                    "INSERT INTO m07_exchange_readonly_orders "
                    "(account_fingerprint,symbol,client_order_id,"
                    "exchange_order_id,full_order_id,side,order_type,"
                    "status,original_qty,executed_qty,created_ms,"
                    "observed_ms,provenance) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", values,
                )
            else:
                con.execute(
                    "UPDATE m07_exchange_readonly_orders "
                    "SET status=?,executed_qty=?,observed_ms=? "
                    "WHERE account_fingerprint=? AND symbol=? AND client_order_id=?",
                    (order.status, format(order.executed_qty,"f"), observed_ms,
                     account_fingerprint, order.symbol, order.client_order_id),
                )
            recovery = self._m07_attach_readonly_recovery_in_txn(
                con, account_fingerprint=account_fingerprint,
                order=order, observed_ms=observed_ms,
            )
        return {
            "replay": False, "execution_allowed": False,
            "recovery_reference_status": recovery,
        }

    @staticmethod
    def _m07_attach_readonly_recovery_in_txn(
        con: sqlite3.Connection, *, account_fingerprint: str,
        order: M07ReadOnlyOrder, observed_ms: int,
    ) -> str:
        """Atomic correlation, never synthetic intent release or fill credit.

        Caller MUST first validate an authenticated V2 View Only order and
        record it into the original journal in this same transaction. A
        matching offline client ID alone cannot prove bot ownership, nor
        that the bot ever submitted an order. Legacy drafts are unbound.
        """
        intent = con.execute(
            "SELECT intent_id,symbol,side,recorded_epoch,state "
            "FROM m07_offline_order_intents WHERE client_order_id=?",
            (order.client_order_id,),
        ).fetchone()
        if intent is None:
            return "M07_RECOVERY_NO_LOCAL_INTENT"
        ident, symbol, side, recorded_epoch, _state = intent
        if symbol != order.symbol or side != order.side:
            raise DecisionConflictError("M07_RECOVERY_CLIENT_ID_SCOPE_CONFLICT")
        facts = con.execute(
            "SELECT order_type,original_qty,symbol,side "
            "FROM m07_offline_intent_draft_facts WHERE intent_id=?",
            (ident,),
        ).fetchone()
        if facts is None:
            return "M07_RECOVERY_LEGACY_OR_MARKET_DRAFT_UNBOUND"
        if (facts[0] != order.order_type
                or Decimal(facts[1]) != order.original_qty
                or facts[2:] != (order.symbol,order.side)):
            raise DecisionConflictError("M07_RECOVERY_IMMUTABLE_DRAFT_MISMATCH")
        # The account cannot create an earlier trade and then borrow the
        # same local client ID retroactively. No assumed live submit time.
        if not recorded_epoch * 1000 <= order.created_ms <= (
                recorded_epoch + 300) * 1000:
            raise DecisionConflictError("M07_RECOVERY_ORDER_TIME_OUTSIDE_INTENT")
        prior = con.execute(
            "SELECT account_fingerprint,symbol,client_order_id,"
            "exchange_order_id,full_order_id,order_type,original_qty,"
            "executed_qty,order_status,observed_ms "
            "FROM m07_readonly_intent_recovery_shadow WHERE intent_id=?",
            (ident,),
        ).fetchone()
        if prior is not None:
            if (prior[:7] != (
                    account_fingerprint,order.symbol,order.client_order_id,
                    order.order_id,order.full_order_id,order.order_type,
                    format(order.original_qty, "f"))
                and not (prior[:6] == (
                    account_fingerprint,order.symbol,order.client_order_id,
                    order.order_id,order.full_order_id,order.order_type)
                    and Decimal(prior[6]) == order.original_qty)):
                raise DecisionConflictError("M07_RECOVERY_ACCOUNT_OR_ORDER_CONFLICT")
            if order.executed_qty < Decimal(prior[7]):
                raise DecisionConflictError("M07_RECOVERY_EXECUTED_QTY_REGRESSED")
            if observed_ms < prior[9]:
                raise DecisionConflictError("M07_RECOVERY_OBSERVATION_REGRESSED")
            if (prior[8] in ("FILLED","CANCELLED","REJECTED")
                    and (prior[8] != order.status
                         or Decimal(prior[7]) != order.executed_qty)):
                raise DecisionConflictError("M07_RECOVERY_TERMINAL_CONFLICT")
            con.execute(
                "UPDATE m07_readonly_intent_recovery_shadow "
                "SET executed_qty=?,order_status=?,observed_ms=? "
                "WHERE intent_id=?",
                (format(order.executed_qty,"f"),order.status,
                 observed_ms,ident),
            )
        else:
            con.execute(
                "INSERT INTO m07_readonly_intent_recovery_shadow "
                "(intent_id,account_fingerprint,symbol,client_order_id,"
                "exchange_order_id,full_order_id,order_type,original_qty,"
                "executed_qty,order_status,observed_ms,provenance) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,"
                "'ACCOUNT_VIEWONLY_CORRELATION_NOT_OWNERSHIP')",
                (ident,account_fingerprint,order.symbol,order.client_order_id,
                 order.order_id,order.full_order_id,order.order_type,
                 format(order.original_qty,"f"),
                 format(order.executed_qty,"f"),order.status,observed_ms),
            )
        return "M07_RECOVERY_SAME_DRAFT_REFERENCE_ONLY_NO_RELEASE"

    def record_m07_exchange_readonly_fills(
        self, *, account_fingerprint: str,
        fills: M07VerifiedReadOnlyFills, observed_ms: int,
    ) -> dict:
        """Immutable, same-journal historical account receipts, NOT bot lots."""
        if (type(account_fingerprint) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", account_fingerprint)
                or type(fills) is not M07VerifiedReadOnlyFills
                or type(observed_ms) is not int
                or not 1_000_000_000_000 <= observed_ms <= 9_999_999_999_999):
            raise DecisionConflictError("M07_READONLY_FILLS_JOURNAL_INPUT_INVALID")
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            known = con.execute(
                "SELECT full_order_id,side,status,executed_qty,created_ms FROM "
                "m07_exchange_readonly_orders WHERE "
                "account_fingerprint=? AND symbol=? AND client_order_id=?",
                (account_fingerprint,fills.account_symbol,fills.client_order_id),
            ).fetchone()
            if (known is None
                    or known[:3] != (fills.full_order_id,fills.side,"FILLED")
                    or Decimal(known[3]) != fills.executed_base):
                raise DecisionConflictError("M07_READONLY_FILLS_ORDER_NOT_ATTESTED")
            current = (
                fills.full_order_id,fills.side,format(fills.executed_base,"f"),
                format(fills.gross_quote,"f"),
                format(fills.total_commission_including_tax_quote,"f"),
                format(fills.net_quote,"f"),fills.count,fills.sha256,
            )
            old = con.execute(
                "SELECT full_order_id,side,qty_base,gross_quote_idr,"
                "commission_including_tax_idr,net_quote_idr,fill_count,fill_digest "
                "FROM m07_exchange_readonly_fill_summaries WHERE "
                "account_fingerprint=? AND symbol=? AND client_order_id=?",
                (account_fingerprint,fills.account_symbol,fills.client_order_id),
            ).fetchone()
            if old is not None:
                if (old[0],old[1]) != current[:2] or old[6:] != current[6:] or any(
                    Decimal(old[i]) != Decimal(current[i]) for i in range(2,6)
                ):
                    raise DecisionConflictError("M07_READONLY_FILLS_REPLAY_CONFLICT")
                self._m08_record_account_trade_time_in_txn(
                    con, account_fingerprint=account_fingerprint,
                    fills=fills, created_ms=known[4],
                    observed_ms=observed_ms,
                )
                return {"replay":True,"execution_allowed":False}
            con.execute(
                "INSERT INTO m07_exchange_readonly_fill_summaries "
                "(account_fingerprint,symbol,client_order_id,full_order_id,"
                "side,qty_base,gross_quote_idr,commission_including_tax_idr,"
                "net_quote_idr,fill_count,fill_digest,verified_at_ms,provenance)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (account_fingerprint,fills.account_symbol,fills.client_order_id,
                 *current,observed_ms,"INDODAX_TAPIV2_MYTRADES_ACCOUNT_ONLY"),
            )
            self._m08_record_account_trade_time_in_txn(
                con, account_fingerprint=account_fingerprint,
                fills=fills, created_ms=known[4],
                observed_ms=observed_ms,
            )
        return {"replay":False,"execution_allowed":False}

    @staticmethod
    def _m08_record_account_trade_time_in_txn(
            con: sqlite3.Connection, *, account_fingerprint: str,
            fills: M07VerifiedReadOnlyFills, created_ms: int,
            observed_ms: int) -> None:
        """Bind first/last TRADE times to the same original journal receipt.

        Legacy account-only summaries without source timestamps remain
        UNKNOWN. This atomic shadow cannot attest V2 bot order ownership.
        """
        first, last = fills.first_trade_ms, fills.last_trade_ms
        if first is None and last is None:
            return
        if (type(first) is not int or type(last) is not int
                or type(created_ms) is not int
                or not 1_000_000_000_000 <= created_ms <= first <= last
                or last > observed_ms
                or last - first > 7 * 86400 * 1000):
            raise DecisionConflictError("M08_ACCOUNT_FILL_TIME_SOURCE_CONFLICT")
        row = (
            account_fingerprint, fills.account_symbol, fills.client_order_id,
            fills.full_order_id, fills.sha256, first, last,
            "INDODAX_TAPIV2_MYTRADES_ACCOUNT_ONLY",
        )
        old = con.execute(
            "SELECT full_order_id,fill_digest,first_trade_ms,last_trade_ms,"
            "provenance FROM m08_account_buy_trade_times WHERE "
            "account_fingerprint=? AND symbol=? AND client_order_id=?",
            (account_fingerprint,fills.account_symbol,fills.client_order_id),
        ).fetchone()
        if old is not None:
            if old != row[3:]:
                raise DecisionConflictError("M08_ACCOUNT_FILL_TIME_REPLAY_CONFLICT")
        else:
            con.execute(
                "INSERT INTO m08_account_buy_trade_times "
                "(account_fingerprint,symbol,client_order_id,full_order_id,"
                "fill_digest,first_trade_ms,last_trade_ms,provenance) "
                "VALUES (?,?,?,?,?,?,?,?)", row,
            )

    def review_m08_account_buy_position_admission_readonly(
        self, *, account_fingerprint: str, symbol: str,
        client_order_id: str,
    ) -> dict:
        """Original journal M08 BUY-source boundary, never a bot-owned lot.

        The *account* exchange order + complete myTrades fee summary form
        evidence for a potential cost-basis reference. A matching synthetic
        OFFLINE M07 intent is NOT proof this V2 bot submitted that order.
        Therefore this cannot mint inventory, an M08 owned position, a SELL
        reservation, a target order, or available token quantity.
        """
        if (type(account_fingerprint) is not str
                or re.fullmatch(r"[0-9a-f]{64}", account_fingerprint) is None
                or type(symbol) is not str
                or re.fullmatch(r"[A-Z0-9]{2,20}IDR",symbol) is None
                or type(client_order_id) is not str
                or re.fullmatch(r"[A-Za-z0-9_-]{1,36}",client_order_id) is None):
            raise DecisionConflictError("M08_POSITION_REVIEW_SCOPE_INVALID")
        base = {
            "stage": "M08_ORIGINAL_BUY_FILL_POSITION_ADMISSION_READONLY",
            "status": "M08_EXCHANGE_BUY_NOT_OBSERVED",
            "exchange_account_buy_observed": False,
            "account_buy_fills_and_fees_reconciled": False,
            "account_buy_qty_reference": None,
            "account_buy_gross_idr_reference": None,
            "account_buy_fees_including_tax_idr_reference": None,
            "account_buy_cost_basis_idr_reference": None,
            "account_first_buy_fill_epoch_reference": None,
            "account_last_buy_fill_epoch_reference": None,
            "account_buy_fill_timestamp_source": "UNAVAILABLE",
            "local_synthetic_intent_correlation_only": False,
            "independent_bot_order_submit_attested": False,
            "bot_owned_buy_fill_proven": False,
            "verified_bot_owned_lots": 0,
            "verified_bot_owned_base_qty": "0",
            "sellable_bot_owned_base_qty": "0",
            "position_manager_admission": False,
            "m08_sell_target_authorized": False,
            "m08_time_exit_authorized": False,
            "order_sent": False,
            "execution_allowed": False,
        }
        with self._connect() as con:
            order = con.execute(
                "SELECT full_order_id,side,status,executed_qty,provenance,"
                "created_ms "
                "FROM m07_exchange_readonly_orders WHERE "
                "account_fingerprint=? AND symbol=? AND client_order_id=?",
                (account_fingerprint,symbol,client_order_id),
            ).fetchone()
            if order is None:
                return base
            if order[4] != "INDODAX_TAPIV2_GET_ACCOUNT_AND_ORDER":
                raise DecisionConflictError("M08_POSITION_ORDER_SOURCE_INVALID")
            if order[1] != "BUY":
                return dict(base,status="M08_EXCHANGE_ORDER_NOT_BUY")
            base["exchange_account_buy_observed"] = True
            if order[2] != "FILLED":
                return dict(base,status="M08_BUY_FILL_INCOMPLETE_OR_UNSETTLED")
            fill = con.execute(
                "SELECT full_order_id,side,qty_base,gross_quote_idr,"
                "commission_including_tax_idr,net_quote_idr,fill_count,"
                "fill_digest,provenance,verified_at_ms "
                "FROM m07_exchange_readonly_fill_summaries WHERE "
                "account_fingerprint=? AND symbol=? AND client_order_id=?",
                (account_fingerprint,symbol,client_order_id),
            ).fetchone()
            if fill is None:
                return dict(base,status="M08_BUY_FILL_FEE_RECEIPTS_MISSING")
            if (fill[0] != order[0] or fill[1] != "BUY"
                    or fill[8] != "INDODAX_TAPIV2_MYTRADES_ACCOUNT_ONLY"
                    or type(fill[6]) is not int or not 1 <= fill[6] < 1000
                    or type(fill[7]) is not str
                    or re.fullmatch(r"[0-9a-f]{64}",fill[7]) is None):
                raise DecisionConflictError("M08_BUY_FILL_RECEIPT_IDENTITY_CONFLICT")
            try:
                qty,gross,fees,cost = (
                    Decimal(fill[i]) for i in range(2,6))
                executed = Decimal(order[3])
            except (ValueError,ArithmeticError,TypeError):
                raise DecisionConflictError("M08_BUY_COST_SOURCE_CORRUPT") from None
            if (any(not v.is_finite() or v >= Decimal("1e28")
                    for v in (qty,gross,fees,cost,executed))
                    or qty <= 0 or gross <= 0 or fees < 0
                    or fees > gross or qty != executed
                    or cost != gross + fees):
                raise DecisionConflictError("M08_BUY_COST_OR_QUANTITY_CONFLICT")
            # Only trade timestamps validated against the exact original
            # filled order and receipt digest qualify as account-time
            # reference. A submitted order timestamp is NOT a fill.
            time_row = con.execute(
                "SELECT full_order_id,fill_digest,first_trade_ms,last_trade_ms,"
                "provenance FROM m08_account_buy_trade_times WHERE "
                "account_fingerprint=? AND symbol=? AND client_order_id=?",
                (account_fingerprint,symbol,client_order_id),
            ).fetchone()
            first_epoch = last_epoch = None
            if time_row is not None:
                if (time_row[0] != order[0] or time_row[1] != fill[7]
                        or time_row[4] !=
                        "INDODAX_TAPIV2_MYTRADES_ACCOUNT_ONLY"
                        or type(time_row[2]) is not int
                        or type(time_row[3]) is not int
                        or not 1_000_000_000_000 <= order[5]
                        <= time_row[2] <= time_row[3] <= fill[9]):
                    raise DecisionConflictError(
                        "M08_ACCOUNT_FILL_TIME_SOURCE_CONFLICT")
                first_epoch = time_row[2] // 1000
                last_epoch = time_row[3] // 1000
            shadow = con.execute(
                "SELECT r.account_fingerprint,r.symbol,"
                "r.client_order_id,r.full_order_id,r.executed_qty,"
                "r.order_status "
                "FROM m07_readonly_intent_recovery_shadow AS r "
                "JOIN m07_offline_order_intents AS i ON "
                "i.intent_id=r.intent_id "
                "WHERE i.client_order_id=?",
                (client_order_id,),
            ).fetchone()
            if shadow is not None:
                if (shadow[:4] != (account_fingerprint,symbol,
                                   client_order_id,order[0])
                        or Decimal(shadow[4]) != qty
                        or shadow[5] != "FILLED"):
                    raise DecisionConflictError("M08_LOCAL_SHADOW_SOURCE_CONFLICT")
            return {
                **base,
                "status": "M08_ACCOUNT_BUY_COST_BASIS_ONLY_NOT_BOT_OWNED",
                "account_buy_fills_and_fees_reconciled": True,
                "account_buy_qty_reference": format(qty,"f"),
                "account_buy_gross_idr_reference": format(gross,"f"),
                "account_buy_fees_including_tax_idr_reference": format(fees,"f"),
                "account_buy_cost_basis_idr_reference": format(cost,"f"),
                "account_first_buy_fill_epoch_reference": first_epoch,
                "account_last_buy_fill_epoch_reference": last_epoch,
                "account_buy_fill_timestamp_source": (
                    "INDODAX_TAPIV2_MYTRADES_ACCOUNT_ONLY"
                    if first_epoch is not None else "UNAVAILABLE"),
                "local_synthetic_intent_correlation_only": shadow is not None,
            }

    @staticmethod
    def _m08_sell_state_in_txn(
            con: sqlite3.Connection, *, account_fingerprint: str,
            symbol: str, source_buy_client_order_id: str,
            sell_client_order_id: str) -> dict:
        """Immutable-source View Only liability snapshot, never sell coverage.

        Includes all previously observed SAME-account SELL statuses, plus
        global M07 synthetic UNKNOWN and any candidate ID collision. An
        empty View Only table NEVER means that all live orders were checked.
        No holdings, order/fill ownership or settlement inferred.
        """
        rows = con.execute(
            "SELECT client_order_id,full_order_id,status,original_qty,"
            "executed_qty,observed_ms,provenance FROM "
            "m07_exchange_readonly_orders WHERE "
            "account_fingerprint=? AND symbol=? AND side='SELL' "
            "ORDER BY client_order_id",
            (account_fingerprint,symbol),
        ).fetchall()
        pending = Decimal(0)
        part = cancelled = filled = rejected = open_count = 0
        for client_id, full_id, status, original_raw, done_raw, ms, src in rows:
            try:
                total, done = Decimal(original_raw), Decimal(done_raw)
            except (TypeError,ValueError,ArithmeticError):
                raise DecisionConflictError("M08_SELL_ACCOUNT_STATUS_CORRUPT") from None
            if (not total.is_finite() or not done.is_finite()
                    or total <= 0 or done < 0 or done > total
                    or src != "INDODAX_TAPIV2_GET_ACCOUNT_AND_ORDER"
                    or status not in (
                        "NEW","PARTIALLY_FILLED","FILLED","CANCELLED",
                        "REJECTED"
                    )
                    or type(ms) is not int):
                raise DecisionConflictError("M08_SELL_ACCOUNT_STATUS_CORRUPT")
            if status in ("NEW","PARTIALLY_FILLED"):
                open_count += 1
                pending += total - done
            if status == "PARTIALLY_FILLED":
                part += 1
            elif status == "CANCELLED":
                cancelled += 1
            elif status == "FILLED":
                filled += 1
            elif status == "REJECTED":
                rejected += 1
        unknown, reserved = con.execute(
            "SELECT COALESCE(SUM(CASE WHEN state='OFFLINE_UNKNOWN_HOLD' "
            "THEN 1 ELSE 0 END),0),COUNT(*) "
            "FROM m07_offline_order_intents"
        ).fetchone()
        collision = con.execute(
            "SELECT 1 FROM m07_offline_order_intents "
            "WHERE client_order_id=? UNION ALL "
            "SELECT 1 FROM m07_exchange_readonly_orders "
            "WHERE client_order_id=? LIMIT 1",
            (sell_client_order_id,sell_client_order_id),
        ).fetchone() is not None
        buy_order = con.execute(
            "SELECT full_order_id,side,status,executed_qty,observed_ms "
            "FROM m07_exchange_readonly_orders WHERE "
            "account_fingerprint=? AND symbol=? AND client_order_id=?",
            (account_fingerprint,symbol,source_buy_client_order_id),
        ).fetchone()
        buy_fills = con.execute(
            "SELECT full_order_id,qty_base,net_quote_idr,fill_digest,"
            "verified_at_ms FROM m07_exchange_readonly_fill_summaries "
            "WHERE account_fingerprint=? AND symbol=? AND client_order_id=?",
            (account_fingerprint,symbol,source_buy_client_order_id),
        ).fetchone()
        buy_clock = con.execute(
            "SELECT fill_digest,first_trade_ms,last_trade_ms "
            "FROM m08_account_buy_trade_times WHERE "
            "account_fingerprint=? AND symbol=? AND client_order_id=?",
            (account_fingerprint,symbol,source_buy_client_order_id),
        ).fetchone()
        data = (
            account_fingerprint,symbol,source_buy_client_order_id,
            sell_client_order_id,rows,unknown,reserved,collision,
            buy_order,buy_fills,buy_clock,
        )
        digest = hashlib.sha256(json.dumps(
            data, separators=(",",":"),ensure_ascii=True,
        ).encode("ascii")).hexdigest()
        if unknown:
            status = "M08_SELL_UNKNOWN_HOLD_BLOCKED"
        elif collision:
            status = "M08_SELL_DUPLICATE_CLIENT_ID_BLOCKED"
        elif open_count:
            status = "M08_SELL_ACCOUNT_ORDER_UNSETTLED"
        elif cancelled:
            status = "M08_SELL_CANCELLED_NOT_RELEASED"
        elif filled:
            status = "M08_SELL_FILLED_NOT_OWNERSHIP"
        else:
            status = "M08_SELL_ACCOUNT_COVERAGE_UNVERIFIED"
        return {
            "status": status,
            "snapshot_digest": digest,
            "account_seen_sell_count": len(rows),
            "account_seen_open_sell_count": open_count,
            "account_seen_partial_sell_count": part,
            "account_seen_cancelled_sell_count": cancelled,
            "account_seen_filled_sell_count": filled,
            "account_seen_rejected_sell_count": rejected,
            "account_seen_pending_sell_qty_reference": format(pending,"f"),
            "global_m07_unknown_holds": unknown,
            "global_m07_offline_unresolved_intents": reserved,
            "sell_client_id_collision": collision,
            "all_exchange_open_sells_covered": False,
            "cancel_or_fill_releases_owned_inventory": False,
            "executed_sell_qty_attested_bot_owned": False,
            "execution_allowed": False,
        }

    def review_m08_sell_account_liabilities_offline(
            self, *, account_fingerprint: str, symbol: str,
            source_buy_client_order_id: str,
            sell_client_order_id: str) -> dict:
        """Same original journal order states, no extra private GET."""
        if (type(account_fingerprint) is not str
                or not re.fullmatch(r"[0-9a-f]{64}",account_fingerprint)
                or type(symbol) is not str
                or not re.fullmatch(r"[A-Z0-9]{2,20}IDR",symbol)
                or type(source_buy_client_order_id) is not str
                or not re.fullmatch(
                    r"[A-Za-z0-9_-]{1,36}",source_buy_client_order_id)
                or type(sell_client_order_id) is not str
                or not re.fullmatch(
                    r"[A-Za-z0-9_-]{1,36}",sell_client_order_id)
                or source_buy_client_order_id == sell_client_order_id):
            raise DecisionConflictError("M08_SELL_PREFLIGHT_IDENTITY_INVALID")
        with self._connect() as con:
            return self._m08_sell_state_in_txn(
                con,account_fingerprint=account_fingerprint,symbol=symbol,
                source_buy_client_order_id=source_buy_client_order_id,
                sell_client_order_id=sell_client_order_id,
            )

    def record_m08_sell_preflight_denied_once(
            self, *, account_fingerprint: str, symbol: str,
            source_buy_client_order_id: str,
            sell_client_order_id: str, draft_digest: str,
            scenario_digest: str, exchange_state_digest: str,
            observed_epoch: int) -> dict:
        """Idempotent non-order receipt. Recheck fresh LOCAL state atomically.

        This never touches M07 order intents, account balances or sellable
        quantity. Replay with changed local exchange status, UNKNOWN state,
        account identity, scenario or size is always CONFLICT, not a retry.
        """
        if (any(type(d) is not str
                or re.fullmatch(r"[0-9a-f]{64}",d) is None
                for d in (draft_digest,scenario_digest,exchange_state_digest))
                or type(observed_epoch) is not int
                or not 1 <= observed_epoch <= 9999999999):
            raise DecisionConflictError("M08_SELL_PREFLIGHT_RECEIPT_INVALID")
        # Reuse the public scope validation rather than duplicating IDs.
        self.review_m08_sell_account_liabilities_offline(
            account_fingerprint=account_fingerprint, symbol=symbol,
            source_buy_client_order_id=source_buy_client_order_id,
            sell_client_order_id=sell_client_order_id,
        )
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            current = self._m08_sell_state_in_txn(
                con, account_fingerprint=account_fingerprint,
                symbol=symbol,
                source_buy_client_order_id=source_buy_client_order_id,
                sell_client_order_id=sell_client_order_id,
            )
            if current["snapshot_digest"] != exchange_state_digest:
                raise DecisionConflictError(
                    "M08_SELL_PREFLIGHT_EXCHANGE_STATE_CHANGED")
            expected = (
                account_fingerprint,symbol,source_buy_client_order_id,
                draft_digest,scenario_digest,exchange_state_digest,
                observed_epoch,"NONEXECUTING_REVIEW_ONLY",
            )
            old = con.execute(
                "SELECT account_fingerprint,symbol,source_buy_client_order_id,"
                "draft_digest,scenario_digest,exchange_state_digest,"
                "observed_epoch,state FROM m08_offline_sell_preflight_receipts "
                "WHERE sell_client_order_id=?",
                (sell_client_order_id,),
            ).fetchone()
            if old is not None:
                if old != expected:
                    raise DecisionConflictError(
                        "M08_SELL_PREFLIGHT_REPLAY_OR_DUPLICATE_CONFLICT")
                return {
                    "status": "M08_SELL_NONEXECUTING_REVIEW_REPLAY",
                    "replay": True,"new_order_intent_created": False,
                    "new_sell_reservation_created": False,
                    "execution_allowed": False,"order_sent": False,
                }
            con.execute(
                "INSERT INTO m08_offline_sell_preflight_receipts "
                "(sell_client_order_id,account_fingerprint,symbol,"
                "source_buy_client_order_id,draft_digest,scenario_digest,"
                "exchange_state_digest,observed_epoch,state) "
                "VALUES (?,?,?,?,?,?,?,?,'NONEXECUTING_REVIEW_ONLY')",
                (sell_client_order_id,account_fingerprint,symbol,
                 source_buy_client_order_id,draft_digest,scenario_digest,
                 exchange_state_digest,observed_epoch),
            )
        return {
            "status": "M08_SELL_NONEXECUTING_REVIEW_RECORDED",
            "replay": False,"new_order_intent_created": False,
            "new_sell_reservation_created": False,
            "execution_allowed": False,"order_sent": False,
        }

    def claim_m08_post_sell_rediscovery_once(self, *,
            event_id: str, scan_trace_id: str, observed_epoch: int) -> dict:
        """Only claim future M08-06 FINAL verified settlement, once.

        Cannot create POSITION_SETTLED_IDR, invent bot-owned order receipts,
        credit any IDR, clear UNKNOWN, or create BUY/reservations. An absent
        independently reconciled event blocks before the scanner is called.
        A claim is committed BEFORE public scanning to avoid a crash/restart
        re-dispatch loop; retry needs a NEW genuine settlement event.
        """
        if (type(event_id) is not str
                or re.fullmatch(r"[A-Za-z0-9_-]{1,36}",event_id) is None
                or type(scan_trace_id) is not str
                or re.fullmatch(r"[A-Za-z0-9_-]{1,64}",scan_trace_id) is None
                or event_id == scan_trace_id
                or type(observed_epoch) is not int
                or not 1 <= observed_epoch <= 9999999999):
            raise DecisionConflictError("M08_POST_SELL_HANDOFF_ID_INVALID")
        blocked = {
            "stage": "M08_POST_SELL_REDISCOVERY_ORIGINAL_JOURNAL",
            "status": "M08_POSITION_SETTLED_IDR_NOT_VERIFIED",
            "settlement_event_accepted": False,
            "rediscovery_claimed": False,
            "fresh_public_scanner_allowed": False,
            "source_sell_position_bot_owned_verified": False,
            "wallet_idr_settlement_verified": False,
            "spendable_idr_for_buy_verified": False,
            "prior_proposal_or_owner_final_reusable": False,
            "automatic_rebuy_previous_pair": False,
            "buy_order_authorized": False,
            "execution_allowed": False,
        }
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            previous = con.execute(
                "SELECT scan_trace_id FROM m08_post_sell_discovery_claims "
                "WHERE event_id=?", (event_id,),
            ).fetchone()
            if previous is not None:
                return dict(blocked, status="M08_SETTLED_EVENT_ALREADY_CLAIMED")
            trace_collision = con.execute(
                "SELECT 1 FROM m08_post_sell_discovery_claims "
                "WHERE scan_trace_id=? UNION ALL "
                "SELECT 1 FROM pipeline_stage_observations "
                "WHERE trace_id=? UNION ALL SELECT 1 FROM decisions "
                "WHERE trace_id=? LIMIT 1",
                (scan_trace_id,scan_trace_id,scan_trace_id),
            ).fetchone()
            if trace_collision is not None:
                raise DecisionConflictError("M08_POST_SELL_SCAN_TRACE_REUSE")
            event = con.execute(
                "SELECT account_fingerprint,symbol,source_buy_client_order_id,"
                "sell_client_order_id,sell_full_order_id,sell_fill_digest,"
                "bot_owned_execution_digest,idr_wallet_settlement_digest,"
                "net_settled_idr,settled_at_ms,source,status "
                "FROM m08_position_settled_idr_events WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if event is None:
                return blocked
            acc,symbol,buy_cid,sell_cid,full,digest,bot_digest,wallet_digest,net,ms,source,status = event
            if (type(acc) is not str
                    or re.fullmatch(r"[0-9a-f]{64}",acc) is None
                    or type(symbol) is not str
                    or re.fullmatch(r"[A-Z0-9]{2,20}IDR",symbol) is None
                    or symbol[:-3] in ("FLOKI","PEPE","USDT","USDC")
                    or any(type(v) is not str
                           or re.fullmatch(r"[0-9a-f]{64}",v) is None
                           for v in (digest,bot_digest,wallet_digest))
                    or any(type(v) is not str or not v for v in (
                        buy_cid,sell_cid,full))
                    or type(ms) is not int
                    or not 1_000_000_000_000 <= ms <= observed_epoch*1000
                    or (observed_epoch*1000-ms) > 60*60*1000
                    or source != "M08_RECONCILED_BOT_OWNED_SELL_IDR"
                    or status != "POSITION_SETTLED_IDR"):
                raise DecisionConflictError("M08_POST_SELL_SETTLEMENT_SOURCE_INVALID")
            try:
                proceeds = Decimal(net)
            except (TypeError,ValueError,ArithmeticError):
                raise DecisionConflictError("M08_POST_SELL_SETTLEMENT_AMOUNT_INVALID") from None
            if (not proceeds.is_finite() or proceeds <= 0
                    or proceeds >= Decimal("1e28")):
                raise DecisionConflictError("M08_POST_SELL_SETTLEMENT_AMOUNT_INVALID")
            # The same exchange ACCOUNT must also have a FILLED BUY
            # context with reconciled myTrades. This is still NOT proof of
            # V2 ownership; the missing independent bot-origin attestation
            # belongs to the future M08-06 settlement event issuer.
            buy_order = con.execute(
                "SELECT full_order_id,side,status,executed_qty "
                "FROM m07_exchange_readonly_orders WHERE "
                "account_fingerprint=? AND symbol=? AND client_order_id=?",
                (acc,symbol,buy_cid),
            ).fetchone()
            buy_fills = con.execute(
                "SELECT full_order_id,side,qty_base FROM "
                "m07_exchange_readonly_fill_summaries WHERE "
                "account_fingerprint=? AND symbol=? AND client_order_id=?",
                (acc,symbol,buy_cid),
            ).fetchone()
            if (buy_order is None or buy_fills is None
                    or buy_order[1:3] != ("BUY","FILLED")
                    or buy_fills[:2] != (buy_order[0],"BUY")):
                return dict(blocked,status="M08_POST_SELL_BUY_LOT_CONTEXT_MISSING")
            try:
                b_qty = Decimal(buy_order[3])
                b_filled = Decimal(buy_fills[2])
            except (TypeError,ValueError,ArithmeticError):
                raise DecisionConflictError("M08_POST_SELL_BUY_CONTEXT_CORRUPT") from None
            if (not b_qty.is_finite() or b_qty <= 0 or b_qty != b_filled):
                raise DecisionConflictError("M08_POST_SELL_BUY_CONTEXT_CORRUPT")
            # Account View Only SELL and fills are necessary but not
            # sufficient; the future settlement event must separately
            # contain bot execution and wallet-delta reconciler digests.
            order = con.execute(
                "SELECT full_order_id,side,status,original_qty,executed_qty "
                "FROM m07_exchange_readonly_orders WHERE "
                "account_fingerprint=? AND symbol=? AND client_order_id=?",
                (acc,symbol,sell_cid),
            ).fetchone()
            fills = con.execute(
                "SELECT full_order_id,side,qty_base,net_quote_idr,fill_digest "
                "FROM m07_exchange_readonly_fill_summaries WHERE "
                "account_fingerprint=? AND symbol=? AND client_order_id=?",
                (acc,symbol,sell_cid),
            ).fetchone()
            if order is None or fills is None:
                return dict(blocked,status="M08_SELL_ORDER_OR_FILLS_UNVERIFIED")
            try:
                ordered = Decimal(order[3])
                executed = Decimal(order[4])
                fill_qty = Decimal(fills[2])
                fill_net = Decimal(fills[3])
            except (TypeError,ValueError,ArithmeticError):
                raise DecisionConflictError("M08_POST_SELL_FILL_ARITHMETIC_INVALID") from None
            if (order[:3] != (full,"SELL","FILLED")
                    or fills[:2] != (full,"SELL")
                    or fills[4] != digest
                    or any(not q.is_finite() for q in (
                        ordered,executed,fill_qty,fill_net))
                    or not (0 < ordered == executed == fill_qty)
                    or fill_net != proceeds):
                raise DecisionConflictError("M08_POST_SELL_FILL_SETTLEMENT_CONFLICT")
            unresolved = con.execute(
                "SELECT COUNT(*) FROM m07_offline_order_intents"
            ).fetchone()[0]
            active_sells = con.execute(
                "SELECT COUNT(*) FROM m07_exchange_readonly_orders "
                "WHERE account_fingerprint=? AND symbol=? AND side='SELL' "
                "AND status IN ('NEW','PARTIALLY_FILLED')",
                (acc,symbol),
            ).fetchone()[0]
            if unresolved or active_sells:
                return dict(blocked,status="M08_POST_SELL_UNRESOLVED_ORDER_COMMITMENTS")
            # M08-06 is not yet an authoritative producer. No event has
            # been minted by V2 in production. The atomic claim exists
            # solely as a future handoff once trusted provenance arrives.
            con.execute(
                "INSERT INTO m08_post_sell_discovery_claims "
                "(event_id,scan_trace_id,claimed_at_epoch,scanner_name) "
                "VALUES (?,?,?,'ORIGINAL_M03_BULK_DEEP')",
                (event_id,scan_trace_id,observed_epoch),
            )
        return {
            **blocked,
            "status": "M08_SETTLED_EVENT_CLAIMED_SCANNER_ONLY",
            "settlement_event_accepted": True,
            "rediscovery_claimed": True,
            "fresh_public_scanner_allowed": True,
            "source_sell_position_bot_owned_verified": True,
            "wallet_idr_settlement_verified": True,
            # a settled event alone never validates whole-account funds;
            # only new source-bound M06/M07 preflight can do that.
            "spendable_idr_for_buy_verified": False,
        }

    def review_m08_sell_settlement_issuer_admission_offline(
            self, *, account_fingerprint: str, symbol: str,
            source_buy_client_order_id: str, sell_client_order_id: str,
            observed_epoch: int) -> dict:
        """M08-06 future issuer admission evidence, NEVER an issuer.

        Read ONLY the SAME original V2 journal's account-observed BUY/SELL
        order and myTrades fee receipts. Account GETs and clientOrderID
        matching do NOT independently attest V2-owned order creation.
        No verified account wallet delta, canonical issuer, FILLED bot-owned
        position, settled IDR, M11 risk release, or actual net PnL exists.
        This method cannot insert a settlement event or any order intent.
        """
        if not getattr(self, "_m11_readonly_fixture", False):
            raise DecisionConflictError("M08_SETTLEMENT_READONLY_JOURNAL_REQUIRED")
        if (type(account_fingerprint) is not str
                or re.fullmatch(r"[0-9a-f]{64}", account_fingerprint) is None
                or type(symbol) is not str
                or re.fullmatch(r"[A-Z0-9]{2,20}IDR", symbol) is None
                or symbol[:-3] in ("FLOKI", "PEPE", "USDT", "USDC")
                or type(source_buy_client_order_id) is not str
                or re.fullmatch(r"[A-Za-z0-9_-]{1,36}",
                                source_buy_client_order_id) is None
                or type(sell_client_order_id) is not str
                or re.fullmatch(r"[A-Za-z0-9_-]{1,36}",
                                sell_client_order_id) is None
                or source_buy_client_order_id == sell_client_order_id
                or type(observed_epoch) is not int
                or not 1 <= observed_epoch <= 9999999999):
            raise DecisionConflictError("M08_SETTLEMENT_IDENTITY_INVALID")

        reasons = [
            "M08_BOT_OWNED_ORDER_ORIGIN_NOT_ATTESTED",
            "M08_WALLET_FREE_LOCKED_IDR_DELTA_NOT_RECONCILED",
            "M08_SETTLED_WIB_REALIZED_PNL_ISSUER_MISSING",
            "M08_ACCOUNT_WIDE_ORDER_AND_PEER_COVERAGE_UNVERIFIED",
        ]
        buy_ok = sell_ok = quantity_ok = False
        buy_cost = sell_net = pnl_reference = None
        sell_fill_digest = None
        pending = unknown = 0

        with self._connect() as con:
            con.execute("BEGIN")
            events_before = con.execute(
                "SELECT COUNT(*) FROM m08_position_settled_idr_events"
            ).fetchone()[0]
            claims_before = con.execute(
                "SELECT COUNT(*) FROM m08_post_sell_discovery_claims"
            ).fetchone()[0]
            unknown, pending = con.execute(
                "SELECT COALESCE(SUM(CASE WHEN state='OFFLINE_UNKNOWN_HOLD' "
                "THEN 1 ELSE 0 END),0),COUNT(*) "
                "FROM m07_offline_order_intents"
            ).fetchone()
            active_sells = con.execute(
                "SELECT COUNT(*) FROM m07_exchange_readonly_orders "
                "WHERE account_fingerprint=? AND symbol=? AND side='SELL' "
                "AND status IN ('NEW','PARTIALLY_FILLED')",
                (account_fingerprint, symbol),
            ).fetchone()[0]
            def get(side, client_id):
                order = con.execute(
                    "SELECT full_order_id,side,status,original_qty,"
                    "executed_qty,observed_ms,provenance FROM "
                    "m07_exchange_readonly_orders WHERE "
                    "account_fingerprint=? AND symbol=? AND client_order_id=?",
                    (account_fingerprint, symbol, client_id),
                ).fetchone()
                fills = con.execute(
                    "SELECT full_order_id,side,qty_base,gross_quote_idr,"
                    "commission_including_tax_idr,net_quote_idr,fill_count,"
                    "fill_digest,verified_at_ms,provenance FROM "
                    "m07_exchange_readonly_fill_summaries WHERE "
                    "account_fingerprint=? AND symbol=? AND client_order_id=?",
                    (account_fingerprint, symbol, client_id),
                ).fetchone()
                if order is None or fills is None:
                    reasons.append(f"M08_{side}_ORDER_OR_FILLS_MISSING")
                    return None
                if (order[1:3] != (side, "FILLED")
                        or fills[0:2] != (order[0], side)
                        or order[6] != "INDODAX_TAPIV2_GET_ACCOUNT_AND_ORDER"
                        or fills[9] != "INDODAX_TAPIV2_MYTRADES_ACCOUNT_ONLY"
                        or type(fills[6]) is not int
                        or not 1 <= fills[6] < 1000
                        or type(fills[7]) is not str
                        or re.fullmatch(r"[a-f0-9]{64}", fills[7]) is None):
                    reasons.append(f"M08_{side}_ORDER_OR_FILL_IDENTITY_UNVERIFIED")
                    return None
                try:
                    qty, gross, fee, net = (
                        Decimal(fills[k]) for k in range(2, 6))
                    original, executed = Decimal(order[3]), Decimal(order[4])
                except (ValueError, TypeError, ArithmeticError):
                    reasons.append(f"M08_{side}_FILL_AMOUNT_INVALID")
                    return None
                if (any(not v.is_finite() or abs(v) >= Decimal("1e28")
                        for v in (qty, gross, fee, net, original, executed))
                        or not (original == executed == qty > 0)
                        or gross <= 0 or fee < 0 or fee > gross
                        or net != (gross + fee if side == "BUY"
                                   else gross - fee)
                        or net <= 0):
                    reasons.append(f"M08_{side}_FILL_QUANTITY_OR_FEE_CONFLICT")
                    return None
                max_ms = observed_epoch * 1000
                if (type(order[5]) is not int
                        or type(fills[8]) is not int
                        or not 1_000_000_000_000 <= order[5] <= max_ms
                        or not 1_000_000_000_000 <= fills[8] <= max_ms
                        or (side == "SELL" and
                            max_ms - max(order[5], fills[8]) > 3600 * 1000)):
                    reasons.append(f"M08_{side}_RECEIPT_FRESHNESS_UNVERIFIED")
                    return None
                return qty, net, fills[7]

            buy = get("BUY", source_buy_client_order_id)
            sell = get("SELL", sell_client_order_id)
            buy_ok = buy is not None
            sell_ok = sell is not None
            if buy is not None and sell is not None:
                quantity_ok = (buy[0] == sell[0])
                if not quantity_ok:
                    reasons.append("M08_SELL_QTY_NOT_FULL_BUY_LOT")
                else:
                    buy_cost = format(buy[1], "f")
                    sell_net = format(sell[1], "f")
                    pnl_reference = format(sell[1] - buy[1], "f")
                    sell_fill_digest = sell[2]
            if pending or unknown:
                reasons.append("M08_M07_UNRESOLVED_OR_UNKNOWN_INTENT")
            if active_sells:
                reasons.append("M08_ACTIVE_EXCHANGE_SELL_NOT_SETTLED")
            # NEVER insert into m08_position_settled_idr_events. Existing
            # rows may be synthetic from old offline tests and are NOT issuer
            # evidence. The M08-07 handoff remains separately gated.
            events_after = con.execute(
                "SELECT COUNT(*) FROM m08_position_settled_idr_events"
            ).fetchone()[0]
            claims_after = con.execute(
                "SELECT COUNT(*) FROM m08_post_sell_discovery_claims"
            ).fetchone()[0]
            if (events_after != events_before or claims_after != claims_before):
                raise DecisionConflictError("M08_SETTLEMENT_READONLY_MUTATION")
        return {
            "stage": "M08_06_ORIGINAL_SELL_SETTLEMENT_ISSUER_ADMISSION_OFFLINE",
            "status": "M08_06_BLOCK_UNVERIFIED_BOT_ORIGIN_AND_IDR_SETTLEMENT",
            "reason_codes": sorted(set(reasons)),
            "account_only_buy_fill_fees_reconciled": buy_ok,
            "account_only_sell_fill_fees_reconciled": sell_ok,
            "account_only_full_buy_qty_matched": quantity_ok,
            "account_buy_cost_idr_reference": buy_cost,
            "account_sell_net_idr_reference": sell_net,
            "account_pnl_idr_reference_only_unverified": pnl_reference,
            "sell_fill_digest_reference_present": sell_fill_digest is not None,
            "m07_unresolved_intents_observed": pending,
            "m07_unknown_holds_observed": unknown,
            "account_open_sells_observed": active_sells,
            "m08_settlement_event_rows_before": events_before,
            "m08_settlement_event_rows_after": events_after,
            "m08_post_sell_claim_rows_before": claims_before,
            "m08_post_sell_claim_rows_after": claims_after,
            "independent_v2_bot_order_origin_verified": False,
            "verified_owned_position_available": False,
            "all_account_open_orders_reconciled": False,
            "wallet_settled_free_locked_idr_verified": False,
            "realized_wib_net_idr_verified": False,
            "canonical_m08_settlement_issuer_connected": False,
            "settlement_event_issued": False,
            "owner_final_decision": None,
            "private_gets_performed": 0,
            "order_sent": False,
            "execution_allowed": False,
            "live_trading_authorized": False,
        }

    def review_m09_original_m08_position_and_idr_readonly(self) -> dict:
        """Original M08 journal posture for M09 PAPER/SHADOW, not a fill.

        View Only BUYs and manually seeded future-event rows NEVER prove
        bot-created BUY, SELL settlement or realized IDR PnL. This original
        journal has NO canonical M08-06 event issuer yet; no settlement
        amount can be promoted into a trade-permission signal.
        """
        with self._connect() as con:
            buy_receipts = con.execute(
                "SELECT COUNT(*) FROM m07_exchange_readonly_fill_summaries "
                "WHERE side='BUY'",
            ).fetchone()[0]
            sell_receipts = con.execute(
                "SELECT COUNT(*) FROM m07_exchange_readonly_fill_summaries "
                "WHERE side='SELL'",
            ).fetchone()[0]
            proposed_exits = con.execute(
                "SELECT COUNT(*) FROM m08_offline_sell_preflight_receipts",
            ).fetchone()[0]
            stored_events = con.execute(
                "SELECT COUNT(*) FROM m08_position_settled_idr_events",
            ).fetchone()[0]
            unknown = con.execute(
                "SELECT COUNT(*) FROM m07_offline_order_intents "
                "WHERE state='OFFLINE_UNKNOWN_HOLD'",
            ).fetchone()[0]
        return {
            "stage": "M09_ORIGINAL_M08_POSITION_SETTLEMENT_SHADOW",
            "status": "M08_NO_AUTHENTICATED_BOT_OWNED_SETTLEMENT_ISSUER",
            "account_only_buy_receipt_count": buy_receipts,
            "account_only_sell_receipt_count": sell_receipts,
            "nonexecuting_sell_preflight_count": proposed_exits,
            "future_event_db_rows_untrusted": stored_events,
            "m07_offline_unknown_holds": unknown,
            "bot_owned_position_verified": False,
            "bot_owned_sell_fill_verified": False,
            "wallet_settled_idr_verified": False,
            "realized_net_pnl_wib_verified": False,
            "paper_is_realized_exchange_proceeds": False,
            "spendable_idr_authorized": False,
            "order_sent": False,
            "execution_allowed": False,
        }

    @staticmethod
    def _m09_paper_id(value: str, prefix: str) -> str:
        """Dedicated paper ID namespace, never an Indodax clientOrderId."""
        if (type(value) is not str
                or re.fullmatch(prefix+r"[A-Za-z0-9_-]{1,30}",value)
                is None):
            raise DecisionConflictError("M09_PAPER_IDENTITY_INVALID")
        return value

    @staticmethod
    def _m09_paper_decimal(value: Decimal, *, positive: bool) -> Decimal:
        if (type(value) is not Decimal or not value.is_finite()
                or value >= Decimal("1e25")
                or (value <= 0 if positive else value < 0)):
            raise DecisionConflictError("M09_PAPER_AMOUNT_INVALID")
        return value

    @staticmethod
    def _m09_paper_epoch(value: int) -> int:
        if type(value) is not int or not 1500000000 <= value <= 4000000000:
            raise DecisionConflictError("M09_PAPER_EPOCH_INVALID")
        return value

    @staticmethod
    def _m09_paper_source_only() -> dict:
        return {
            "source": "M09_PAPER_SCENARIO_ONLY",
            "paper_trade_uses_simulated_prices_fees": True,
            "paper_trade_does_not_consume_exchange_balance": True,
            "paper_is_exchange_confirmed_fill": False,
            "paper_is_bot_owned_real_lot": False,
            "real_exchange_wallet_idr_delta": None,
            "real_exchange_realized_pnl_idr": None,
            "owner_order_final_verified": False,
            "authenticated_exchange_settlement_verified": False,
            "spendable_idr_authorized": False,
            "m08_position_settled_idr_created": False,
            "live_trading_authorized": False,
            "order_sent": False,
            "execution_allowed": False,
        }

    @contextmanager
    def _m09_paper_write(self, con=None):
        """Share the existing PAPER write transaction with automatic steps."""
        if con is not None:
            if not con.in_transaction:
                raise DecisionConflictError("M09_PAPER_TRANSACTION_REQUIRED")
            yield con
        else:
            with self._connect() as local:
                local.execute("BEGIN IMMEDIATE")
                # A configured auto wallet must not mix manual fixture fills.
                exists=local.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='m09_paper_auto_state'").fetchone()
                if exists and local.execute(
                        "SELECT 1 FROM m09_paper_auto_state WHERE id=1").fetchone():
                    raise DecisionConflictError("M09_PAPER_AUTO_WALLET_MANAGED")
                yield local

    def record_m09_paper_buy_offline(self, *, paper_id: str,
            symbol: str, buy_qty: Decimal, buy_price_idr: Decimal,
            buy_all_in_fee_idr: Decimal, buy_epoch: int, _paper_con=None) -> dict:
        """Simulate an IDR BUY, NEVER create exchange order/bot-owned lot."""
        self._m09_paper_id(paper_id,"paper_")
        if (type(symbol) is not str
                or re.fullmatch(r"[A-Z0-9]{2,20}IDR",symbol) is None
                or symbol[:-3] in ("FLOKI","PEPE")):
            raise DecisionConflictError("M09_PAPER_SYMBOL_SCOPE_INVALID")
        qty = self._m09_paper_decimal(buy_qty,positive=True)
        price = self._m09_paper_decimal(buy_price_idr,positive=True)
        fee = self._m09_paper_decimal(buy_all_in_fee_idr,positive=False)
        epoch = self._m09_paper_epoch(buy_epoch)
        cost = qty*price+fee
        if not cost.is_finite() or not 0 < cost < Decimal("1e25"):
            raise DecisionConflictError("M09_PAPER_BUY_COST_INVALID")
        row=(paper_id,symbol,format(qty,"f"),format(price,"f"),
             format(fee,"f"),epoch,"M09_PAPER_SCENARIO_ONLY")
        with self._m09_paper_write(_paper_con) as con:
            stored=con.execute(
                "SELECT paper_id,symbol,buy_qty,buy_price_idr,"
                "buy_all_in_fee_idr,buy_epoch,source "
                "FROM m09_paper_simulated_buys WHERE paper_id=?",
                (paper_id,),
            ).fetchone()
            if stored is not None:
                if stored != row:
                    raise DecisionConflictError("M09_PAPER_BUY_REPLAY_CONFLICT")
                replay=True
            else:
                # One PAPER position globally, including partial exits.
                # The existing BEGIN IMMEDIATE serializes competing BUYs
                # and SELLs; no new ledger, lock, or real position authority.
                buys=con.execute(
                    "SELECT paper_id,buy_qty,buy_epoch,source "
                    "FROM m09_paper_simulated_buys",
                ).fetchall()
                sells=con.execute(
                    "SELECT paper_id,sell_qty,sell_epoch,source "
                    "FROM m09_paper_simulated_sells",
                ).fetchall()
                positions={}
                last_exit_epoch=0
                try:
                    for pid,raw_qty,stamp,source in buys:
                        amount=Decimal(raw_qty)
                        if (source!="M09_PAPER_SCENARIO_ONLY"
                                or not amount.is_finite() or amount<=0
                                or type(stamp) is not int):
                            raise ValueError("invalid buy receipt")
                        positions[pid]=[amount,stamp]
                    for pid,raw_qty,stamp,source in sells:
                        amount=Decimal(raw_qty)
                        if (source!="M09_PAPER_SCENARIO_ONLY"
                                or pid not in positions
                                or not amount.is_finite() or amount<=0
                                or type(stamp) is not int
                                or stamp<positions[pid][1]):
                            raise ValueError("invalid sell receipt")
                        positions[pid][0]-=amount
                        if positions[pid][0]<0:
                            raise ValueError("oversold paper receipt")
                        last_exit_epoch=max(last_exit_epoch,stamp)
                except (TypeError,ValueError,ArithmeticError):
                    raise DecisionConflictError(
                        "M09_PAPER_POSITION_HISTORY_CORRUPT") from None
                if any(remaining>0 for remaining,_ in positions.values()):
                    raise DecisionConflictError("M09_PAPER_POSITION_ALREADY_OPEN")
                if epoch<last_exit_epoch:
                    raise DecisionConflictError("M09_PAPER_BUY_PRECEDES_LAST_EXIT")
                con.execute(
                    "INSERT INTO m09_paper_simulated_buys "
                    "(paper_id,symbol,buy_qty,buy_price_idr,"
                    "buy_all_in_fee_idr,buy_epoch,source)"
                    "VALUES (?,?,?,?,?,?,?)",row,
                )
                replay=False
        return {
            **self._m09_paper_source_only(),
            "status":"M09_SIMULATED_BUY_REPLAY" if replay
                     else "M09_SIMULATED_BUY_OPEN",
            "paper_id":paper_id,"paper_symbol":symbol,
            "paper_buy_cost_idr":format(cost,"f"),
            "paper_buy_qty":format(qty,"f"),
            "replay":replay,
        }

    def record_m09_paper_sell_offline(self, *, paper_id: str,
            paper_exit_id: str, sell_qty: Decimal,
            sell_price_idr: Decimal, sell_all_in_fee_idr: Decimal,
            incremental_slippage_idr: Decimal, sell_epoch: int,
            _paper_con=None) -> dict:
        """Simulate partial/final SELL with allocated BUY cost, PAPER ONLY.

        Explicit sell fee and incremental slippage are deducted just once.
        Atomic per-exit receipt prevents oversell and duplicate after
        restart, without touching real M07/M08 exchange/owned inventory.
        """
        self._m09_paper_id(paper_id,"paper_")
        self._m09_paper_id(paper_exit_id,"pexit_")
        qty=self._m09_paper_decimal(sell_qty,positive=True)
        price=self._m09_paper_decimal(sell_price_idr,positive=True)
        fee=self._m09_paper_decimal(sell_all_in_fee_idr,positive=False)
        slip=self._m09_paper_decimal(
            incremental_slippage_idr,positive=False)
        epoch=self._m09_paper_epoch(sell_epoch)
        gross=qty*price
        if (not gross.is_finite() or not 0 < gross < Decimal("1e25")
                or fee+slip > gross):
            raise DecisionConflictError("M09_PAPER_SELL_NET_INVALID")
        with self._m09_paper_write(_paper_con) as con:
            parent=con.execute(
                "SELECT symbol,buy_qty,buy_price_idr,buy_all_in_fee_idr,"
                "buy_epoch,source FROM m09_paper_simulated_buys "
                "WHERE paper_id=?", (paper_id,),
            ).fetchone()
            if parent is None:
                raise DecisionConflictError("M09_PAPER_BUY_RECEIPT_MISSING")
            if parent[5]!="M09_PAPER_SCENARIO_ONLY" or epoch<parent[4]:
                raise DecisionConflictError("M09_PAPER_BUY_SOURCE_OR_TIME_INVALID")
            try:
                buy_qty=Decimal(parent[1])
                buy_price=Decimal(parent[2])
                buy_fee=Decimal(parent[3])
            except (TypeError,ValueError,ArithmeticError):
                raise DecisionConflictError("M09_PAPER_BUY_RECEIPT_CORRUPT") from None
            if (not all(x.is_finite() for x in (buy_qty,buy_price,buy_fee))
                    or buy_qty<=0 or buy_price<=0 or buy_fee<0):
                raise DecisionConflictError("M09_PAPER_BUY_RECEIPT_CORRUPT")
            buy_cost=buy_qty*buy_price+buy_fee
            allocated_cost=buy_cost*qty/buy_qty
            projected_pnl=(gross-fee-slip)-allocated_cost
            if not projected_pnl.is_finite():
                raise DecisionConflictError("M09_PAPER_SELL_NET_INVALID")
            incoming=(paper_exit_id,paper_id,format(qty,"f"),
                      format(price,"f"),format(fee,"f"),
                      format(slip,"f"),format(projected_pnl,"f"),
                      epoch,"M09_PAPER_SCENARIO_ONLY")
            previous=con.execute(
                "SELECT paper_exit_id,paper_id,sell_qty,sell_price_idr,"
                "sell_all_in_fee_idr,incremental_slippage_idr,"
                "paper_net_pnl_idr,sell_epoch,source "
                "FROM m09_paper_simulated_sells WHERE paper_exit_id=?",
                (paper_exit_id,),
            ).fetchone()
            if previous is not None:
                if previous!=incoming:
                    raise DecisionConflictError("M09_PAPER_SELL_REPLAY_CONFLICT")
                replay=True
            else:
                history=con.execute(
                    "SELECT sell_qty,sell_epoch FROM "
                    "m09_paper_simulated_sells WHERE paper_id=?",
                    (paper_id,),
                ).fetchall()
                try:
                    done=sum((Decimal(r[0]) for r in history),Decimal(0))
                except (TypeError,ValueError,ArithmeticError):
                    raise DecisionConflictError("M09_PAPER_SELL_HISTORY_CORRUPT") from None
                if (not done.is_finite() or done<0 or done+qty>buy_qty
                        or any(type(r[1]) is not int or r[1]>epoch
                               for r in history)):
                    raise DecisionConflictError("M09_PAPER_OVERSELL_OR_TIME_CONFLICT")
                con.execute(
                    "INSERT INTO m09_paper_simulated_sells "
                    "(paper_exit_id,paper_id,sell_qty,sell_price_idr,"
                    "sell_all_in_fee_idr,incremental_slippage_idr,"
                    "paper_net_pnl_idr,sell_epoch,source) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",incoming,
                )
                replay=False
            total=con.execute(
                "SELECT sell_qty,paper_net_pnl_idr FROM "
                "m09_paper_simulated_sells WHERE paper_id=?",
                (paper_id,),
            ).fetchall()
            cumulative_qty=sum((Decimal(t[0]) for t in total),Decimal(0))
            cumulative_pnl=sum((Decimal(t[1]) for t in total),Decimal(0))
        if cumulative_qty>buy_qty:
            raise DecisionConflictError("M09_PAPER_OVERSELL_OR_TIME_CONFLICT")
        return {
            **self._m09_paper_source_only(),
            "status":"M09_PAPER_CLOSED" if cumulative_qty==buy_qty
                     else "M09_PAPER_PARTIAL",
            "paper_id":paper_id,"paper_exit_id":paper_exit_id,
            "paper_exit_net_pnl_idr":format(projected_pnl,"f"),
            "paper_position_total_net_pnl_idr":
                format(cumulative_pnl,"f"),
            "paper_remaining_qty":format(buy_qty-cumulative_qty,"f"),
            "replay":replay,
        }

    def read_m09_open_paper_position_offline(self, *, paper_id: str,
            observed_epoch: int) -> dict:
        """Exact remaining virtual lot and cost basis, never a real holding."""
        self._m09_paper_id(paper_id,"paper_")
        self._m09_paper_epoch(observed_epoch)
        with self._connect() as con:
            con.execute("BEGIN")
            buy=con.execute(
                "SELECT symbol,buy_qty,buy_price_idr,buy_all_in_fee_idr,"
                "buy_epoch,source FROM m09_paper_simulated_buys WHERE paper_id=?",
                (paper_id,),
            ).fetchone()
            sells=con.execute(
                "SELECT sell_qty,sell_epoch,source FROM "
                "m09_paper_simulated_sells WHERE paper_id=?",
                (paper_id,),
            ).fetchall()
        if buy is None:
            raise DecisionConflictError("M09_PAPER_BUY_RECEIPT_MISSING")
        try:
            symbol,qty_raw,price_raw,fee_raw,epoch,source=buy
            qty=self._m09_paper_decimal(Decimal(qty_raw),positive=True)
            price=self._m09_paper_decimal(Decimal(price_raw),positive=True)
            fee=self._m09_paper_decimal(Decimal(fee_raw),positive=False)
            self._m09_paper_epoch(epoch)
            if (source!="M09_PAPER_SCENARIO_ONLY"
                    or type(symbol) is not str
                    or re.fullmatch(r"[A-Z0-9]{2,20}IDR",symbol) is None
                    or symbol[:-3] in ("FLOKI","PEPE","USDT","USDC")
                    or observed_epoch<epoch):
                raise ValueError("invalid paper buy")
            used=Decimal(0)
            for raw,stamp,scope in sells:
                self._m09_paper_epoch(stamp)
                if (scope!="M09_PAPER_SCENARIO_ONLY"
                        or not epoch<=stamp<=observed_epoch):
                    raise ValueError("invalid paper exit")
                used+=self._m09_paper_decimal(Decimal(raw),positive=True)
            if used>qty:
                raise ValueError("oversold paper")
        except (ValueError,TypeError,ArithmeticError,DecisionConflictError):
            raise DecisionConflictError("M09_PAPER_POSITION_SOURCE_INVALID") from None
        left=qty-used
        if left==0:
            raise DecisionConflictError("M09_PAPER_POSITION_CLOSED")
        return {"symbol":symbol,"buy_epoch":epoch,"remaining_qty":left,
                "remaining_cost_idr":(qty*price+fee)*left/qty}

    def review_m09_paper_pnl_offline(self, *,
            day_wib: str | None = None) -> dict:
        """Virtual PAPER realized-at-simulated-SELL only, WIB day boundary.

        Never references the exchange IDR balance or real settlement
        events. Day grouping is based on simulated SELL time in WIB.
        """
        if day_wib is not None:
            if (type(day_wib) is not str
                    or re.fullmatch(r"\d{4}-\d{2}-\d{2}",day_wib)
                    is None):
                raise DecisionConflictError("M09_PAPER_WIB_DATE_INVALID")
            try:
                if datetime.strptime(day_wib,"%Y-%m-%d").strftime(
                        "%Y-%m-%d")!=day_wib:
                    raise ValueError("bad date")
            except ValueError:
                raise DecisionConflictError("M09_PAPER_WIB_DATE_INVALID") from None
        wib=ZoneInfo("Asia/Jakarta")
        with self._connect() as con:
            buys=con.execute(
                "SELECT paper_id,buy_qty,source "
                "FROM m09_paper_simulated_buys",
            ).fetchall()
            sells=con.execute(
                "SELECT paper_id,sell_qty,paper_net_pnl_idr,"
                "sell_epoch,source FROM m09_paper_simulated_sells",
            ).fetchall()
        all_qty={}
        for pid,raw_qty,source in buys:
            if source!="M09_PAPER_SCENARIO_ONLY":
                raise DecisionConflictError("M09_PAPER_RECEIPT_CORRUPT")
            try:
                qty=Decimal(raw_qty)
            except (TypeError,ValueError,ArithmeticError):
                raise DecisionConflictError("M09_PAPER_RECEIPT_CORRUPT") from None
            if not qty.is_finite() or qty<=0:
                raise DecisionConflictError("M09_PAPER_RECEIPT_CORRUPT")
            all_qty[pid]=qty
        totals={pid:Decimal(0) for pid in all_qty}
        relevant=[]
        for pid,qty_raw,pnl_raw,epoch,source in sells:
            if source!="M09_PAPER_SCENARIO_ONLY" or pid not in totals:
                raise DecisionConflictError("M09_PAPER_RECEIPT_CORRUPT")
            try:
                qty=Decimal(qty_raw)
                pnl=Decimal(pnl_raw)
                day=datetime.fromtimestamp(epoch,wib).strftime("%Y-%m-%d")
            except (TypeError,ValueError,ArithmeticError,OverflowError):
                raise DecisionConflictError("M09_PAPER_RECEIPT_CORRUPT") from None
            if (not qty.is_finite() or not pnl.is_finite() or qty<=0):
                raise DecisionConflictError("M09_PAPER_RECEIPT_CORRUPT")
            totals[pid]+=qty
            if day_wib is None or day==day_wib:
                relevant.append(pnl)
        if any(totals[pid]>all_qty[pid] for pid in totals):
            raise DecisionConflictError("M09_PAPER_RECEIPT_OVERSELL_CORRUPT")
        net=sum(relevant,Decimal(0))
        return {
            **self._m09_paper_source_only(),
            "stage":"M09_VIRTUAL_PAPER_PNL_SEPARATE",
            "status":"M09_PAPER_ONLY_NOT_EXCHANGE_REALIZED",
            "day_wib_filter":day_wib,
            "paper_buy_count":len(buys),
            "paper_exit_count":len(relevant),
            "paper_open_or_partial_positions":sum(
                qty<all_qty[pid] for pid,qty in totals.items()),
            "paper_fully_closed_positions":sum(
                qty==all_qty[pid] for pid,qty in totals.items()),
            "paper_positive_exit_count":sum(x>0 for x in relevant),
            "paper_negative_exit_count":sum(x<0 for x in relevant),
            "paper_net_pnl_idr_reference":format(net,"f"),
        }

    def m07_offline_intent_summary(self) -> dict:
        """Anonymized original journal status; no account/API/order secrets."""
        with self._connect() as con:
            total, unknown = con.execute(
                "SELECT COUNT(*), COALESCE(SUM("
                "CASE WHEN state='OFFLINE_UNKNOWN_HOLD' THEN 1 ELSE 0 END"
                "),0) FROM m07_offline_order_intents"
            ).fetchone()
            local_total, local_uncertain = con.execute(
                "SELECT COUNT(*),COALESCE(SUM(CASE WHEN "
                "state='OFFLINE_UNCERTAIN_HOLD_NO_RETRY' THEN 1 ELSE 0 END),0)"
                " FROM m07_prepared_origin_no_submit"
            ).fetchone()
            manual_total, manual_unknown = con.execute(
                "SELECT COUNT(*),COALESCE(SUM(CASE WHEN "
                "state='OFFLINE_UNKNOWN_HOLD_NO_RETRY' THEN 1 ELSE 0 END),0)"
                " FROM m07_manual_sender_nonlive"
            ).fetchone()
            correlated, partial, terminal, still_unknown = con.execute(
                "SELECT COUNT(*),"
                "COALESCE(SUM(CASE WHEN r.order_status='PARTIALLY_FILLED' "
                "THEN 1 ELSE 0 END),0),"
                "COALESCE(SUM(CASE WHEN r.order_status IN "
                "('FILLED','CANCELLED','REJECTED') THEN 1 ELSE 0 END),0),"
                "COALESCE(SUM(CASE WHEN i.state='OFFLINE_UNKNOWN_HOLD' "
                "THEN 1 ELSE 0 END),0) "
                "FROM m07_readonly_intent_recovery_shadow AS r "
                "JOIN m07_offline_order_intents AS i "
                "ON i.intent_id=r.intent_id"
            ).fetchone()
        return {
            "stage": "M07_ORIGINAL_OFFLINE_INTENT_GUARD",
            # Immutable source correlation survives restart, but never
            # turns an OFFLINE synthetic intent into a real bot-owned trade.
            "viewonly_recovery_references_not_bot_owned": correlated,
            "viewonly_recovery_partial_status_observed": partial,
            "viewonly_recovery_terminal_status_observed": terminal,
            "viewonly_recovery_unknown_hold_still_blocked": still_unknown,
            "viewonly_recovery_does_not_release_or_retry": True,
            "unresolved_simulated_intents": total,
            "offline_unknown_holds": unknown,
            "additional_buy_reservations_blocked": (
                unknown > 0 or local_uncertain > 0
                or manual_unknown > 0
                or total + local_total + manual_total >= 2),
            "manual_sender_nonlive_holds": manual_total,
            "manual_sender_nonlive_unknown_holds": manual_unknown,
            "manual_sender_no_order_issuer": True,
            "local_origin_prepared_no_submit_count": local_total,
            "local_origin_uncertain_no_retry_count": local_uncertain,
            "local_origin_does_not_prove_order_issuer": True,
            "exchange_order_state_authenticated": False,
            "owner_spendable_balance_verified": False,
            "order_sent": False,
            "execution_allowed": False,
        }

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

    def claim_m04_candidate_evidence_once(self, *, pair: str,
                                          evidence_key: str,
                                          signed_digest: str,
                                          observed_epoch: int) -> bool:
        """At most one review attempt per source identity across restarts.

        Called ONLY after M04 receipt and coverage validation. A crash or
        timeout is ambiguous, so a claim is never released for retry.
        No AI request, credentials, final decision or order is stored here.
        """
        if (type(pair) is not str
                or re.fullmatch(r"[a-z0-9]{2,20}_idr", pair) is None
                or pair in ("floki_idr", "pepe_idr")
                or any(type(v) is not str or re.fullmatch(r"[0-9a-f]{64}", v) is None
                       for v in (evidence_key, signed_digest))
                or type(observed_epoch) is not int or observed_epoch <= 0):
            raise ValueError("M04_REVIEW_CLAIM_INVALID")
        if self._m11_readonly_fixture:
            raise ValueError("M04_REVIEW_CLAIM_READONLY")
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("""CREATE TABLE IF NOT EXISTS m04_candidate_review_claim (
                pair TEXT NOT NULL, evidence_key TEXT NOT NULL,
                signed_digest TEXT NOT NULL, observed_epoch INTEGER NOT NULL,
                PRIMARY KEY(pair,evidence_key)
            )""")
            cur = con.execute(
                "INSERT OR IGNORE INTO m04_candidate_review_claim "
                "(pair,evidence_key,signed_digest,observed_epoch) VALUES (?,?,?,?)",
                (pair,evidence_key,signed_digest,observed_epoch))
            return cur.rowcount == 1

    @staticmethod
    def _m04_history_tables(con):
        # Additive tables in the original journal, never a new database.
        con.execute("""CREATE TABLE IF NOT EXISTS m04_history_reviews (
            pair TEXT NOT NULL, digest TEXT NOT NULL, source_epoch INTEGER NOT NULL,
            decision_epoch INTEGER NOT NULL, review_json TEXT NOT NULL,
            PRIMARY KEY(pair,digest))""")
        con.execute("""CREATE TABLE IF NOT EXISTS m04_history_outcomes (
            pair TEXT NOT NULL, digest TEXT NOT NULL, horizon_minutes INTEGER NOT NULL,
            outcome_json TEXT NOT NULL, PRIMARY KEY(pair,digest,horizon_minutes))""")

    def record_m04_history_reviews(self, *, ai_shadow: dict,
                                   source_epoch: int, decision_epoch: int) -> int:
        """Record only durable-claimed role attempts, not public WATCH rejects.

        Store bounded categorical results, not model prompt/reason prose or
        account data. A replay or missing G1 is never labelled an AI decision.
        """
        if (type(source_epoch) is not int or type(decision_epoch) is not int
                or not 0 < source_epoch <= decision_epoch
                or not isinstance(ai_shadow, dict)
                or ai_shadow.get("execution_allowed") is not False
                or ai_shadow.get("final_decision") is not None):
            return 0
        reports = ai_shadow.get("candidate_reports")
        if reports is None:
            reports = [{"pair": ai_shadow.get("candidate_pair"),
                        "evidence_digest_sha256": ai_shadow.get("source_digest_sha256"),
                        "reason": ai_shadow.get("reason"),
                        "completed_roles": ai_shadow.get("completed_roles"),
                        "role_results": ai_shadow.get("role_results")}]
        if not isinstance(reports, list) or len(reports) > 2:
            return 0
        decision = ai_shadow.get("candidate_decision_loop")
        decision = decision if isinstance(decision, dict) else {}
        selected = decision.get("selected_pair")
        selected_digest = decision.get("selected_evidence_digest_sha256")
        if self._m11_readonly_fixture:
            raise ValueError("M04_HISTORY_READONLY")
        count = 0
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            # No G1 claim table yet: the public path never reviewed a model.
            if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                           "AND name='m04_candidate_review_claim'").fetchone() is None:
                return 0
            self._m04_history_tables(con)
            for r in reports:
                if not isinstance(r, dict):
                    continue
                pair, digest, reason = (r.get("pair"),r.get("evidence_digest_sha256"),
                                        r.get("reason"))
                roles = r.get("completed_roles")
                if (type(pair) is not str or re.fullmatch(r"[a-z0-9]{2,20}_idr",pair) is None
                        or type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}",digest) is None
                        or type(reason) is not str or re.fullmatch(r"[A-Z0-9_]{1,100}",reason) is None
                        or not isinstance(roles,list)
                        or roles != ["MARKET","RISK","STRATEGY"][:len(roles)]
                        or reason in ("M04_REVIEW_EVIDENCE_ALREADY_CLAIMED",
                                      "M04_REVIEW_CLAIM_UNAVAILABLE")
                        or not (roles or reason == "M04_ROLE_INVALID_OR_UNAVAILABLE")):
                    continue
                if con.execute("SELECT 1 FROM m04_candidate_review_claim "
                               "WHERE pair=? AND signed_digest=? AND observed_epoch=?",
                               (pair,digest,source_epoch)).fetchone() is None:
                    continue
                role_fields = {}
                for row in r.get("role_results") or []:
                    if not isinstance(row,dict):
                        continue
                    for role,field,allowed in (
                        ("MARKET","bias",("BULLISH","BEARISH","MIXED")),
                        ("RISK","decision",("ALLOW","NO_TRADE","EXIT_REVIEW")),
                        ("STRATEGY","action",("BUY","WAIT","EXIT_REVIEW"))):
                        if row.get("role")==role and row.get(field) in allowed:
                            role_fields[role]=row[field]
                disposition = next((x.get("disposition") for x in
                    decision.get("candidates",[]) if isinstance(x,dict)
                    and x.get("pair")==pair and x.get("evidence_digest_sha256")==digest),
                    None)
                if type(disposition) is not str or re.fullmatch(r"[A-Z0-9_]{1,100}",disposition) is None:
                    disposition = "ADVISORY_UNAVAILABLE"
                item = {"reason":reason,"completed_roles":roles,"role_findings":role_fields,
                        "disposition":disposition,
                        "selected_synthetic_review":selected==pair and selected_digest==digest,
                        "actual_provider_verified":False,"execution_allowed":False}
                cur=con.execute("INSERT OR IGNORE INTO m04_history_reviews VALUES(?,?,?,?,?)",
                    (pair,digest,source_epoch,decision_epoch,json.dumps(item,sort_keys=True)))
                count+=cur.rowcount
        return count

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


    def record_m03_compact_tick(self, trace_id: str,
                                summary: dict) -> None:
        """Durable bounded DEEP20 fact only; never invent an owner Final.

        Replay is idempotent only for identical source-derived summaries.
        A conflicting record or trace with an existing Final fails closed.
        """
        base_fields = {
            "schema_version", "lane", "coverage_status",
            "attempted_count", "analyzed_count",
            "final_decision", "stage_disposition",
            "execution_allowed", "g1_verified", "paid_network_allowed",
        }
        v2_fields = base_fields | {
            "candidate_valid_count", "candidate_rejected_count",
            "candidate_rejection_reason_counts", "eligible_cohort_status",
            "scan_observed_epoch",
        }
        if (type(trace_id) is not str or not 1 <= len(trace_id) <= 128
                or type(summary) is not dict
                or type(summary.get("schema_version")) is not int
                or summary["schema_version"] not in (1, 2)
                or set(summary) != (base_fields if summary["schema_version"] == 1
                                   else v2_fields)
                or summary["lane"] != "DEEP20_EVERY_FIVE_MINUTES"
                or summary["final_decision"] is not None
                or summary["stage_disposition"] !=
                    "ADVISORY_ONLY_NO_OWNER_DECISION"
                or summary["execution_allowed"] is not False
                or summary["g1_verified"] is not False
                or summary["paid_network_allowed"] is not False):
            raise ValueError("M03_COMPACT_PRE_FINAL_ONLY")
        if summary["schema_version"] == 2:
            valid = summary["candidate_valid_count"]
            rejected = summary["candidate_rejected_count"]
            for count in (valid, rejected):
                if count is not None and (
                    type(count) is not int or not 0 <= count <= 40
                ):
                    raise ValueError("M03_COMPACT_V2_COUNT_INVALID")
            if (type(valid) is int and type(rejected) is int
                    and type(summary["analyzed_count"]) is int
                    and valid + rejected != summary["analyzed_count"]):
                raise ValueError("M03_COMPACT_V2_COUNT_MISMATCH")
            reasons = summary["candidate_rejection_reason_counts"]
            if (type(reasons) is not dict or len(reasons) > 6
                    or any(type(k) is not str or not 3 <= len(k) <= 48
                           or not all(c.isascii() and (c.isupper()
                           or c.isdigit() or c == "_") for c in k)
                           or type(v) is not int or not 1 <= v <= 40
                           for k, v in reasons.items())
                    or (type(rejected) is int
                        and sum(reasons.values()) != rejected)
                    or (rejected is None and reasons)):
                raise ValueError("M03_COMPACT_V2_REJECTIONS_INVALID")
            cohort = summary["eligible_cohort_status"]
            if (type(cohort) is not str or not 1 <= len(cohort) <= 64
                    or not all(c.isascii() and (c.isupper() or c.isdigit()
                                                or c == "_") for c in cohort)):
                raise ValueError("M03_COMPACT_V2_COHORT_INVALID")
            observed = summary["scan_observed_epoch"]
            if observed is not None and (
                type(observed) is not int or not 0 < observed < 4102444800
            ):
                raise ValueError("M03_COMPACT_V2_EPOCH_INVALID")
        coverage = summary["coverage_status"]
        if (type(coverage) is not str or not 1 <= len(coverage) <= 64
                or not all(c.isascii() and
                    (c.isupper() or c.isdigit() or c == "_")
                    for c in coverage)):
            raise ValueError("M03_COMPACT_COVERAGE_INVALID")
        for key in ("attempted_count", "analyzed_count"):
            value = summary[key]
            if value is not None and (type(value) is not int
                                      or not 0 <= value <= 40):
                raise ValueError("M03_COMPACT_COUNT_INVALID")
        if (type(summary["attempted_count"]) is int
                and type(summary["analyzed_count"]) is int
                and summary["analyzed_count"] > summary["attempted_count"]):
            raise ValueError("M03_COMPACT_COUNT_INVALID")
        payload = json.dumps(
            summary, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
        if len(payload.encode("utf-8")) > 1024:
            raise ValueError("M03_COMPACT_TOO_LARGE")
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if con.execute(
                "SELECT 1 FROM decisions WHERE trace_id=?", (trace_id,)
            ).fetchone() is not None:
                raise DecisionConflictError("M03_COMPACT_OWNER_FINAL_EXISTS")
            con.execute(
                "INSERT OR IGNORE INTO m03_compact_tick "
                "(trace_id,lane,summary_json,stage_disposition,execution_allowed) "
                "VALUES (?,?,?,?,0)",
                (trace_id, summary["lane"], payload,
                 summary["stage_disposition"]),
            )
            row = con.execute(
                "SELECT summary_json FROM m03_compact_tick WHERE trace_id=?",
                (trace_id,),
            ).fetchone()
            if row != (payload,):
                raise DecisionConflictError("M03_COMPACT_CONFLICTING_REPLAY")

    def m03_compact_count(self) -> int:
        with self._connect() as con:
            return int(con.execute(
                "SELECT COUNT(*) FROM m03_compact_tick"
            ).fetchone()[0])

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



    # M11-04 ONLY: durable owner-entered risk scenario + permanent OFFLINE
    # emergency halt contract in the ORIGINAL DecisionJournal. Not called
    # by any execution, private API, Telegram or normal scanner route.
    @staticmethod
    def _m11_offline_id(value: str) -> str:
        if type(value) is not str or re.fullmatch(r"[a-f0-9]{24}", value) is None:
            raise ValueError("M11_RISK_OFFLINE_ID_INVALID")
        return value

    @staticmethod
    def _m11_offline_epoch(value: int) -> int:
        if type(value) is not int or not 1 <= value <= 9999999999:
            raise ValueError("M11_RISK_OFFLINE_EPOCH_INVALID")
        return value

    @staticmethod
    def _m11_offline_owner(value: int) -> int:
        # This is only a caller-provided identifier, not authentication.
        if type(value) is not int or not 1 <= value <= 9999999999:
            raise ValueError("M11_RISK_OFFLINE_OWNER_INVALID")
        return value

    @staticmethod
    def _m11_offline_amount(value: Decimal, *, zero_ok: bool = False,
                            signed: bool = False) -> str:
        if (type(value) is not Decimal or not value.is_finite()
                or value != 0 and not -18 <= value.adjusted() <= 20
                or (not zero_ok and not signed and value <= 0)
                or (zero_ok and not signed and value < 0)):
            raise ValueError("M11_RISK_OFFLINE_AMOUNT_INVALID")
        return format(value, "f")

    def record_m11_offline_risk_limits_once(
        self, *, owner_user_id: int, policy_id: str,
        capital_ceiling_idr: Decimal, max_open_orders: int,
        daily_loss_ceiling_idr: Decimal, recorded_epoch: int,
    ) -> dict:
        """Persist immutable, explicitly *unverified* offline owner inputs.

        An identical replay is inert; ANY change to owner/budget/policy
        requires a FUTURE separately approved mechanism, not overwrite.
        Creates no usable BUY budget, free balance or live risk permission.
        """
        uid = self._m11_offline_owner(owner_user_id)
        pid = self._m11_offline_id(policy_id)
        epoch = self._m11_offline_epoch(recorded_epoch)
        capital = self._m11_offline_amount(capital_ceiling_idr)
        dayloss = self._m11_offline_amount(daily_loss_ceiling_idr)
        if type(max_open_orders) is not int or not 1 <= max_open_orders <= 10000:
            raise ValueError("M11_RISK_OFFLINE_ORDER_LIMIT_INVALID")
        payload = (uid, pid, capital, max_open_orders, dayloss, epoch)
        digest = hashlib.sha256(json.dumps(
            payload, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")).hexdigest()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            halt = con.execute(
                "SELECT state,execution_allowed FROM m11_offline_risk_halt "
                "WHERE singleton=1"
            ).fetchone()
            if halt != ("ENGAGED", 0):
                raise DecisionConflictError("M11_RISK_HALT_UNTRUSTED")
            event_owners = con.execute(
                "SELECT DISTINCT owner_user_id FROM m11_offline_risk_halt_events"
            ).fetchall()
            if any(row[0] != uid for row in event_owners):
                raise DecisionConflictError("M11_RISK_POLICY_HALT_OWNER_CONFLICT")
            prior = con.execute(
                "SELECT owner_user_id,policy_id,policy_sha256,"
                "capital_ceiling_idr,max_open_orders,"
                "daily_loss_ceiling_idr,recorded_epoch,source_kind,"
                "execution_allowed FROM m11_offline_risk_limits "
                "WHERE singleton=1"
            ).fetchone()
            if prior is not None:
                if prior != (uid, pid, digest, capital, max_open_orders,
                             dayloss, epoch, "OWNER_ENTERED_OFFLINE_UNVERIFIED", 0):
                    raise DecisionConflictError("M11_RISK_POLICY_REPLAY_CONFLICT")
                replay = True
            else:
                con.execute(
                    "INSERT INTO m11_offline_risk_limits "
                    "(singleton,owner_user_id,policy_id,policy_sha256,"
                    "capital_ceiling_idr,max_open_orders,"
                    "daily_loss_ceiling_idr,recorded_epoch,source_kind,"
                    "execution_allowed) VALUES (1,?,?,?,?,?,?,?,?,0)",
                    (uid, pid, digest, capital, max_open_orders,
                     dayloss, epoch, "OWNER_ENTERED_OFFLINE_UNVERIFIED"),
                )
                replay = False
        return {
            "status": "OFFLINE_RISK_POLICY_STORED_NOT_AUTHORITY",
            "replay": replay, "persistent_halt_engaged": True,
            "owner_authenticated": False, "policy_owner_approved": False,
            "execution_allowed": False, "m11_live_release_authorized": False,
        }

    def engage_m11_offline_emergency_halt_once(
        self, *, owner_user_id: int, event_id: str,
        reason_code: str, observed_epoch: int,
    ) -> dict:
        """Idempotent ENGAGE only; cannot clear halt or authorize SELL/BUY."""
        uid = self._m11_offline_owner(owner_user_id)
        eid = self._m11_offline_id(event_id)
        epoch = self._m11_offline_epoch(observed_epoch)
        if type(reason_code) is not str or reason_code not in (
            "OWNER_EMERGENCY", "LIMIT_REVIEW", "UNKNOWN_ORDER",
            "RECOVERY_UNVERIFIED",
        ):
            raise ValueError("M11_RISK_HALT_REASON_INVALID")
        fingerprint = hashlib.sha256(json.dumps(
            (uid, eid, reason_code, epoch), separators=(",", ":")
        ).encode("ascii")).hexdigest()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            halt = con.execute(
                "SELECT state,event_count,execution_allowed "
                "FROM m11_offline_risk_halt WHERE singleton=1"
            ).fetchone()
            if (halt is None or halt[0] != "ENGAGED"
                    or type(halt[1]) is not int or halt[1] < 0
                    or halt[2] != 0):
                raise DecisionConflictError("M11_RISK_HALT_UNTRUSTED")
            prior = con.execute(
                "SELECT owner_user_id,reason_code,observed_epoch,"
                "event_digest,source_kind,execution_allowed "
                "FROM m11_offline_risk_halt_events WHERE event_id=?",
                (eid,),
            ).fetchone()
            if prior is not None:
                if prior != (uid, reason_code, epoch, fingerprint,
                             "OFFLINE_UNVERIFIED", 0):
                    raise DecisionConflictError("M11_HALT_EVENT_REPLAY_CONFLICT")
                replay = True
            else:
                # An event from a different owner cannot alter the same
                # singleton's owner-bound offline policy once recorded.
                policy = con.execute(
                    "SELECT owner_user_id FROM m11_offline_risk_limits "
                    "WHERE singleton=1"
                ).fetchone()
                if policy is not None and policy[0] != uid:
                    raise DecisionConflictError("M11_HALT_OWNER_CONFLICT")
                last = con.execute(
                    "SELECT MAX(observed_epoch) FROM m11_offline_risk_halt_events"
                ).fetchone()[0]
                if last is not None and epoch < last:
                    raise DecisionConflictError("M11_HALT_OLDER_EVENT_DENIED")
                con.execute(
                    "INSERT INTO m11_offline_risk_halt_events "
                    "(event_id,owner_user_id,reason_code,observed_epoch,"
                    "event_digest,source_kind,execution_allowed) "
                    "VALUES (?,?,?,?,?,'OFFLINE_UNVERIFIED',0)",
                    (eid, uid, reason_code, epoch, fingerprint),
                )
                changed = con.execute(
                    "UPDATE m11_offline_risk_halt "
                    "SET event_count=event_count+1 WHERE singleton=1 "
                    "AND state='ENGAGED' AND execution_allowed=0"
                ).rowcount
                if changed != 1:
                    raise DecisionConflictError("M11_HALT_UPDATE_FAILED")
                replay = False
        return {
            "status": "OFFLINE_EMERGENCY_HALT_DURABLE",
            "replay": replay, "persistent_halt_engaged": True,
            "halt_cleared": False, "execution_allowed": False,
            "live_execution_unlocked": False,
            "m11_live_release_authorized": False,
        }

    def review_m11_offline_risk_halt(
        self, *, owner_user_id: int, observed_epoch: int,
        proposed_buy_cost_idr: Decimal | None = None,
        existing_exposure_idr: Decimal | None = None,
        open_order_count: int | None = None,
        daily_realized_net_idr: Decimal | None = None,
        realized_pnl_day_wib: str | None = None,
    ) -> dict:
        """One snapshot of original V2 OFFLINE contract state.

        All account/exposure/PnL inputs are UNTRUSTED user-supplied
        scenarios. Even if all caps pass, the permanent halt remains
        ENGAGED and release is always rejected. No state mutation.
        """
        uid = self._m11_offline_owner(owner_user_id)
        epoch = self._m11_offline_epoch(observed_epoch)
        values = (proposed_buy_cost_idr, existing_exposure_idr,
                  open_order_count, daily_realized_net_idr,
                  realized_pnl_day_wib)
        if all(x is None for x in values):
            supplied = False
        elif any(x is None for x in values):
            raise ValueError("M11_RISK_SNAPSHOT_INCOMPLETE")
        else:
            supplied = True
            self._m11_offline_amount(proposed_buy_cost_idr)
            self._m11_offline_amount(existing_exposure_idr, zero_ok=True)
            self._m11_offline_amount(daily_realized_net_idr, signed=True)
            if type(open_order_count) is not int or not 0 <= open_order_count <= 10000:
                raise ValueError("M11_RISK_OPEN_ORDER_COUNT_INVALID")
            if (type(realized_pnl_day_wib) is not str or
                    re.fullmatch(r"\d{4}-\d{2}-\d{2}", realized_pnl_day_wib) is None):
                raise ValueError("M11_RISK_DAY_WIB_INVALID")
        with self._connect() as con:
            # A read-only fixture takes an atomic SQLite snapshot, not a
            # RESERVED write lock. Existing mutable review path is unchanged.
            con.execute("BEGIN" if getattr(self, "_m11_readonly_fixture", False)
                        else "BEGIN IMMEDIATE")
            policy = con.execute(
                "SELECT owner_user_id,policy_id,policy_sha256,"
                "capital_ceiling_idr,max_open_orders,daily_loss_ceiling_idr,"
                "recorded_epoch,source_kind,execution_allowed "
                "FROM m11_offline_risk_limits WHERE singleton=1"
            ).fetchone()
            halt = con.execute(
                "SELECT state,event_count,execution_allowed "
                "FROM m11_offline_risk_halt WHERE singleton=1"
            ).fetchone()
            count = con.execute(
                "SELECT COUNT(*) FROM m11_offline_risk_halt_events"
            ).fetchone()[0]
        halt_valid = (
            halt is not None and halt[0] == "ENGAGED" and
            type(halt[1]) is int and halt[1] == count and halt[2] == 0
        )
        policy_valid = (policy is not None and policy[0] == uid and
                        policy[7] == "OWNER_ENTERED_OFFLINE_UNVERIFIED" and
                        policy[8] == 0)
        if policy_valid:
            expected_digest = hashlib.sha256(json.dumps(
                (policy[0], policy[1], policy[3], policy[4],
                 policy[5], policy[6]),
                separators=(",", ":"), ensure_ascii=True,
            ).encode("ascii")).hexdigest()
            policy_valid = (expected_digest == policy[2])
        reason_codes = []
        if not halt_valid:
            reason_codes.append("HALT_STATE_OR_EVENT_COUNT_UNVERIFIED")
        else:
            reason_codes.append("PERSISTENT_EMERGENCY_HALT_ENGAGED")
        if not policy_valid:
            reason_codes.append("OWNER_OFFLINE_POLICY_UNVERIFIED_OR_MISSING")
        if not supplied:
            reason_codes.append("RISK_OBSERVATION_MISSING")
        day_matches = None
        cap_ok = count_ok = loss_ok = None
        if policy_valid and supplied:
            expected_day = datetime.fromtimestamp(
                epoch, ZoneInfo("Asia/Jakarta")
            ).date().isoformat()
            day_matches = realized_pnl_day_wib == expected_day
            if not day_matches:
                reason_codes.append("WIB_DAY_UNVERIFIED")
            try:
                ceiling = Decimal(policy[3])
                max_orders = policy[4]
                loss_ceiling = Decimal(policy[5])
                if (not ceiling.is_finite() or ceiling <= 0
                        or type(max_orders) is not int or max_orders <= 0
                        or not loss_ceiling.is_finite() or loss_ceiling <= 0):
                    raise ValueError("M11_RISK_PERSISTED_POLICY_INVALID")
                cap_ok = (existing_exposure_idr + proposed_buy_cost_idr <= ceiling)
                count_ok = open_order_count < max_orders
                loss_ok = (day_matches and
                           daily_realized_net_idr > -loss_ceiling)
            except (TypeError, ValueError, ArithmeticError):
                cap_ok = count_ok = loss_ok = None
                reason_codes.append("RISK_POLICY_FIELDS_UNTRUSTED")
            if cap_ok is False:
                reason_codes.append("CAPITAL_SCENARIO_CEILING_BREACHED")
            if count_ok is False:
                reason_codes.append("ORDER_SCENARIO_CEILING_BREACHED")
            if loss_ok is False:
                reason_codes.append("DAILY_LOSS_SCENARIO_CEILING_OR_DAY_BREACHED")
        return {
            "stage": "M11_ORIGINAL_V2_JOURNAL_RISK_HALT_OFFLINE",
            "status": "REJECT_OFFLINE_RISK_HALT_AND_UNVERIFIED_SOURCES",
            "reason_codes": sorted(set(reason_codes)),
            "offline_policy_present_for_caller": policy_valid,
            "persistent_halt_engaged_observed": halt_valid,
            "halt_event_count_observed": count if halt_valid else None,
            "scenario_proposed_total_capital_within": cap_ok,
            "scenario_order_slots_within": count_ok,
            "scenario_daily_loss_below_limit": loss_ok,
            "scenario_day_wib_matches": day_matches,
            "scenario_is_real_exchange_evidence": False,
            "owner_identity_authenticated": False,
            "owner_policy_signed_verified": False,
            "realized_pnl_settlement_verified": False,
            "kill_switch_installed_runtime_verified": False,
            "m11_04_full_acceptance": False,
            "execution_allowed": False,
            "m11_live_release_authorized": False,
        }
