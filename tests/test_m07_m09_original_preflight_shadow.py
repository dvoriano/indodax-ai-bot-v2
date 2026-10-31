"""M07 preflight / M09 shadow: no unverified source can become an order."""
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from indodax_ai_bot.config import Settings
from indodax_ai_bot.execution_preflight import ExecutionPreflight
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.position_manager import PositionManager


class M07OriginalPreflightAndM09PaperSeparationTests(unittest.TestCase):
    def setUp(self):
        self.preflight = ExecutionPreflight()

    def profitable_hypothesis(self):
        return {
            "pair": "inj_idr", "now_epoch": 1800000000,
            "source_expires_epoch": 1800000060,
            "risk_allow": True, "owner_pass": True,
            "issuer_authenticated": True,
            "wallet_authenticated": True,
            "order_identity_attested": True,
            "tradability_verified": True,
            "fees_verified": True, "quantity_verified": True,
            "buy_notional_idr": D("100000"),
            "projected_sell_notional_idr": D("115000"),
            "buy_all_in_fee_idr": D("250"),
            "sell_all_in_fee_idr": D("250"),
            "spread_slippage_idr": D("500"),
            "spendable_idr": D("100250"),
        }

    def test_negative_real_source_veto_keeps_original_order_hardlock(self):
        r = self.preflight.assess_buy()
        self.assertEqual(r.status, "REJECT")
        self.assertEqual(r.reason, "M07_RISK_VETO")
        self.assertTrue(r.risk_veto)
        self.assertFalse(r.execution_allowed)
        self.assertFalse(r.exchange_order_request_created)

    def test_even_profitable_caller_claims_cannot_authorize_exchange(self):
        r = self.preflight.assess_buy(**self.profitable_hypothesis())
        self.assertEqual(r.reason, "M07_EXCHANGE_BOUND_ORDER_ADAPTER_NOT_CONNECTED")
        self.assertGreater(r.estimated_net_profit_idr, D("10000"))
        self.assertGreater(r.estimated_net_roi_percent, D("2"))
        self.assertFalse(r.execution_allowed)
        self.assertFalse(r.exchange_order_request_created)

    def test_risk_owner_source_wallet_fee_and_ttl_negative_gates(self):
        cases = (
            ("risk_allow", False, "M07_RISK_VETO"),
            ("issuer_authenticated", False, "M07_SOURCE_ISSUER_NOT_VERIFIED"),
            ("owner_pass", False, "M07_OWNER_PASS_NOT_VERIFIED"),
            ("wallet_authenticated", False, "M07_ACCOUNT_NOT_VERIFIED"),
            ("order_identity_attested", False, "M07_ORDER_IDENTITY_NOT_ATTESTED"),
            ("fees_verified", False, "M07_QUOTE_PRECISION_OR_FEE_UNVERIFIED"),
            ("source_expires_epoch", 1800000000, "M07_PAIR_OR_SOURCE_TTL_INVALID"),
            ("spendable_idr", D("100249"), "M07_INSUFFICIENT_FREE_IDR"),
            ("buy_notional_idr", D("NaN"), "M07_UNVERIFIED_PRICE_FEE_OR_BALANCE"),
            ("quantity_verified", True, "M07_EXCHANGE_BOUND_ORDER_ADAPTER_NOT_CONNECTED"),
        )
        for key, value, reason in cases:
            with self.subTest(key=key, value=str(value)):
                data = self.profitable_hypothesis()
                data[key] = value
                r = self.preflight.assess_buy(**data)
                self.assertEqual(r.reason, reason)
                self.assertFalse(r.execution_allowed)

    def test_incomplete_net_profit_and_slippage_do_not_qualify(self):
        data = self.profitable_hypothesis()
        data["projected_sell_notional_idr"] = D("110000")
        r = self.preflight.assess_buy(**data)
        self.assertEqual(r.reason, "M07_NET_PROFIT_GATE_FAILED")
        self.assertFalse(r.execution_allowed)

    def test_original_v2_orchestrator_wires_m07_and_m08_both_reject(self):
        with TemporaryDirectory() as td:
            app = DailyScalpingOrchestrator(
                Settings.from_env(), DecisionJournal(Path(td) / "decisions.sqlite3")
            )
            result = app.run_cycle("m09-on-original-v2")
            self.assertEqual(result.preflight_review["reason"], "M07_RISK_VETO")
            self.assertEqual(result.position_review["reason"],
                             "M08_EXCHANGE_OWNED_BUY_FILLS_NOT_CONNECTED")
            self.assertFalse(result.preflight_review["exchange_order_request_created"])
            self.assertFalse(result.preflight_review["execution_allowed"])
            self.assertFalse(result.position_review["realized"])
            self.assertFalse(result.execution_allowed)

    def test_paper_quote_is_not_real_wallet_settlement(self):
        paper = PositionManager().review_unverified_quote(
            actual_buy_fill_epoch=1000, observed_epoch=1010,
            filled_buy_quantity=D("1"), filled_buy_notional_idr=D("100000"),
            allocated_buy_fees_idr=D("250"), quantity_to_sell=D("1"),
            expected_sell_notional_idr=D("120000"),
            estimated_sell_fees_idr=D("300"),
            incremental_slippage_idr=D("200"),
        )
        self.assertEqual(paper.highest_net_tier, "T3")
        self.assertEqual(paper.reason, "M08_UNVERIFIED_LOT_AND_SETTLEMENT")
        self.assertFalse(paper.realized)
        self.assertFalse(paper.execution_allowed)
        self.assertFalse(paper.bot_ownership_verified)



    def test_m06_budget_review_cannot_be_promoted_to_exchange_authority(self):
        from indodax_ai_bot.policy import ScalpingPolicy, MainCapitalReview
        review = ScalpingPolicy().review_main_capital(
            free_idr=D("150000"), locked_idr=D("70000"),
            floki_rebuy_idr=D("20000"), pepe_rebuy_idr=D("10000"),
            commitments_not_locked_idr=D("10000"))
        self.assertEqual(review.spendable_idr, D("110000"))
        data = self.profitable_hypothesis()
        data["spendable_idr"] = review.spendable_idr
        data["capital_review"] = review
        response = self.preflight.assess_buy(**data)
        self.assertEqual(response.reason, "M07_EXCHANGE_BOUND_ORDER_ADAPTER_NOT_CONNECTED")
        self.assertFalse(response.execution_allowed)
        data["spendable_idr"] = D("150000")
        mismatch = self.preflight.assess_buy(**data)
        self.assertEqual(mismatch.reason, "M07_CAPITAL_SPENDABLE_MISMATCH")
        forged = MainCapitalReview(
            free_idr=review.free_idr,
            locked_idr_observed=review.locked_idr_observed,
            reserved_rebuy_idr=review.reserved_rebuy_idr,
            commitments_not_locked_idr=review.commitments_not_locked_idr,
            spendable_idr=review.spendable_idr,
            overcommitted=False,
            source_authenticated=True,
        )
        data["spendable_idr"] = review.spendable_idr
        data["capital_review"] = forged
        self.assertEqual(
            self.preflight.assess_buy(**data).reason,
            "M07_CAPITAL_REVIEW_AUTHORITY_INVALID")
        self.assertFalse(self.preflight.assess_buy(**data).execution_allowed)

    def test_m06_capital_budget_cannot_approve_overspend(self):
        from indodax_ai_bot.policy import ScalpingPolicy
        review = ScalpingPolicy().review_main_capital(
            free_idr=D("150000"), locked_idr=D("0"),
            floki_rebuy_idr=D("30000"), pepe_rebuy_idr=D("30000"),
            commitments_not_locked_idr=D("10000"))
        data = self.profitable_hypothesis()
        data["spendable_idr"] = review.spendable_idr
        data["capital_review"] = review
        out = self.preflight.assess_buy(**data)
        self.assertEqual(out.reason, "M07_INSUFFICIENT_FREE_IDR")
        self.assertFalse(out.exchange_order_request_created)


if __name__ == "__main__":
    unittest.main()
