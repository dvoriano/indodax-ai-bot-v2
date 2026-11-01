"""M06-03 V2 25/75 FLOKI and 30/70 PEPE: conservative same-path tests.

No private network, exchange orders, actual bot-owned lots or live trade rights.
"""
import tempfile
import time
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.m06_account_review import (
    review_m06_special_partitions, reconcile_readonly_private_snapshot,
)
from indodax_ai_bot.m06_ownership_ledger import M06OwnershipLedger, OwnershipConflict
from indodax_ai_bot.m06_private_payloads import AccountBalances
from indodax_ai_bot.m06_scoped_https import M06ScopedApproval, M06RequestBudget
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator


def account(uid=481, floki="1000", pepe="1000"):
    return AccountBalances(
        uid,
        (("IDR", D("5000"), D("250")),
         ("FLOKI", D(floki), D("0")),
         ("PEPE", D(pepe), D("0"))),
        D("5000"), D("250"),
    )


class M06SpecialPoolOriginalMainpathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "same-v2-ledger.sqlite3"
        self.ledger = M06OwnershipLedger(self.path)
        self.path.chmod(0o600)
        self.scope = self.ledger.account_scope_from_exchange_uid(481)

    def set_floors(self, floki="0", pepe="0", uid=481):
        scope = self.ledger.account_scope_from_exchange_uid(uid)
        self.ledger.protect_manual_asset_floor(
            account_key=scope, asset="FLOKI", minimum_qty=D(floki))
        self.ledger.protect_manual_asset_floor(
            account_key=scope, asset="PEPE", minimum_qty=D(pepe))

    def read(self, uid=481):
        return self.ledger.inspect_existing_account_readonly(
            db_path=str(self.path), account_uid=uid)

    def test_base_fractions_are_exact_and_never_sellable(self):
        partitions = review_m06_special_partitions(
            account=account(), local_rebuy=None,
        )
        self.assertEqual(tuple(p.asset for p in partitions), ("FLOKI", "PEPE"))
        self.assertEqual(tuple(p.minimum_protected_qty for p in partitions),
                         (D("750"), D("700")))
        self.assertEqual(tuple(p.theoretical_trading_cap_qty for p in partitions),
                         (D("250"), D("300")))
        self.assertTrue(all(
            p.bot_owned_sellable_qty == 0
            and not p.execution_allowed
            and not p.wallet_ownership_verified
            and not p.local_manual_floor_recorded
            for p in partitions
        ))

    def test_manual_floor_can_only_tighten_the_fractional_reserves(self):
        self.set_floors("900", "850")
        local = self.read()
        self.assertTrue(local.floki_manual_floor_recorded)
        self.assertTrue(local.pepe_manual_floor_recorded)
        partitions = review_m06_special_partitions(
            account=account(), local_rebuy=local,
        )
        self.assertEqual(tuple(p.minimum_protected_qty for p in partitions),
                         (D("900"), D("850")))
        self.assertEqual(tuple(p.theoretical_trading_cap_qty for p in partitions),
                         (D("100"), D("150")))
        self.assertEqual(tuple(p.bot_owned_sellable_qty for p in partitions),
                         (D("0"), D("0")))
        decision = reconcile_readonly_private_snapshot(
            account=account(), open_orders=(), order_history=(),
            fills=(), symbol="injidr", local_rebuy=local,
        )
        self.assertTrue(decision.special_pool_local_floors_complete)
        self.assertFalse(decision.manual_asset_ownership_verified)
        self.assertFalse(decision.spendable_capital_verified)
        self.assertFalse(decision.execution_allowed)

    def test_missing_one_manual_floor_is_unknown_not_zero_attestation(self):
        self.ledger.protect_manual_asset_floor(
            account_key=self.scope, asset="FLOKI", minimum_qty=D("500"),
        )
        local = self.read()
        self.assertTrue(local.floki_manual_floor_recorded)
        self.assertFalse(local.pepe_manual_floor_recorded)
        review = reconcile_readonly_private_snapshot(
            account=account(), open_orders=(), order_history=(),
            fills=(), symbol="injidr", local_rebuy=local,
        )
        self.assertFalse(review.special_pool_local_floors_complete)
        self.assertFalse(review.manual_asset_ownership_verified)
        self.assertFalse(review.execution_allowed)

    def test_manual_quantity_over_wallet_fails_closed(self):
        self.set_floors("1001", "0")
        with self.assertRaisesRegex(ValueError, "FLOOR_EXCEEDS_WALLET"):
            reconcile_readonly_private_snapshot(
                account=account(), open_orders=(), order_history=(),
                fills=(), symbol="injidr", local_rebuy=self.read(),
            )
        self.assertEqual(self.read().floki_manual_qty_local_floor, D("1001"))

    def test_uid_isolation_survives_restart(self):
        self.set_floors("800", "900", uid=481)
        self.set_floors("700", "700", uid=482)
        first = self.read(481)
        other = self.read(482)
        self.assertNotEqual(first.floki_manual_qty_local_floor,
                            other.floki_manual_qty_local_floor)
        self.assertEqual(M06OwnershipLedger.inspect_existing_account_readonly(
            db_path=str(self.path), account_uid=481,
        ), first)
        with self.assertRaisesRegex(OwnershipConflict, "REDUCTION_DENIED"):
            self.ledger.protect_manual_asset_floor(
                account_key=self.scope, asset="FLOKI", minimum_qty=D("799"))

    def test_zero_wallet_with_recorded_nonzero_floor_is_rejected(self):
        self.set_floors("1", "0")
        with self.assertRaisesRegex(ValueError, "FLOOR_EXCEEDS_WALLET"):
            review_m06_special_partitions(
                account=account(floki="0", pepe="1000"),
                local_rebuy=self.read(),
            )

    def test_original_orchestrator_four_mock_gets_report_only_flags(self):
        self.set_floors("800", "850")
        now = int(time.time() * 1000)
        go = M06ScopedApproval(
            "M06_PRIVATE_VIEW_ONLY_RETRY_20261031",
            now-1000, now+899000, 4, "injidr",
        )
        source = {
            "/api/v2/account": {
                "uid": 481, "canTrade": False, "canWithdraw": False,
                "canWithdrawFiat": False,
                "balances": [
                    {"asset":"IDR", "free":"5000","locked":"250"},
                    {"asset":"FLOKI","free":"1000","locked":"0"},
                    {"asset":"PEPE","free":"1000","locked":"0"},
                ],
            },
            "/api/v2/openOrders": [],
            "/api/v2/order/histories": {"data": []},
            "/api/v2/myTrades": {"data": []},
        }
        visited = []
        with patch(
            "indodax_ai_bot.m06_scoped_https._read_json_over_verified_tls",
            side_effect=lambda request: (
                visited.append(request.path), source[request.path]
            )[1],
        ):
            bot = DailyScalpingOrchestrator(
                Settings(), DecisionJournal(Path(self.temp.name) / "decisions.sqlite3"),
            )
            result = bot.inspect_m06_private_readonly_once(
                approval=go,
                budget=M06RequestBudget(
                    str(Path(self.temp.name) / "budget.sqlite3")),
                api_key="fixture-key", secret_key="fixture-secret",
                local_ownership_db=str(self.path),
            )
        self.assertEqual(len(visited), 4)
        self.assertTrue(result.local_ledger_account_bound)
        self.assertTrue(result.special_pool_local_floors_complete)
        self.assertFalse(result.special_asset_ownership_verified)
        self.assertFalse(result.capital_spendable_verified)
        self.assertFalse(result.execution_allowed)


if __name__ == "__main__":
    unittest.main()
