"""M09-03 original V2 single CLI→shadow→M07/M08 recovery, no order.

Authenticated-account order status is an OFFLINE fixture, never an
attestation that V2 submitted the order or settled a sell. M08-06 has
NO canonical settlement issuer and M09 must NEVER claim fake events.
"""
from __future__ import annotations

from contextlib import redirect_stdout,redirect_stderr
from dataclasses import replace
from decimal import Decimal as D
from io import StringIO
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal,DecisionConflictError
from indodax_ai_bot.m07_order_adapter import M07OrderDraft
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator,CycleResult
from tests.test_m08_original_post_sell_rediscovery import (
    ACCOUNT, NOW, EVT, SELL_CID, BUY_CID, account_order,
)


CANDIDATE_SELL="m09freshsell001"


class OriginalM09RecoveryShadowTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db=Path(tmp.name)/"original-v2.sqlite3"
        self.app=DailyScalpingOrchestrator(
            Settings(),DecisionJournal(self.db))
        self.count=0

    def observe(self,status="PARTIALLY_FILLED",*,executed="1",
                account=ACCOUNT):
        self.count+=1
        item=replace(account_order(),status=status,
                     executed_qty=D(executed))
        self.app.journal.record_m07_authenticated_readonly_order_status(
            account_fingerprint=account,order=item,
            observed_ms=NOW*1000+self.count*1000,
        )

    def recovered(self,*,app=None,candidate=CANDIDATE_SELL,
                  event_id=EVT,account=ACCOUNT,trace=None):
        app=app or self.app
        source=CycleResult(
            "m09offline",None,"G1_PUBLIC_BULK_SOURCE_UNVERIFIED",False,
            stage_disposition="ADVISORY_ONLY_NO_OWNER_DECISION",
        )
        self.count+=1
        with (patch.object(app,"run_bulk_deep_cycle",
                           return_value=source) as original_shadow,
              patch.object(app,"run_m08_post_sell_rediscovery_once",
                           side_effect=AssertionError("NO_POST_SELL_SCAN")) as after_sell):
            report=app.run_m09_original_recovery_shadow_once(
                trace or "m09recovery"+str(self.count),
                account_fingerprint=account,symbol="XRPIDR",
                source_buy_client_order_id=BUY_CID,
                sell_client_order_id=candidate,
                requested_settlement_event_id=event_id,
            )
        self.assertEqual(original_shadow.call_count,1)
        after_sell.assert_not_called()
        return report

    def test_absent_order_or_event_denies_rediscovery(self):
        result=self.recovered()
        self.assertEqual(result["status"],
                         "M09_RECOVERY_ACCOUNT_ORDER_COVERAGE_UNVERIFIED")
        self.assertFalse(result["post_sell_rediscovery_eligible"])
        self.assertFalse(result["post_sell_scanner_invoked"])
        self.assertFalse(result["signed_order_sent"])
        self.assertFalse(result["exchange_wallet_idr_settled_verified"])
        self.assertFalse(result["execution_allowed"])

    def test_partial_fill_never_recredits_pending_qty(self):
        self.observe()
        r=self.recovered()
        self.assertEqual(r["status"],"M09_RECOVERY_PARTIAL_SELL_UNSETTLED")
        self.assertEqual(r["m08_account_seen_partial_sells"],1)
        self.assertEqual(r["m08_account_seen_pending_base_reference"],"3")
        self.assertFalse(r["sell_unsettled_inventory_recredited"])
        self.assertFalse(r["order_intent_created_by_recovery"])

    def test_partial_cancel_restart_does_not_release_hold(self):
        self.observe()
        self.observe("CANCELLED",executed="1")
        restart=DailyScalpingOrchestrator(
            Settings(),DecisionJournal(self.db))
        report=self.recovered(app=restart)
        self.assertEqual(report["status"],"M09_RECOVERY_CANCELLED_NO_RELEASE")
        self.assertEqual(report["m08_account_seen_cancelled_sells"],1)
        self.assertFalse(report["sell_unsettled_inventory_recredited"])
        self.assertFalse(report["post_sell_scanner_invoked"])

    def test_filled_readonly_account_status_not_wallet_settlement(self):
        self.observe("FILLED",executed="4")
        r=self.recovered()
        self.assertEqual(r["status"],
                         "M09_RECOVERY_ACCOUNT_SELL_NOT_SETTLED_IDR")
        self.assertEqual(r["m08_account_seen_filled_sells"],1)
        self.assertFalse(r["exchange_sell_bot_owned_verified"])
        self.assertFalse(r["realized_pnl_recorded"])
        self.assertFalse(r["spendable_idr_authorized"])

    def test_synthetic_unknown_stays_blocked_across_restart(self):
        d=M07OrderDraft(
            symbol="ETHIDR",side="BUY",order_type="LIMIT",
            client_order_id="m09unkown001",price=D("2000"),
            quantity=D("2"),
        )
        self.app.reserve_m07_offline_intent_once(
            intent_id="m09unkown001",draft=d,observed_epoch=NOW,
        )
        self.app.simulate_m07_unknown_timeout_once(
            intent_id="m09unkown001",observed_epoch=NOW+1,
        )
        restarted=DailyScalpingOrchestrator(
            Settings(),DecisionJournal(self.db))
        for i in range(2):
            with self.subTest(i=i):
                r=self.recovered(app=restarted)
                self.assertEqual(r["status"],
                                 "M09_RECOVERY_UNKNOWN_HOLD_BLOCKED")
                self.assertEqual(r["m07_unknown_holds"],1)
                self.assertFalse(r["m07_unknown_hold_released"])
        self.assertEqual(self.app.journal.m07_offline_intent_summary()[
            "offline_unknown_holds"],1)

    def test_duplicate_existing_sell_client_id_never_retries(self):
        self.observe("NEW",executed="0")
        r=self.recovered(candidate=SELL_CID)
        self.assertEqual(r["status"],"M09_RECOVERY_DUPLICATE_ID_BLOCKED")
        self.assertTrue(r["m08_duplicate_sell_client_id"])
        self.assertFalse(r["signed_order_sent"])

    def test_account_uid_isolation_no_borrowed_status(self):
        self.observe(account="b"*64)
        r=self.recovered()
        self.assertEqual(r["status"],
                         "M09_RECOVERY_ACCOUNT_ORDER_COVERAGE_UNVERIFIED")
        self.assertEqual(r["m08_account_seen_partial_sells"],0)

    def test_inserted_future_like_event_cannot_force_scanner_or_idr(self):
        # This fixture is NOT an M08-06 issuer or real bot-owned receipt.
        with sqlite3.connect(self.db) as db:
            db.execute(
                "INSERT INTO m08_position_settled_idr_events "
                "(event_id,account_fingerprint,symbol,"
                "source_buy_client_order_id,sell_client_order_id,"
                "sell_full_order_id,sell_fill_digest,"
                "bot_owned_execution_digest,idr_wallet_settlement_digest,"
                "net_settled_idr,settled_at_ms,source,status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (EVT,ACCOUNT,"XRPIDR",BUY_CID,SELL_CID,"M08_SELL_101",
                 "c"*64,"d"*64,"e"*64,"90000",NOW*1000,
                 "M08_RECONCILED_BOT_OWNED_SELL_IDR",
                 "POSITION_SETTLED_IDR"),
            )
        r=self.recovered(event_id=EVT)
        self.assertEqual(r["m08_future_settlement_rows_untrusted"],1)
        self.assertFalse(r["m08_genuine_settlement_issuer_exists"])
        self.assertFalse(r["original_post_sell_event_claimed"])
        self.assertFalse(r["exchange_wallet_idr_settled_verified"])
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m08_post_sell_discovery_claims"
            ).fetchone()[0],0)

    def test_during_scan_order_state_change_fails_closed(self):
        self.observe("PARTIALLY_FILLED",executed="1")
        def race(_trace,*a,**kwargs):
            self.observe("CANCELLED",executed="1")
            return CycleResult(
                "m09race",None,"PUBLIC_NOT_ATTESTED",False,
                stage_disposition="ADVISORY_ONLY_NO_OWNER_DECISION")
        with patch.object(self.app,"run_bulk_deep_cycle",side_effect=race):
            with self.assertRaisesRegex(
                    ValueError,"M09_RECOVERY_JOURNAL_CHANGED_DURING_SHADOW"):
                self.app.run_m09_original_recovery_shadow_once(
                    "m09race",account_fingerprint=ACCOUNT,symbol="XRPIDR",
                    source_buy_client_order_id=BUY_CID,
                    sell_client_order_id=CANDIDATE_SELL,
                    requested_settlement_event_id=EVT)
        self.assertFalse(self.app.journal.review_m09_original_m08_position_and_idr_readonly()[
            "wallet_settled_idr_verified"])

    def test_invalid_identity_fails_before_public_scanner(self):
        with patch.object(self.app,"run_bulk_deep_cycle") as scanner:
            with self.assertRaises((ValueError,DecisionConflictError)):
                self.app.run_m09_original_recovery_shadow_once(
                    "m09bad",account_fingerprint="invalid",
                    symbol="XRPIDR",
                    source_buy_client_order_id=BUY_CID,
                    sell_client_order_id=CANDIDATE_SELL,
                    requested_settlement_event_id=EVT)
        scanner.assert_not_called()

    def test_cli_isolated_original_journal_denies_any_post_sell(self):
        fixture=self.db.parent/"recover.json"
        fixture.write_text(json.dumps({
            "account_fingerprint":ACCOUNT,"symbol":"XRPIDR",
            "source_buy_client_order_id":BUY_CID,
            "sell_client_order_id":CANDIDATE_SELL,
            "requested_settlement_event_id":EVT,
        }))
        fixture.chmod(0o600)
        mock_cycle=CycleResult(
            "m09cli",None,"G1_UNVERIFIED",False,
            stage_disposition="ADVISORY_ONLY_NO_OWNER_DECISION")
        with (patch.object(DailyScalpingOrchestrator,
                           "run_bulk_deep_cycle",return_value=mock_cycle) as shadow,
              patch.object(DailyScalpingOrchestrator,
                           "run_m08_post_sell_rediscovery_once",
                           side_effect=AssertionError("SCANNER_MUST_NOT_RUN")) as rediscovery):
            output=StringIO()
            with redirect_stdout(output):
                code=main([
                    "--m09-recovery-shadow-once",
                    "--m09-recovery-file",str(fixture),
                    "--db",str(self.db),
                ])
        self.assertEqual(code,0)
        shadow.assert_called_once()
        rediscovery.assert_not_called()
        result=json.loads(output.getvalue())
        self.assertIsNone(result["final_decision"])
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["m09_recovery_shadow"]["post_sell_scanner_invoked"])

    def test_cli_rejects_extra_secret_keys_without_public_scan(self):
        fixture=self.db.parent/"unexpected.json"
        fixture.write_text(json.dumps({
            "account_fingerprint":ACCOUNT,"symbol":"XRPIDR",
            "source_buy_client_order_id":BUY_CID,
            "sell_client_order_id":CANDIDATE_SELL,
            "requested_settlement_event_id":EVT,
            "api_secret":"MUST_NEVER_LEAK",
        }))
        fixture.chmod(0o600)
        with patch.object(DailyScalpingOrchestrator,"run_bulk_deep_cycle") as scanner:
            output=StringIO()
            with redirect_stderr(output):
                code=main([
                    "--m09-recovery-shadow-once",
                    "--m09-recovery-file",str(fixture),
                    "--db",str(self.db),
                ])
        self.assertEqual(code,3)
        scanner.assert_not_called()
        self.assertNotIn("MUST_NEVER_LEAK",output.getvalue())
        self.assertFalse(json.loads(output.getvalue())["execution_allowed"])


if __name__=="__main__":
    unittest.main()
