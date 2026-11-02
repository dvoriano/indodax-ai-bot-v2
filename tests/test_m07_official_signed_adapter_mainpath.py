"""Original M07-01 TAPI V2 offline signed envelope and hardlock mainpath.

No real API key, private HTTP, paid AI, exchange order or CLI side effect
other than its isolated M01 decision journal. New official contract only.
"""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal
from hashlib import sha256
import hmac
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import parse_qs, urlencode
from unittest.mock import patch

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.m07_order_adapter import (
    M07Hardlocked, M07OrderDraft, OfficialV2OrderAdapter,
    PreparedM07Request,
)
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator


KEY = "FAKE_TAPI_V2_PUBLIC_TEST_KEY"
SECRET = "FAKE_LOCAL_TEST_SECRET"
EPOCH = 1793596260000


def kwargs():
    return {
        "api_key": KEY, "secret_key": SECRET,
        "timestamp_ms": EPOCH,
    }


class M07SignedAdapterMainpathTests(unittest.TestCase):
    def test_exact_signed_tapiv2_limit_create_post_body_and_redacted_repr(self):
        draft = M07OrderDraft(
            "BTCIDR", "BUY", "LIMIT", "v2-buy-01",
            price=Decimal("500000000"), quantity=Decimal("0.001"),
        )
        adapter = OfficialV2OrderAdapter()
        request = adapter.prepare_order_offline(draft, **kwargs())
        self.assertIs(type(request), PreparedM07Request)
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.path, "/api/v2/order")
        self.assertEqual(request.query, "")
        self.assertEqual(request.headers["Content-Type"],
                         "application/x-www-form-urlencoded")
        self.assertEqual(request.headers["X-APIKEY"], KEY)
        self.assertEqual(request.headers["Accept"], "application/json")
        params = parse_qs(request.body)
        self.assertEqual(params["symbol"], ["BTCIDR"])
        self.assertEqual(params["side"], ["BUY"])
        self.assertEqual(params["type"], ["LIMIT"])
        self.assertEqual(params["price"], ["500000000"])
        self.assertEqual(params["quantity"], ["0.001"])
        self.assertEqual(params["timeInForce"], ["GTC"])
        self.assertEqual(params["newClientOrderId"], ["v2-buy-01"])
        self.assertEqual(params["timestamp"], [str(EPOCH)])
        self.assertEqual(params["recvWindow"], ["5000"])
        self.assertEqual(
            request.headers["Sign"],
            hmac.new(SECRET.encode(), request.body.encode(),
                     sha256).hexdigest(),
        )
        self.assertNotIn(KEY, repr(request))
        self.assertNotIn(SECRET, repr(request))
        self.assertFalse(request.network_allowed)
        self.assertFalse(request.execution_allowed)

    def test_market_buy_quote_idr_vs_market_sell_base_and_limit_id_are_scoped(self):
        a = OfficialV2OrderAdapter()
        buy = a.prepare_order_offline(
            M07OrderDraft("ETHIDR", "BUY", "MARKET",
                          "eth-m-buy-01", quote_order_qty=10000),
            **kwargs(),
        )
        buy_params = parse_qs(buy.body)
        self.assertEqual(buy_params["quoteOrderQty"], ["10000"])
        self.assertNotIn("quantity", buy_params)
        self.assertNotIn("price", buy_params)
        sell = a.prepare_order_offline(
            M07OrderDraft("ETHIDR", "SELL", "MARKET",
                          "eth-m-sell-01", quantity=Decimal("0.01")),
            **kwargs(),
        )
        sell_params = parse_qs(sell.body)
        self.assertEqual(sell_params["quantity"], ["0.01"])
        self.assertNotIn("quoteOrderQty", sell_params)
        self.assertNotIn("price", sell_params)
        self.assertFalse(sell.execution_allowed)

    def test_status_get_and_cancel_delete_signed_only_with_client_order_id(self):
        a = OfficialV2OrderAdapter()
        get = a.prepare_status_offline(
            symbol="BTCIDR", orig_client_order_id="v2-buy-01", **kwargs()
        )
        delete = a.prepare_cancel_offline(
            symbol="BTCIDR", orig_client_order_id="v2-buy-01", **kwargs()
        )
        self.assertEqual(get.method, "GET")
        self.assertEqual(delete.method, "DELETE")
        for envelope in (get, delete):
            self.assertEqual(envelope.path, "/api/v2/order")
            self.assertEqual(envelope.body, "")
            self.assertEqual(parse_qs(envelope.query)[
                "origClientOrderId"], ["v2-buy-01"])
            self.assertEqual(envelope.headers["Sign"], hmac.new(
                SECRET.encode(), envelope.query.encode(), sha256
            ).hexdigest())
            self.assertFalse(envelope.network_allowed)
            self.assertFalse(envelope.execution_allowed)
            self.assertNotIn(KEY, repr(envelope))

    def test_invalid_pair_order_side_qty_price_and_unsafe_id_never_prepared(self):
        bad_drafts = (
            M07OrderDraft("BTCUSDT", "BUY", "LIMIT", "id-1",
                          Decimal("100"), Decimal("1")),
            M07OrderDraft("PEPEIDR", "BUY", "LIMIT", "id-1",
                          Decimal("100"), Decimal("1")),
            M07OrderDraft("BTCIDR", "BUY", "LIMIT", "../bad",
                          Decimal("100"), Decimal("1")),
            M07OrderDraft("BTCIDR", "BUY", "STOP_LIMIT", "id-1",
                          Decimal("100"), Decimal("1")),
            M07OrderDraft("BTCIDR", "SELL", "LIMIT", "id-1",
                          None, Decimal("1")),
            M07OrderDraft("BTCIDR", "BUY", "MARKET", "id-1",
                          quantity=Decimal("1"), quote_order_qty=10000),
            M07OrderDraft("BTCIDR", "BUY", "MARKET", "id-1",
                          quote_order_qty=True),
            M07OrderDraft("BTCIDR", "BUY", "MARKET", "id-1",
                          quote_order_qty=10000.5),
            M07OrderDraft("BTCIDR", "SELL", "MARKET", "id-1",
                          quantity=Decimal("NaN")),
            M07OrderDraft("BTCIDR", "BUY", "LIMIT", "id-1",
                          price=Decimal("-2"), quantity=Decimal("1")),
        )
        adapter = OfficialV2OrderAdapter()
        for draft in bad_drafts:
            with self.subTest(draft=draft), self.assertRaises(ValueError):
                adapter.prepare_order_offline(draft, **kwargs())
        for bad in ("not-an-id!", "", "../id"):
            with self.subTest(ref=bad), self.assertRaises(ValueError):
                adapter.prepare_cancel_offline(
                    symbol="BTCIDR", orig_client_order_id=bad, **kwargs()
                )
        with self.assertRaises(ValueError):
            adapter.prepare_status_offline(
                symbol="ETHUSDT", orig_client_order_id="ok", **kwargs()
            )
        with self.assertRaises(ValueError):
            adapter.prepare_order_offline(
                M07OrderDraft("BTCIDR", "BUY", "MARKET", "ok",
                              quote_order_qty=10000),
                api_key=KEY, secret_key=SECRET,
                timestamp_ms=EPOCH, recv_window_ms=60000,
            )

    def test_never_dispatch_even_with_credentials_or_environment_go_flags(self):
        adapter = OfficialV2OrderAdapter()
        envelope = adapter.prepare_order_offline(
            M07OrderDraft("BTCIDR", "BUY", "MARKET",
                          "intent-once", quote_order_qty=20000),
            **kwargs(),
        )
        with patch.dict(
            "os.environ", {
                "INDODAX_TRADING_ENABLED": "YES",
                "INDODAX_ALLOW_LIVE_EXECUTION": "YES",
                "INDODAX_EXECUTION_MODE": "LIVE",
                "M07_TRADE_GO": "YES",
            },
        ):
            for func, args in (
                (adapter.create_order, (envelope,)),
                (adapter.get_order, (envelope,)),
                (adapter.cancel_order, (envelope,)),
                (adapter.dispatch, (envelope,)),
            ):
                with self.subTest(func=func.__name__):
                    with self.assertRaises(M07Hardlocked):
                        func(*args)
        self.assertFalse(envelope.network_allowed)
        self.assertFalse(envelope.execution_allowed)

    def test_original_orchestrator_reports_explicitly_unavailable_order_contract(self):
        with TemporaryDirectory() as d:
            app = DailyScalpingOrchestrator(
                Settings(), DecisionJournal(Path(d) / "m07.sqlite3")
            )
            result = app.inspect_m07_official_order_adapter_once()
        self.assertEqual(result["status"], "HARDLOCKED_NO_PRIVATE_HTTP_OR_ORDER")
        self.assertEqual(result["create_method"], "POST")
        self.assertEqual(result["status_method"], "GET")
        self.assertEqual(result["cancel_method"], "DELETE")
        self.assertTrue(result["unknown_order_state_must_not_retry"])
        self.assertFalse(result["authenticated_account_scope_verified"])
        self.assertFalse(result["idempotent_reservation_verified"])
        self.assertFalse(result["network_allowed"])
        self.assertFalse(result["execution_allowed"])

    def test_original_cli_to_orchestrator_has_no_private_transport_or_trade(self):
        with TemporaryDirectory() as d:
            out, err = StringIO(), StringIO()
            with patch(
                "indodax_ai_bot.orchestrator."
                "OfficialV2OrderAdapter.contract_status",
                wraps=OfficialV2OrderAdapter.contract_status,
            ) as called:
                with redirect_stdout(out), redirect_stderr(err):
                    rc = main([
                        "--m07-order-adapter-offline-once", "--db",
                        str(Path(d) / "decisions.sqlite3"),
                    ])
            self.assertEqual(rc, 0, err.getvalue())
            called.assert_called_once()
            result = json.loads(out.getvalue())
            self.assertIsNone(result["final_decision"])
            self.assertFalse(result["execution_allowed"])
            self.assertFalse(result["live_trading_authorized"])
            self.assertEqual(result["m07_official_order_adapter"]["status"],
                             "HARDLOCKED_NO_PRIVATE_HTTP_OR_ORDER")


if __name__ == "__main__":
    unittest.main()
