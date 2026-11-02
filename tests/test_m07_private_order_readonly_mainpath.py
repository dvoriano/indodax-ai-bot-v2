"""Original M07 authenticated View Only GET order proof; all transport mocked."""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal, DecisionConflictError
from indodax_ai_bot.m07_order_adapter import (
    OfficialV2OrderAdapter, M07OrderDraft, M07Hardlocked,
)
from indodax_ai_bot.m07_private_order_readonly import (
    normalize_order_status, M07ReadOnlyError, _https_json,
)
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator

KEY = "FAKE_LOCAL_VIEW_ONLY_KEY"
SECRET = "FAKE_LOCAL_VIEW_ONLY_SECRET"
MS = 1793601000000
CID = "v2-owner-order-1"
ACCOUNT = {
    "canTrade": False, "canWithdraw": False, "canWithdrawFiat": False,
    "uid": 99887766, "balances": [],
}


def order(*, status="NEW", executed="0", cid=CID, symbol="BTCIDR",
          order_id=6423, original="0.02"):
    return {
        "symbol": symbol, "orderId": order_id,
        "clientOrderId": cid, "side": "BUY", "type": "LIMIT",
        "origQty": original, "executedQty": executed,
        "status": status, "price": "421004000",
        "time": MS - 60000, "fullOrderId": "btcidr-limit-" + str(order_id),
    }


def app(db):
    return DailyScalpingOrchestrator(Settings(), DecisionJournal(db))


class M07RealOrderReadOnlyTests(unittest.TestCase):
    def test_original_cli_account_first_then_exact_order_signed_get(self):
        with TemporaryDirectory() as tmp:
            calls = []
            def fake(request):
                calls.append((request.path, request.query, request.headers))
                return ACCOUNT if len(calls) == 1 else order()
            with (
                patch("indodax_ai_bot.__main__._existing_v2_credentials",
                      return_value=Path("/synthetic/v2/0600.json")),
                patch("indodax_ai_bot.__main__.read_view_only_credentials",
                      return_value=(KEY, SECRET)),
                patch("indodax_ai_bot.m07_private_order_readonly._https_json",
                      side_effect=fake),
                patch("indodax_ai_bot.m07_private_order_readonly.time.time",
                      return_value=MS / 1000),
            ):
                out, err = StringIO(), StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    rc = main([
                        "--m07-private-order-status-once",
                        "--m07-private-symbol", "BTCIDR",
                        "--m07-private-client-order-id", CID,
                        "--db", str(Path(tmp) / "v2.sqlite3"),
                    ])
            self.assertEqual(rc, 0, err.getvalue())
            self.assertEqual(len(calls), 2)
            self.assertEqual([c[0] for c in calls],
                             ["/api/v2/account", "/api/v2/order"])
            self.assertTrue(calls[1][1].startswith(
                "symbol=BTCIDR&origClientOrderId=" + CID))
            self.assertEqual(set(calls[0][2]), {"Accept", "X-APIKEY", "Sign"})
            report = json.loads(out.getvalue())
            self.assertTrue(report["m07_private_readonly_order"][
                "view_only_permissions_verified"])
            self.assertFalse(report["execution_allowed"])
            self.assertFalse(report["m07_private_readonly_order"][
                "bot_ownership_verified"])
            self.assertNotIn(KEY, out.getvalue())
            self.assertNotIn(SECRET, out.getvalue())
            self.assertNotIn("99887766", out.getvalue())
            self.assertNotIn(CID, out.getvalue())
            self.assertNotIn("balances", out.getvalue())
            with self.assertRaises(M07Hardlocked):
                OfficialV2OrderAdapter().get_order()

    def test_permission_denied_prevents_second_get_and_no_status_record(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp)/"v2.sqlite3"
            calls = []
            def fake(request):
                calls.append(request.path)
                return {**ACCOUNT, "canTrade": True}
            with patch("indodax_ai_bot.m07_private_order_readonly._https_json",
                       side_effect=fake):
                with self.assertRaisesRegex(M07ReadOnlyError,
                                            "M07_KEY_NOT_PROVEN_VIEW_ONLY"):
                    app(db).inspect_m07_private_readonly_order_once(
                        symbol="BTCIDR", client_order_id=CID,
                        api_key=KEY, secret_key=SECRET,
                    )
            self.assertEqual(calls, ["/api/v2/account"])
            with DecisionJournal(db)._connect() as con:
                self.assertEqual(con.execute(
                    "SELECT COUNT(*) FROM m07_exchange_readonly_orders"
                ).fetchone()[0], 0)

    def test_not_found_is_unresolved_no_retry_or_release(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp)/"v2.sqlite3"
            obs = [ACCOUNT, {"code": -2013, "msg": "Order not found"}]
            with patch("indodax_ai_bot.m07_private_order_readonly._https_json",
                       side_effect=obs) as fake:
                with self.assertRaisesRegex(M07ReadOnlyError,
                                            "M07_ORDER_NOT_FOUND_UNRESOLVED"):
                    app(db).inspect_m07_private_readonly_order_once(
                        symbol="BTCIDR", client_order_id=CID,
                        api_key=KEY, secret_key=SECRET,
                    )
                self.assertEqual(fake.call_count, 2)
            with DecisionJournal(db)._connect() as con:
                self.assertEqual(con.execute(
                    "SELECT COUNT(*) FROM m07_exchange_readonly_orders"
                ).fetchone()[0], 0)

    def test_readonly_status_progress_preserves_offline_unknown_no_ownership(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp)/"v2.sqlite3"
            a = app(db)
            a.reserve_m07_offline_intent_once(
                intent_id=CID,
                draft=M07OrderDraft(
                    "BTCIDR", "BUY", "MARKET", CID, quote_order_qty=10000),
                observed_epoch=MS // 1000 - 20,
            )
            a.simulate_m07_unknown_timeout_once(
                intent_id=CID, observed_epoch=MS // 1000 - 10,
            )
            for status, executed, now in [
                    ("NEW", "0", MS),
                    ("PARTIALLY_FILLED", "0.01", MS + 1000),
                    ("FILLED", "0.02", MS + 2000)]:
                with (
                    patch("indodax_ai_bot.m07_private_order_readonly._https_json",
                          side_effect=[ACCOUNT, order(status=status,
                                                      executed=executed)]),
                    patch("indodax_ai_bot.m07_private_order_readonly.time.time",
                          return_value=now / 1000),
                ):
                    report = app(db).inspect_m07_private_readonly_order_once(
                        symbol="BTCIDR", client_order_id=CID,
                        api_key=KEY, secret_key=SECRET,
                    )
                    self.assertEqual(report["order_status"], status)
                    self.assertFalse(report["unknown_intent_released"])
                    self.assertFalse(report["fills_and_fee_history_reconciled"])
                    self.assertFalse(report["execution_allowed"])
            summary = app(db).inspect_m07_offline_intent_guard_once()
            self.assertEqual(summary["offline_unknown_holds"], 1)
            self.assertTrue(summary["additional_buy_reservations_blocked"])
            with DecisionJournal(db)._connect() as con:
                row = con.execute(
                    "SELECT status,executed_qty FROM m07_exchange_readonly_orders"
                ).fetchone()
            self.assertEqual(row, ("FILLED", "0.02"))

    def test_duplicate_read_is_idempotent_and_conflicting_terminal_blocked(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp)/"v2.sqlite3"
            a = app(db)
            def run(body, now):
                with (
                    patch("indodax_ai_bot.m07_private_order_readonly._https_json",
                          side_effect=[ACCOUNT, body]),
                    patch("indodax_ai_bot.m07_private_order_readonly.time.time",
                          return_value=now / 1000),
                ):
                    return a.inspect_m07_private_readonly_order_once(
                        symbol="BTCIDR", client_order_id=CID,
                        api_key=KEY, secret_key=SECRET,
                    )
            first = run(order(status="FILLED", executed="0.02"), MS)
            again = run(order(status="FILLED", executed="0.02"), MS + 1000)
            self.assertFalse(first["observation_replayed"])
            self.assertTrue(again["observation_replayed"])
            with self.assertRaisesRegex(DecisionConflictError,
                                        "M07_READONLY_TERMINAL_CONFLICT"):
                run(order(status="CANCELLED", executed="0.02"), MS + 2000)
            with self.assertRaisesRegex(DecisionConflictError,
                                        "M07_READONLY_ORDER_IDENTITY_CHANGED"):
                run(order(status="FILLED", executed="0.02",
                          order_id=7000), MS + 3000)

    def test_identity_and_documented_enum_must_match(self):
        for payload, code in (
            (order(symbol="ETHIDR"), "M07_ORDER_IDENTITY_MISMATCH"),
            (order(cid="other"), "M07_ORDER_IDENTITY_MISMATCH"),
            (order(status="CANCELED"), "M07_ORDER_ENUM_INVALID"),
            (order(status="FILLED", executed="0"), "M07_ORDER_STATUS_QUANTITY_CONFLICT"),
            (order(status="NEW", executed="0.01"), "M07_ORDER_STATUS_QUANTITY_CONFLICT"),
            (order(status="PARTIALLY_FILLED", executed="0.03"),
             "M07_ORDER_EXECUTED_EXCEEDS_ORIGINAL"),
            (order(original="NaN"), "M07_ORDER_QUANTITY_INVALID"),
            (order(original="1e999"), "M07_ORDER_QUANTITY_INVALID"),
        ):
            with self.subTest(code=code):
                with self.assertRaisesRegex(M07ReadOnlyError, code):
                    normalize_order_status(
                        payload, expected_symbol="BTCIDR",
                        expected_client_id=CID,
                    )

    def test_bad_cli_inputs_do_not_read_credentials_or_create_journal(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp)/"journal.sqlite3"
            for args in [
                ["--m07-private-order-status-once"],
                ["--m07-private-order-status-once", "--m07-private-symbol",
                 "BTCIDR", "--m07-private-client-order-id", "DROP;TABLE"],
                ["--health", "--m07-private-symbol", "BTCIDR"],
            ]:
                out, err = StringIO(), StringIO()
                with patch("indodax_ai_bot.__main__._existing_v2_credentials") as discovery:
                    with redirect_stdout(out), redirect_stderr(err):
                        rc = main(args + ["--db", str(db)])
                    self.assertEqual(rc, 2)
                    discovery.assert_not_called()
            self.assertFalse(db.exists())


if __name__ == "__main__":
    unittest.main()
