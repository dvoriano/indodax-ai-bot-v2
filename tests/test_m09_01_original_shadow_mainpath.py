"""M09-01 original V2 M03/G1/M04/M05/M07/M08 no-order mainpath.

The ORIGINAL production CLI/orchestrator, scanner, M04 ThreeRoleShadow,
M07 adapter, and M08 journal are exercised, but exchange/public HTTPS
boundaries are mocked. No real private GET, paid provider or order.
"""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.contracts import FinalDecision
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.m07_order_adapter import M07OrderDraft
from indodax_ai_bot.orchestrator import CycleResult, DailyScalpingOrchestrator
from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.public_market import PublicDataError

NOW=1793694000


def public_bulk(*, preview=True):
    """Coherent PUBLIC cohort, no signed G1 / private proof / paid AI."""
    return {
        "coverage_status":"ALL_SELECTED_ANALYZED_UNATTESTED",
        "g1_source_admission":{"status":"PUBLIC_UNATTESTED",
                               "g1_receipt_issuance_allowed":False},
        "eligible_cohort":{
            "status":"ALIGNED_PUBLIC_UNATTESTED",
            "closed_1m_epoch":NOW-45,
            "ranked_same_window_diagnostics":[{"pair":"btc_idr","score":78}],
            "provisional_watchlist_preview":["btc_idr"] if preview else [],
            "provisional_quality_reviews":[
                {"pair":"btc_idr","review_flags":[],
                 "clear_of_provisional_cautions":True}
            ],
        },
        "screening_observed_epoch":NOW-25,
        "provisional_market_evidence":[],
    }


class OriginalM09Shadow(unittest.TestCase):
    def setUp(self):
        td=TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.db=Path(td.name)/"decisions.sqlite3"
        self.app=DailyScalpingOrchestrator(
            Settings(),DecisionJournal(self.db))

    def run_integrated(self, *, bot=None, preview=True):
        app=bot or self.app
        with (patch.object(app,"_scan_with_early_momentum",
                           return_value=object()) as scan,
              patch("indodax_ai_bot.orchestrator.apply_deep_volume_floor",
                    side_effect=lambda preliminary, **kw: preliminary) as floor,
              patch("indodax_ai_bot.orchestrator.bulk_public_deep_scan",
                    return_value=public_bulk(preview=preview)) as deep,
              patch("indodax_ai_bot.orchestrator.time.time",
                    return_value=NOW)):
            result=app.run_m09_original_shadow_mainpath_once(
                "m09shadow_original_001",deep_limit=20)
        self.assertEqual(scan.call_count,1)
        self.assertEqual(floor.call_count,1)
        self.assertEqual(deep.call_count,1)
        return result

    def test_original_scanner_m04_roles_m07_adapter_m08_journal_join(self):
        r=self.run_integrated()
        self.assertEqual(r["status"],"M09_SHADOW_CHAIN_NO_ORDER_NO_OWNER_FINAL")
        self.assertTrue(r["m03_original_bulk_invoked"])
        self.assertEqual(r["m03_cohort_status"],"ALIGNED_PUBLIC_UNATTESTED")
        self.assertEqual(r["m03_provisional_watchlist_count"],1)
        self.assertTrue(r["m04_original_g1_admission_observed"])
        self.assertFalse(r["independent_g1_issuer_verified"])
        self.assertTrue(r["m04_original_three_role_shadow_invoked"])
        self.assertEqual(r["m04_expected_roles"],
                         ["MARKET","RISK","STRATEGY"])
        self.assertEqual(r["m04_completed_role_count"],0)
        self.assertFalse(r["m04_paid_ai_provider_called"])
        self.assertEqual(r["m05_original_advisory_status"],
                         "M05_NO_BOUND_OWNER_ADVISORY")
        self.assertIsNone(r["owner_final_decision"])
        self.assertTrue(r["m07_hardlock_active"])
        self.assertEqual(r["m07_original_adapter_status"],
                         "HARDLOCKED_NO_PRIVATE_HTTP_OR_ORDER")
        self.assertEqual(r["m08_original_position_source_status"],
                         "M08_NO_AUTHENTICATED_BOT_OWNED_SETTLEMENT_ISSUER")
        self.assertFalse(r["m08_wallet_settled_idr_verified"])
        self.assertFalse(r["paper_pnl_recorded_as_real_idr"])
        self.assertFalse(r["execution_allowed"])
        self.assertFalse(r["order_sent"])
        self.assertEqual(self.app.journal.stage_observation_count(),1)

    def test_missing_or_stale_g1_never_becomes_owner_final(self):
        result=self.run_integrated(preview=False)
        self.assertEqual(result["m03_provisional_watchlist_count"],0)
        self.assertEqual(result["m04_completed_role_count"],0)
        self.assertIsNone(result["m05_order_specific_final_decision"])
        self.assertFalse(result["live_trading_authorized"])

    def test_original_public_source_error_fail_closed_no_provider(self):
        with patch.object(self.app,"_scan_with_early_momentum",
                          side_effect=PublicDataError("TEST_SOURCE_STALE")):
            report=self.app.run_m09_original_shadow_mainpath_once(
                "m09source_error")
        self.assertEqual(report["m03_provisional_watchlist_count"],0)
        self.assertIn("PUBLIC_BULK_BLOCKED",report["m03_scan_reason"])
        self.assertFalse(report["private_account_get_invoked"])
        self.assertFalse(report["order_sent"])

    def test_existing_owner_advisory_is_not_final_decision(self):
        owner_db=self.db.parent/"owner.sqlite3"
        owner=OwnerDecisionLedger(owner_db,owner_user_id=777777)
        app=DailyScalpingOrchestrator(
            Settings(), DecisionJournal(self.db),owner_control=owner)
        result=self.run_integrated(bot=app,preview=False)
        self.assertIn(result["m05_original_advisory_status"],
                      ("NO_VALID_PREVIEW","M05_OWNER_ADVISORY_NOT_QUEUED"))
        self.assertIsNone(result["owner_final_decision"])
        self.assertFalse(result["execution_allowed"])

    def test_unknown_hold_survives_m09_and_no_new_order_intent(self):
        draft=M07OrderDraft(
            symbol="ETHIDR",side="BUY",order_type="LIMIT",
            client_order_id="m09unknown001",price=Decimal("2000"),
            quantity=Decimal("2"),
        )
        self.app.reserve_m07_offline_intent_once(
            intent_id="m09unknown001",draft=draft,observed_epoch=NOW)
        self.app.simulate_m07_unknown_timeout_once(
            intent_id="m09unknown001",observed_epoch=NOW+1)
        r=self.run_integrated()
        self.assertEqual(r["m07_unknown_holds"],1)
        self.assertFalse(r["m07_order_intents_created_by_shadow"])
        self.assertEqual(
            self.app.journal.m07_offline_intent_summary()[
                "unresolved_simulated_intents"],1)
        self.assertFalse(r["execution_allowed"])

    def test_m08_untrusted_account_only_fills_cannot_create_real_pnl(self):
        with sqlite3.connect(self.db) as con:
            con.execute(
                "INSERT INTO m08_position_settled_idr_events "
                "(event_id,account_fingerprint,symbol,"
                "source_buy_client_order_id,sell_client_order_id,"
                "sell_full_order_id,sell_fill_digest,"
                "bot_owned_execution_digest,idr_wallet_settlement_digest,"
                "net_settled_idr,settled_at_ms,source,status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("fixture_m09","a"*64,"XRPIDR","testbuy","testsell",
                 "testorder","c"*64,"d"*64,"e"*64,"999999",
                 NOW*1000,"M08_RECONCILED_BOT_OWNED_SELL_IDR",
                 "POSITION_SETTLED_IDR"),
            )
        r=self.run_integrated()
        self.assertEqual(r["m08_original_position_source_status"],
                         "M08_NO_AUTHENTICATED_BOT_OWNED_SETTLEMENT_ISSUER")
        self.assertFalse(r["m08_wallet_settled_idr_verified"])
        self.assertFalse(r["m08_realized_net_pnl_wib_verified"])
        self.assertFalse(r["verified_spendable_capital_idr"])

    def test_forbid_injected_paid_ai_caller_before_public_scan(self):
        app=DailyScalpingOrchestrator(
            Settings(), DecisionJournal(self.db),
            ai_role_caller=lambda *a:{"status":"PASS"})
        with patch.object(app,"run_bulk_deep_cycle") as bulk:
            with self.assertRaisesRegex(ValueError,"M09_SHADOW_PAID_OR_INJECTED"):
                app.run_m09_original_shadow_mainpath_once("m09blocked")
        bulk.assert_not_called()

    def test_forged_owner_final_or_execution_permission_fails_closed(self):
        for bad in (
            CycleResult("m09fake",FinalDecision.PASS,"INJECTED",False,
                        stage_disposition="ADVISORY_ONLY_NO_OWNER_DECISION"),
            CycleResult("m09fake",None,"INJECTED",True,
                        stage_disposition="ADVISORY_ONLY_NO_OWNER_DECISION"),
        ):
            with self.subTest(bad=bad),patch.object(
                    self.app,"run_bulk_deep_cycle",return_value=bad):
                with self.assertRaisesRegex(ValueError,
                                            "M09_ORIGINAL_BULK_AUTHORITY_CONFLICT"):
                    self.app.run_m09_original_shadow_mainpath_once("m09fake")

    def test_original_m07_hardlock_must_remain_disabled(self):
        fake=CycleResult("m09",None,"PUBLIC_UNTRUSTED",False,
                         ai_shadow={"reason":"MISSING_SOURCE_COHORT"},
                         stage_disposition="ADVISORY_ONLY_NO_OWNER_DECISION")
        with (patch.object(self.app,"run_bulk_deep_cycle",return_value=fake),
              patch.object(self.app,"inspect_m07_official_order_adapter_once",
                          return_value={"status":"INJECTED",
                                        "network_allowed":True,
                                        "execution_allowed":True})):
            with self.assertRaisesRegex(ValueError,"M09_M07_HARDLOCK_INACTIVE"):
                self.app.run_m09_original_shadow_mainpath_once("m09lock")

    def test_original_cli_invokes_the_same_orchestrator_no_order(self):
        with patch.object(
                DailyScalpingOrchestrator,"run_bulk_deep_cycle",
                return_value=CycleResult(
                    "cli",None,"G1_PUBLIC_BULK_SOURCE_UNVERIFIED",False,
                    bulk=public_bulk(),
                    ai_shadow={"reason":"NO_VERIFIED_G1",
                               "completed_roles":[]},
                    stage_disposition="ADVISORY_ONLY_NO_OWNER_DECISION",
                )) as bulk:
            out=StringIO()
            with redirect_stdout(out):
                status=main([
                    "--m09-shadow-mainpath-once","--db",str(self.db),
                ])
        self.assertEqual(status,0)
        bulk.assert_called_once()
        data=json.loads(out.getvalue())
        self.assertIsNone(data["final_decision"])
        self.assertFalse(data["execution_allowed"])
        self.assertFalse(data["live_trading_authorized"])
        shadow=data["m09_original_shadow"]
        self.assertTrue(shadow["m07_hardlock_active"])
        self.assertFalse(shadow["m08_wallet_settled_idr_verified"])

    def test_original_cli_rejects_live_mode_and_nonisolated_journal(self):
        err=StringIO()
        with redirect_stderr(err):
            code=main(["--m09-shadow-mainpath-once",
                       "--db","runtime/decisions.sqlite3"])
        self.assertEqual(code,2)
        self.assertIn("M09_SHADOW_TMP_ORIGINAL_JOURNAL_REQUIRED",err.getvalue())
        with patch("indodax_ai_bot.__main__.Settings.from_env",
                   return_value=Settings(
                       trading_enabled=True,allow_live_execution=True,
                       execution_mode="LIVE")):
            bad=StringIO()
            with redirect_stderr(bad):
                ret=main(["--m09-shadow-mainpath-once",
                          "--db",str(self.db)])
        self.assertEqual(ret,2)

    def test_original_cli_emits_no_signed_orders_or_telegram_transport(self):
        with (patch.object(DailyScalpingOrchestrator,
                           "run_bulk_deep_cycle",
                           return_value=CycleResult(
                               "cli",None,"PUBLIC_SOURCE_MISSING",False,
                               stage_disposition="ADVISORY_ONLY_NO_OWNER_DECISION",
                           )),
              patch("indodax_ai_bot.m07_private_order_readonly._https_json",
                    side_effect=AssertionError("NO_PRIVATE_GET")) as private,
              patch("indodax_ai_bot.m07_order_adapter.OfficialV2OrderAdapter.create_order",
                    side_effect=AssertionError("NO_BUY_SELL")) as create):
            out=StringIO()
            with redirect_stdout(out):
                ret=main(["--m09-shadow-mainpath-once","--db",str(self.db)])
        self.assertEqual(ret,0)
        private.assert_not_called()
        create.assert_not_called()
        self.assertFalse(json.loads(out.getvalue())["execution_allowed"])


if __name__=="__main__":
    unittest.main()
