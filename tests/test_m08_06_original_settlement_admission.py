"""M08-06: ORIGINAL V2 CLI → orchestrator → SAME read-only journal.

All tests use local private /tmp SQLite with exchange-shaped account ONLY
fixtures. Real BUY/SELL, bot-origin, wallet settlement and GO are absent.
Even valid-looking fee/qty and manually seeded future rows must stay BLOCK.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal, DecisionConflictError
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.m07_order_adapter import OfficialV2OrderAdapter, M07Hardlocked
from tests.test_m08_original_post_sell_rediscovery import (
    ACCOUNT, BUY_CID, SELL_CID, SYMBOL, NOW, account_order, account_fills,
)

EPOCH = NOW


class OriginalM08SettlementIssuerAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="m08-06-original-", dir="/tmp")
        self.addCleanup(temporary.cleanup)
        self.dir = Path(temporary.name)
        self.db = self.dir / "m11_offline_fixture.sqlite3"
        self.journal = DecisionJournal(self.db)
        self.db.chmod(0o600)
        self.file = self.dir / "m08-settlement-review.json"

    def args(self):
        return dict(account_fingerprint=ACCOUNT, symbol=SYMBOL,
                    source_buy_client_order_id=BUY_CID,
                    sell_client_order_id=SELL_CID,
                    observed_epoch=EPOCH)

    def account_row(self, side="BUY", status="FILLED", account=ACCOUNT,
                    qty="4"):
        order = account_order(side=side, status=status, qty=qty)
        self.journal.record_m07_authenticated_readonly_order_status(
            account_fingerprint=account, order=order,
            observed_ms=NOW * 1000 - 1000,
        )
        if status == "FILLED":
            self.journal.record_m07_exchange_readonly_fills(
                account_fingerprint=account, fills=account_fills(order),
                observed_ms=NOW * 1000 - 500,
            )

    def call(self, *, db=None, payload=None, file=None, reference=True,
             env=None, args=None):
        if payload is None:
            payload = self.args()
        if file is None:
            file = self.file
        if file == self.file:
            self.file.write_text(json.dumps(payload))
            self.file.chmod(0o600)
        if args is None:
            args = [
                "--m08-settlement-admission-offline-once",
                "--m08-settlement-admission-file", str(file),
                "--db", str(self.db if db is None else db),
            ]
        source = {
            "INDODAX_TRADING_ENABLED": "NO",
            "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
            "INDODAX_EXECUTION_MODE": "DRY_RUN",
        }
        source.update(env or {})
        out, err = StringIO(), StringIO()
        with patch.dict(os.environ, source, clear=True):
            with redirect_stdout(out), redirect_stderr(err):
                rc = main(args)
        return rc, out.getvalue(), err.getvalue()

    def check_deny(self, reply):
        self.assertIsNone(reply["final_decision"])
        self.assertFalse(reply["execution_allowed"])
        self.assertFalse(reply["live_trading_authorized"])
        result = reply["m08_settlement_issuer_admission"]
        self.assertEqual(result["status"],
                         "M08_06_BLOCK_UNVERIFIED_BOT_ORIGIN_AND_IDR_SETTLEMENT")
        self.assertFalse(result["independent_v2_bot_order_origin_verified"])
        self.assertFalse(result["verified_owned_position_available"])
        self.assertFalse(result["all_account_open_orders_reconciled"])
        self.assertFalse(result["wallet_settled_free_locked_idr_verified"])
        self.assertFalse(result["realized_wib_net_idr_verified"])
        self.assertFalse(result["canonical_m08_settlement_issuer_connected"])
        self.assertFalse(result["settlement_event_issued"])
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["live_trading_authorized"])
        self.assertFalse(result["order_sent"])
        self.assertIsNone(result["owner_final_decision"])
        self.assertEqual(result["private_gets_performed"], 0)
        for code in (
            "M08_BOT_OWNED_ORDER_ORIGIN_NOT_ATTESTED",
            "M08_WALLET_FREE_LOCKED_IDR_DELTA_NOT_RECONCILED",
            "M08_SETTLED_WIB_REALIZED_PNL_ISSUER_MISSING",
            "M08_ACCOUNT_WIDE_ORDER_AND_PEER_COVERAGE_UNVERIFIED",
        ):
            self.assertIn(code, result["reason_codes"])
        return result

    def counts(self):
        with sqlite3.connect(self.db) as db:
            return (
                db.execute("SELECT COUNT(*) FROM m08_position_settled_idr_events"
                           ).fetchone()[0],
                db.execute("SELECT COUNT(*) FROM m08_post_sell_discovery_claims"
                           ).fetchone()[0],
                db.execute("SELECT COUNT(*) FROM m07_offline_order_intents"
                           ).fetchone()[0],
            )

    def test_original_cli_without_account_fills_rejects(self):
        rc, out, err = self.call()
        self.assertEqual((rc, err), (0, ""))
        review = self.check_deny(json.loads(out))
        self.assertFalse(review["account_only_buy_fill_fees_reconciled"])
        self.assertFalse(review["account_only_sell_fill_fees_reconciled"])
        self.assertIsNone(review["account_pnl_idr_reference_only_unverified"])
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_valid_format_account_buy_sell_and_fee_arithmetic_only_not_issuer(self):
        self.account_row("BUY")
        self.account_row("SELL")
        before = sha256(self.db.read_bytes()).hexdigest()
        rc, out, err = self.call()
        self.assertEqual((rc, err), (0, ""))
        review = self.check_deny(json.loads(out))
        self.assertTrue(review["account_only_buy_fill_fees_reconciled"])
        self.assertTrue(review["account_only_sell_fill_fees_reconciled"])
        self.assertTrue(review["account_only_full_buy_qty_matched"])
        self.assertEqual(review["account_buy_cost_idr_reference"], "100200")
        self.assertEqual(review["account_sell_net_idr_reference"], "107000")
        self.assertEqual(review["account_pnl_idr_reference_only_unverified"], "6800")
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertEqual(sha256(self.db.read_bytes()).hexdigest(), before)

    def test_fee_including_tax_mismatch_rejected_and_no_realized(self):
        self.account_row("BUY")
        self.account_row("SELL")
        with sqlite3.connect(self.db) as db:
            db.execute(
                "UPDATE m07_exchange_readonly_fill_summaries "
                "SET commission_including_tax_idr='2' "
                "WHERE side='SELL'"
            )
        review = self.check_deny(json.loads(self.call()[1]))
        self.assertFalse(review["account_only_sell_fill_fees_reconciled"])
        self.assertIsNone(review["account_pnl_idr_reference_only_unverified"])
        self.assertIn("M08_SELL_FILL_QUANTITY_OR_FEE_CONFLICT",
                      review["reason_codes"])

    def test_partial_sell_never_full_settlement(self):
        self.account_row("BUY")
        self.account_row("SELL", status="PARTIALLY_FILLED")
        review = self.check_deny(json.loads(self.call()[1]))
        self.assertFalse(review["account_only_sell_fill_fees_reconciled"])
        self.assertIsNone(review["account_pnl_idr_reference_only_unverified"])

    def test_qty_mismatch_account_filled_but_cannot_close_full_buy(self):
        self.account_row("BUY")
        self.account_row("SELL", qty="3")
        review = self.check_deny(json.loads(self.call()[1]))
        self.assertTrue(review["account_only_sell_fill_fees_reconciled"])
        self.assertFalse(review["account_only_full_buy_qty_matched"])
        self.assertIn("M08_SELL_QTY_NOT_FULL_BUY_LOT", review["reason_codes"])
        self.assertIsNone(review["account_pnl_idr_reference_only_unverified"])

    def test_stale_order_and_fill_not_fresh_settlement_evidence(self):
        self.account_row("BUY")
        self.account_row("SELL")
        review = self.check_deny(json.loads(self.call(payload={
            **self.args(), "observed_epoch": NOW + 3601,
        })[1]))
        self.assertFalse(review["account_only_buy_fill_fees_reconciled"])
        self.assertFalse(review["account_only_sell_fill_fees_reconciled"])

    def test_foreign_account_sell_cannot_pair_with_buy(self):
        self.account_row("BUY")
        self.account_row("SELL", account="b"*64)
        review = self.check_deny(json.loads(self.call()[1]))
        self.assertFalse(review["account_only_sell_fill_fees_reconciled"])
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_cancel_unknown_or_active_sells_must_not_clear_inventory(self):
        self.account_row("BUY")
        self.account_row("SELL", status="NEW")
        review = self.check_deny(json.loads(self.call()[1]))
        self.assertGreater(review["account_open_sells_observed"], 0)
        self.assertIn("M08_ACTIVE_EXCHANGE_SELL_NOT_SETTLED",
                      review["reason_codes"])
        self.assertFalse(review["verified_owned_position_available"])

    def test_forged_existing_future_settlement_row_never_serves_as_issuer(self):
        self.account_row("BUY")
        self.account_row("SELL")
        with sqlite3.connect(self.db) as db:
            db.execute(
                "INSERT INTO m08_position_settled_idr_events "
                "(event_id,account_fingerprint,symbol,"
                "source_buy_client_order_id,sell_client_order_id,"
                "sell_full_order_id,sell_fill_digest,"
                "bot_owned_execution_digest,idr_wallet_settlement_digest,"
                "net_settled_idr,settled_at_ms,source,status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("synthetic_future", ACCOUNT, SYMBOL, BUY_CID, SELL_CID,
                 "M08_SELL_101", "c"*64, "e"*64, "f"*64, "107000",
                 (NOW-5)*1000, "M08_RECONCILED_BOT_OWNED_SELL_IDR",
                 "POSITION_SETTLED_IDR"),
            )
        before = self.counts()
        review = self.check_deny(json.loads(self.call()[1]))
        self.assertEqual(self.counts(), before)
        self.assertEqual(review["m08_settlement_event_rows_before"], 1)
        self.assertEqual(review["m08_settlement_event_rows_after"], 1)
        self.assertFalse(review["canonical_m08_settlement_issuer_connected"])

    def test_original_journal_reopening_readonly_restart_no_event(self):
        self.account_row("BUY")
        self.account_row("SELL")
        for _ in range(2):
            review = self.check_deny(json.loads(self.call()[1]))
            self.assertTrue(review["account_only_full_buy_qty_matched"])
        self.assertEqual(self.counts(), (0, 0, 0))
        for suffix in ("-wal", "-shm", "-journal"):
            self.assertFalse(Path(str(self.db) + suffix).exists())

    def test_direct_original_orchestrator_rejects_writable_journal(self):
        app = DailyScalpingOrchestrator(Settings(), self.journal)
        with self.assertRaisesRegex(
            DecisionConflictError, "M08_SETTLEMENT_READONLY_JOURNAL_REQUIRED"
        ):
            app.review_m08_original_sell_settlement_admission_offline_once(
                **self.args())

    def test_original_order_adapter_remains_hardlocked(self):
        with self.assertRaises(M07Hardlocked):
            OfficialV2OrderAdapter().create_order()
        with self.assertRaises(M07Hardlocked):
            OfficialV2OrderAdapter().cancel_order()
        with self.assertRaises(M07Hardlocked):
            OfficialV2OrderAdapter().dispatch()

    def test_unrelated_cli_action_and_orphan_file_refused(self):
        rc, out, err = self.call(args=[
            "--health", "--m08-settlement-admission-file", str(self.file),
        ])
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("M08_SETTLEMENT_FIXTURE_ONLY_FOR_OFFLINE_ADMISSION", err)

    def test_wrong_name_missing_db_or_file_denied_before_constructor(self):
        bad_dbs = [
            self.dir / "no.db",
            self.dir / "decisions.sqlite3",
            Path("/tmp/m11_offline_fixture.sqlite3"),
        ]
        for p in bad_dbs:
            with self.subTest(path=str(p)):
                rc, out, err = self.call(db=p)
                self.assertEqual((rc, out), (2, ""))
                self.assertIn("M08_SETTLEMENT_OFFLINE_ADMISSION_UNVERIFIED", err)
        self.assertFalse((self.dir / "no.db").exists())

    def test_world_readable_db_or_directory_and_sidecars_denied(self):
        self.db.chmod(0o644)
        self.assertEqual(self.call()[0], 2)
        self.db.chmod(0o600)
        p = Path(str(self.db) + "-wal")
        p.write_bytes(b"unsafe")
        self.assertEqual(self.call()[0], 2)

    def test_input_file_outside_same_private_fixture_directory_denied(self):
        other = TemporaryDirectory(prefix="m08-06-other-", dir="/tmp")
        self.addCleanup(other.cleanup)
        p = Path(other.name) / "other.json"
        p.write_text(json.dumps(self.args()))
        p.chmod(0o600)
        rc, out, err = self.call(file=p)
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("M08_SETTLEMENT_OFFLINE_ADMISSION_UNVERIFIED", err)

    def test_bad_or_missing_schema_and_crossed_or_foreign_id_denied(self):
        for bad in (
            {"account_fingerprint": ACCOUNT},
            {**self.args(), "observed_epoch": True},
            {**self.args(), "symbol": "FLOKIIDR"},
            {**self.args(), "account_fingerprint": "not-valid"},
            {**self.args(), "sell_client_order_id": BUY_CID},
        ):
            with self.subTest(bad=bad):
                rc, out, err = self.call(payload=bad)
                self.assertEqual((rc, out), (2, ""))
                self.assertIn("M08_SETTLEMENT_OFFLINE_ADMISSION_UNVERIFIED", err)

    def test_live_env_denied_before_readonly_db_or_network(self):
        for mode in (
            {"INDODAX_TRADING_ENABLED": "YES"},
            {"INDODAX_ALLOW_LIVE_EXECUTION": "YES"},
            {"INDODAX_EXECUTION_MODE": "LIVE"},
        ):
            with self.subTest(mode=mode):
                with patch.object(
                    DecisionJournal, "open_m11_offline_fixture_readonly",
                    side_effect=AssertionError("MUST_NOT_OPEN"),
                ):
                    rc, out, err = self.call(env=mode)
                self.assertEqual((rc, out), (2, ""))
                self.assertIn("M01_LIVE_NOT_IMPLEMENTED", err)

    def test_no_exchange_network_paid_ai_telegram_or_order(self):
        with (patch("indodax_ai_bot.__main__.TelegramBotApiClient") as tg,
              patch("indodax_ai_bot.__main__.SumopodChatAdapter") as ai,
              patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket") as public,
              patch("indodax_ai_bot.orchestrator.OfficialV2OrderAdapter") as adapter):
            rc, out, err = self.call()
        self.assertEqual((rc, err), (0, ""))
        self.check_deny(json.loads(out))
        for mock in (tg, ai, public, adapter):
            mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
