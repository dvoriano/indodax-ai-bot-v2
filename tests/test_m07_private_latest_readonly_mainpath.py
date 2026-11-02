"""M07 View Only latest order: genuine original CLI route, mocked private GET."""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.m07_private_order_readonly import (
    M07ReadOnlyError, _latest_client_id_from_history,
)
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator

T = 1793601000000
CLIENT = "injidr-example-1"
ACCOUNT = {
    "canTrade": False, "canWithdraw": False, "canWithdrawFiat": False,
    "uid": 777, "balances": [],
}


def history(client=CLIENT, epoch=T - 4000):
    return {"data": [{
        "symbol": "injidr", "clientOrderId": client,
        "submitTime": epoch, "status": "FILLED",
        "side": "BUY", "type": "LIMIT",
    }]}


def status(client=CLIENT):
    return {
        "symbol": "INJIDR", "clientOrderId": client,
        "orderId": 88, "fullOrderId": "injidr-limit-88",
        "side": "BUY", "type": "LIMIT", "status": "FILLED",
        "origQty": "0.2", "executedQty": "0.2", "time": T - 4000,
    }


def app(db):
    return DailyScalpingOrchestrator(Settings(), DecisionJournal(db))


class M07LatestReadonlyTests(unittest.TestCase):
    def test_real_original_cli_account_history_getorder_three_get_no_id_needed(self):
        with TemporaryDirectory() as tmp:
            seen = []
            def mocked(request):
                seen.append((request.path, request.query))
                return [ACCOUNT, history(), status()][len(seen) - 1]
            with (
                patch("indodax_ai_bot.__main__._existing_v2_credentials",
                      return_value=Path("/synthetic/readonly.json")),
                patch("indodax_ai_bot.__main__.read_view_only_credentials",
                      return_value=("FAKE_KEY","FAKE_SECRET")),
                patch("indodax_ai_bot.m07_private_order_readonly._https_json",
                      side_effect=mocked),
                patch("indodax_ai_bot.m07_private_order_readonly.time.time",
                      return_value=T / 1000),
            ):
                out, err = StringIO(), StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    rc = main([
                        "--m07-private-latest-order-once",
                        "--m07-private-symbol", "INJIDR",
                        "--db", str(Path(tmp)/"v2.sqlite3"),
                    ])
            self.assertEqual(rc, 0, err.getvalue())
            self.assertEqual([v[0] for v in seen], [
                "/api/v2/account", "/api/v2/order/histories", "/api/v2/order",
            ])
            self.assertIn("symbol=injidr&limit=10", seen[1][1])
            self.assertIn("origClientOrderId="+CLIENT, seen[2][1])
            result = json.loads(out.getvalue())["m07_private_readonly_order"]
            self.assertTrue(result["discovered_from_recent_history"])
            self.assertEqual(result["requests_completed"], 3)
            self.assertEqual(result["order_status"], "FILLED")
            self.assertFalse(result["bot_ownership_verified"])
            self.assertFalse(result["fills_and_fee_history_reconciled"])
            self.assertFalse(result["unknown_intent_released"])
            self.assertFalse(result["execution_allowed"])
            self.assertNotIn(CLIENT, out.getvalue())
            self.assertNotIn("FAKE_SECRET", out.getvalue())

    def test_empty_history_and_denied_permissions_never_query_order(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp)/"v2.sqlite3"
            with patch("indodax_ai_bot.m07_private_order_readonly._https_json",
                       side_effect=[ACCOUNT, {"data":[]}]) as get:
                with self.assertRaisesRegex(M07ReadOnlyError,
                                            "M07_HISTORY_EMPTY_UNRESOLVED"):
                    app(db).inspect_m07_private_latest_order_once(
                        symbol="INJIDR", api_key="fake", secret_key="secret",
                    )
                self.assertEqual(get.call_count,2)
            with patch("indodax_ai_bot.m07_private_order_readonly._https_json",
                       side_effect=[{**ACCOUNT, "canTrade":True}]) as get:
                with self.assertRaisesRegex(M07ReadOnlyError,
                                            "M07_KEY_NOT_PROVEN_VIEW_ONLY"):
                    app(db).inspect_m07_private_latest_order_once(
                        symbol="INJIDR", api_key="fake", secret_key="secret",
                    )
                self.assertEqual(get.call_count,1)

    def test_history_latest_max_submit_time_not_first_row_and_reject_ambiguity(self):
        rows = {"data": [
            {"symbol":"injidr","clientOrderId":"old","submitTime":T-10000},
            {"symbol":"injidr","clientOrderId":"new","submitTime":T-1000},
        ]}
        self.assertEqual(
            _latest_client_id_from_history(rows, symbol="INJIDR"), "new",
        )
        rows["data"].append({
            "symbol":"injidr","clientOrderId":"tie","submitTime":T-1000,
        })
        with self.assertRaisesRegex(M07ReadOnlyError,
                                    "M07_HISTORY_AMBIGUOUS_LATEST"):
            _latest_client_id_from_history(rows, symbol="INJIDR")

    def test_history_bad_shape_unknown_symbol_and_limit_refused(self):
        bad = [
            ({"data":[{"symbol":"btcidr","clientOrderId":CLIENT,
                        "submitTime":T}]}, "M07_HISTORY_SYMBOL_MISMATCH"),
            ({"data":[{"symbol":"injidr","clientOrderId":"invalid!!",
                        "submitTime":T}]}, "M07_HISTORY_CLIENT_ID_INVALID"),
            ({"data":[{"symbol":"injidr","clientOrderId":CLIENT,
                        "submitTime":None}]}, "M07_HISTORY_SUBMIT_TIME_INVALID"),
            ({"data":[]}, "M07_HISTORY_EMPTY_UNRESOLVED"),
            ({"data":[history()["data"][0]]*11}, "M07_HISTORY_LIMIT_INVALID"),
            ({"rows":[]}, "M07_HISTORY_RESPONSE_INVALID"),
        ]
        for payload, code in bad:
            with self.subTest(code=code):
                with self.assertRaisesRegex(M07ReadOnlyError, code):
                    _latest_client_id_from_history(payload, symbol="INJIDR")

    def test_latest_rejects_client_id_override_before_credentials(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp)/"v2.sqlite3"
            with patch("indodax_ai_bot.__main__._existing_v2_credentials") as lookup:
                out, err = StringIO(), StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    rc = main([
                        "--m07-private-latest-order-once",
                        "--m07-private-symbol","INJIDR",
                        "--m07-private-client-order-id",CLIENT,
                        "--db",str(db),
                    ])
                self.assertEqual(rc,2)
                lookup.assert_not_called()
            self.assertFalse(db.exists())


if __name__ == "__main__":
    unittest.main()
