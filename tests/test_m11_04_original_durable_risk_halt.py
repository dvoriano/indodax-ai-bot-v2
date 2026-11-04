"""M11-04 original DecisionJournal durable OFFLINE risk halt contract.

Only an isolated temporary original V2 SQLite journal, not a second
database, trading engine, live owner permission, Telegram, API or order.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from indodax_ai_bot.journal import DecisionJournal, DecisionConflictError


OWNER = 792001
EPOCH = 1793781000
PID = "a" * 24
EVENT = "b" * 24


def limits(**overrides):
    values = dict(
        owner_user_id=OWNER, policy_id=PID,
        capital_ceiling_idr=Decimal("300000"),
        max_open_orders=3,
        daily_loss_ceiling_idr=Decimal("40000"),
        recorded_epoch=EPOCH,
    )
    values.update(overrides)
    return values


def review(**overrides):
    values = dict(
        owner_user_id=OWNER, observed_epoch=EPOCH,
        proposed_buy_cost_idr=Decimal("120000"),
        existing_exposure_idr=Decimal("130000"),
        open_order_count=1,
        daily_realized_net_idr=Decimal("-30000"),
        realized_pnl_day_wib="2026-11-04",
    )
    values.update(overrides)
    return values


class M11OriginalV2DurableOfflineHaltTests(unittest.TestCase):
    def setUp(self):
        td = TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.db = Path(td.name) / "original_v2_journal.sqlite3"
        self.journal = DecisionJournal(self.db)

    def forbidden_go(self, record):
        self.assertFalse(record["execution_allowed"])
        self.assertFalse(record["m11_live_release_authorized"])

    def test_initial_unknown_policy_and_halt_engaged(self):
        result = self.journal.review_m11_offline_risk_halt(
            owner_user_id=OWNER, observed_epoch=EPOCH)
        self.assertIn("OWNER_OFFLINE_POLICY_UNVERIFIED_OR_MISSING",
                      result["reason_codes"])
        self.assertTrue(result["persistent_halt_engaged_observed"])
        self.assertFalse(result["offline_policy_present_for_caller"])
        self.assertIsNone(result["scenario_proposed_total_capital_within"])
        self.forbidden_go(result)

    def test_immutable_policy_persists_after_new_journal_instance(self):
        first = self.journal.record_m11_offline_risk_limits_once(**limits())
        self.assertFalse(first["replay"])
        self.assertFalse(first["owner_authenticated"])
        self.forbidden_go(first)
        restarted = DecisionJournal(self.db)
        second = restarted.record_m11_offline_risk_limits_once(**limits())
        self.assertTrue(second["replay"])
        self.assertTrue(second["persistent_halt_engaged"])
        self.forbidden_go(second)
        snapshot = restarted.review_m11_offline_risk_halt(**review())
        self.assertTrue(snapshot["offline_policy_present_for_caller"])
        self.assertEqual(snapshot["halt_event_count_observed"], 0)
        self.assertTrue(snapshot["persistent_halt_engaged_observed"])
        self.assertEqual(snapshot["status"],
                         "REJECT_OFFLINE_RISK_HALT_AND_UNVERIFIED_SOURCES")
        self.forbidden_go(snapshot)

    def test_changed_policy_same_key_cannot_elevate_limits(self):
        self.journal.record_m11_offline_risk_limits_once(**limits())
        for mutation in (
            {"capital_ceiling_idr": Decimal("999999")},
            {"max_open_orders": 8},
            {"daily_loss_ceiling_idr": Decimal("90000")},
            {"owner_user_id": OWNER + 1},
            {"policy_id": "c" * 24},
            {"recorded_epoch": EPOCH + 1},
        ):
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(DecisionConflictError,
                                           "M11_RISK_POLICY_REPLAY_CONFLICT"):
                    self.journal.record_m11_offline_risk_limits_once(
                        **limits(**mutation))
        observed = self.journal.review_m11_offline_risk_halt(
            **review(existing_exposure_idr=Decimal("290000")))
        self.assertFalse(observed["scenario_proposed_total_capital_within"])
        self.forbidden_go(observed)

    def test_awaiting_policy_cannot_be_assumed_from_one_halt_event(self):
        r = self.journal.engage_m11_offline_emergency_halt_once(
            owner_user_id=OWNER, event_id=EVENT,
            reason_code="UNKNOWN_ORDER", observed_epoch=EPOCH)
        self.assertFalse(r["replay"])
        self.assertFalse(r["halt_cleared"])
        self.forbidden_go(r) if "m11_live_release_authorized" in r else self.assertFalse(r["live_execution_unlocked"])
        snapshot = self.journal.review_m11_offline_risk_halt(
            owner_user_id=OWNER, observed_epoch=EPOCH)
        self.assertEqual(snapshot["halt_event_count_observed"], 1)
        self.assertFalse(snapshot["offline_policy_present_for_caller"])
        self.forbidden_go(snapshot)

    def test_persistent_halt_across_recovery_no_clear_api(self):
        self.journal.record_m11_offline_risk_limits_once(**limits())
        entered = self.journal.engage_m11_offline_emergency_halt_once(
            owner_user_id=OWNER, event_id=EVENT,
            reason_code="OWNER_EMERGENCY", observed_epoch=EPOCH)
        self.assertEqual(entered["status"], "OFFLINE_EMERGENCY_HALT_DURABLE")
        self.assertFalse(entered["live_execution_unlocked"])
        restarted = DecisionJournal(self.db)
        same = restarted.engage_m11_offline_emergency_halt_once(
            owner_user_id=OWNER, event_id=EVENT,
            reason_code="OWNER_EMERGENCY", observed_epoch=EPOCH)
        self.assertTrue(same["replay"])
        state = restarted.review_m11_offline_risk_halt(**review())
        self.assertEqual(state["halt_event_count_observed"], 1)
        self.assertTrue(state["persistent_halt_engaged_observed"])
        self.assertIn("PERSISTENT_EMERGENCY_HALT_ENGAGED", state["reason_codes"])
        self.assertFalse(hasattr(restarted, "release_m11_offline_emergency_halt"))
        self.forbidden_go(state)

    def test_duplicate_event_different_reason_or_owner_is_denied(self):
        self.journal.record_m11_offline_risk_limits_once(**limits())
        self.journal.engage_m11_offline_emergency_halt_once(
            owner_user_id=OWNER, event_id=EVENT,
            reason_code="OWNER_EMERGENCY", observed_epoch=EPOCH)
        for delta in (
            {"reason_code": "UNKNOWN_ORDER"},
            {"owner_user_id": OWNER + 1},
            {"observed_epoch": EPOCH + 1},
        ):
            values = dict(owner_user_id=OWNER, event_id=EVENT,
                          reason_code="OWNER_EMERGENCY", observed_epoch=EPOCH)
            values.update(delta)
            with self.subTest(delta=delta), self.assertRaisesRegex(
                    DecisionConflictError, "M11_HALT_EVENT_REPLAY_CONFLICT"):
                self.journal.engage_m11_offline_emergency_halt_once(**values)

    def test_other_owner_cannot_add_new_halt_after_policy(self):
        self.journal.record_m11_offline_risk_limits_once(**limits())
        with self.assertRaisesRegex(DecisionConflictError,
                                    "M11_HALT_OWNER_CONFLICT"):
            self.journal.engage_m11_offline_emergency_halt_once(
                owner_user_id=OWNER + 1, event_id=EVENT,
                reason_code="OWNER_EMERGENCY", observed_epoch=EPOCH)
        state = self.journal.review_m11_offline_risk_halt(**review())
        self.assertEqual(state["halt_event_count_observed"], 0)
        self.forbidden_go(state)

    def test_earlier_events_cannot_rewind_after_newer_halt(self):
        self.journal.engage_m11_offline_emergency_halt_once(
            owner_user_id=OWNER, event_id=EVENT,
            reason_code="RECOVERY_UNVERIFIED", observed_epoch=EPOCH+10)
        with self.assertRaisesRegex(DecisionConflictError,
                                    "M11_HALT_OLDER_EVENT_DENIED"):
            self.journal.engage_m11_offline_emergency_halt_once(
                owner_user_id=OWNER, event_id="c" * 24,
                reason_code="OWNER_EMERGENCY", observed_epoch=EPOCH)
        state = self.journal.review_m11_offline_risk_halt(
            owner_user_id=OWNER, observed_epoch=EPOCH+11)
        self.assertEqual(state["halt_event_count_observed"], 1)
        self.forbidden_go(state)

    def test_aggregate_capital_and_order_slots_are_only_scenarios(self):
        self.journal.record_m11_offline_risk_limits_once(**limits())
        under = self.journal.review_m11_offline_risk_halt(**review())
        self.assertTrue(under["scenario_proposed_total_capital_within"])
        self.assertTrue(under["scenario_order_slots_within"])
        self.assertTrue(under["scenario_daily_loss_below_limit"])
        self.assertFalse(under["scenario_is_real_exchange_evidence"])
        self.forbidden_go(under)
        equal = self.journal.review_m11_offline_risk_halt(
            **review(existing_exposure_idr=Decimal("180000"),
                     open_order_count=3))
        self.assertTrue(equal["scenario_proposed_total_capital_within"])
        self.assertFalse(equal["scenario_order_slots_within"])
        self.assertIn("ORDER_SCENARIO_CEILING_BREACHED",
                      equal["reason_codes"])
        above = self.journal.review_m11_offline_risk_halt(
            **review(existing_exposure_idr=Decimal("180001")))
        self.assertFalse(above["scenario_proposed_total_capital_within"])
        self.assertIn("CAPITAL_SCENARIO_CEILING_BREACHED",
                      above["reason_codes"])
        self.forbidden_go(above)

    def test_loss_cap_equal_or_beyond_never_passes(self):
        self.journal.record_m11_offline_risk_limits_once(**limits())
        for amount in ("-40000", "-40001"):
            with self.subTest(amount=amount):
                state = self.journal.review_m11_offline_risk_halt(
                    **review(daily_realized_net_idr=Decimal(amount)))
                self.assertFalse(state["scenario_daily_loss_below_limit"])
                self.assertIn("DAILY_LOSS_SCENARIO_CEILING_OR_DAY_BREACHED",
                              state["reason_codes"])
                self.forbidden_go(state)

    def test_wib_day_mismatch_and_missing_snapshot_deny(self):
        self.journal.record_m11_offline_risk_limits_once(**limits())
        missing = self.journal.review_m11_offline_risk_halt(
            owner_user_id=OWNER, observed_epoch=EPOCH)
        self.assertIn("RISK_OBSERVATION_MISSING", missing["reason_codes"])
        self.forbidden_go(missing)
        mismatch = self.journal.review_m11_offline_risk_halt(
            **review(realized_pnl_day_wib="2026-11-03"))
        self.assertFalse(mismatch["scenario_day_wib_matches"])
        self.assertFalse(mismatch["scenario_daily_loss_below_limit"])
        self.assertIn("WIB_DAY_UNVERIFIED", mismatch["reason_codes"])
        self.forbidden_go(mismatch)

    def test_incomplete_and_untrusted_inputs_fail_closed(self):
        for changes in (
            {"proposed_buy_cost_idr": None},
            {"existing_exposure_idr": Decimal("NaN")},
            {"proposed_buy_cost_idr": Decimal("-1")},
            {"open_order_count": True},
            {"daily_realized_net_idr": Decimal("Infinity")},
            {"realized_pnl_day_wib": "2026-11-04;DROP"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.journal.review_m11_offline_risk_halt(**review(**changes))
        for change in (
            {"max_open_orders": True},
            {"capital_ceiling_idr": Decimal("Infinity")},
            {"daily_loss_ceiling_idr": Decimal("0")},
            {"policy_id": "owner-secret"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.journal.record_m11_offline_risk_limits_once(
                    **limits(**change))

    def test_owner_scope_mismatch_does_not_echo_identity(self):
        self.journal.record_m11_offline_risk_limits_once(**limits())
        state = self.journal.review_m11_offline_risk_halt(
            **review(owner_user_id=OWNER+1))
        self.assertFalse(state["offline_policy_present_for_caller"])
        self.assertNotIn(str(OWNER), str(state))
        self.forbidden_go(state)

    def test_corrupted_event_count_and_missing_halt_fail_closed(self):
        self.journal.record_m11_offline_risk_limits_once(**limits())
        with sqlite3.connect(self.db) as con:
            con.execute(
                "UPDATE m11_offline_risk_halt SET event_count=100 "
                "WHERE singleton=1"
            )
        state = self.journal.review_m11_offline_risk_halt(**review())
        self.assertIn("HALT_STATE_OR_EVENT_COUNT_UNVERIFIED",
                      state["reason_codes"])
        self.assertFalse(state["persistent_halt_engaged_observed"])
        self.forbidden_go(state)
        with sqlite3.connect(self.db) as con:
            con.execute("DELETE FROM m11_offline_risk_halt")
        missing = self.journal.review_m11_offline_risk_halt(**review())
        self.assertFalse(missing["persistent_halt_engaged_observed"])
        self.forbidden_go(missing)
        with self.assertRaisesRegex(DecisionConflictError,
                                    "M11_RISK_HALT_UNTRUSTED"):
            self.journal.record_m11_offline_risk_limits_once(**limits())

    def test_concurrent_identical_policy_requests_single_durable_row(self):
        def worker(_):
            return DecisionJournal(self.db).record_m11_offline_risk_limits_once(
                **limits()
            )
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(worker, range(4)))
        self.assertEqual([r["replay"] for r in results].count(False), 1)
        self.assertEqual([r["replay"] for r in results].count(True), 3)
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM m11_offline_risk_limits").fetchone()[0], 1)
        self.forbidden_go(self.journal.review_m11_offline_risk_halt(**review()))

    def test_concurrent_halt_event_replay_is_atomic(self):
        self.journal.record_m11_offline_risk_limits_once(**limits())
        def worker(_):
            return DecisionJournal(self.db).engage_m11_offline_emergency_halt_once(
                owner_user_id=OWNER, event_id=EVENT,
                reason_code="OWNER_EMERGENCY", observed_epoch=EPOCH
            )
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(worker, range(4)))
        self.assertEqual([x["replay"] for x in responses].count(False), 1)
        self.assertEqual([x["replay"] for x in responses].count(True), 3)
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM m11_offline_risk_halt_events"
            ).fetchone()[0], 1)
        state = self.journal.review_m11_offline_risk_halt(**review())
        self.assertEqual(state["halt_event_count_observed"], 1)
        self.forbidden_go(state)

    def test_original_m07_and_m01_hardlock_unmodified(self):
        from indodax_ai_bot.config import Settings, ConfigurationError
        from indodax_ai_bot.m07_order_adapter import (
            OfficialV2OrderAdapter, M07Hardlocked)
        self.assertFalse(Settings().live_allowed)
        with self.assertRaises(ConfigurationError):
            Settings(trading_enabled=True).require_m01_safety()
        adapter = OfficialV2OrderAdapter()
        for method in (
            adapter.create_order, adapter.get_order,
            adapter.cancel_order, adapter.dispatch,
        ):
            with self.subTest(method=method), self.assertRaises(M07Hardlocked):
                method()
        self.assertFalse(self.journal.review_m11_offline_risk_halt(
            **review())["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
