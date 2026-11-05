"""M05 original order Telegram display contract for MANUAL + future AUTO.

Pure display unit tests. Existing owner Telegram integration tests separately
prove the same formatter reaches the dedicated original M05 poller.
"""
from decimal import Decimal as D
import unittest

from indodax_ai_bot.m07_order_adapter import M07OrderDraft
from indodax_ai_bot.m05_trade_order_card import render_m05_trade_order_card


def draft(side="BUY", *, price="25000", qty="4"):
    return M07OrderDraft(
        symbol="INJIDR", side=side, order_type="LIMIT",
        client_order_id="m05-financial-card-001",
        price=D(price), quantity=D(qty),
    )


class M05OwnerFinancialDisplayContractTests(unittest.TestCase):
    def test_buy_idr_and_expected_token_visible_in_manual_and_auto(self):
        for mode in ("MANUAL", "AUTO"):
            with self.subTest(mode=mode):
                body = render_m05_trade_order_card(draft(), mode=mode)
                self.assertIn("Nominal pembelian (estimasi): Rp100.000", body)
                self.assertIn("Token diperoleh (estimasi): 4 INJ", body)
                self.assertIn("Harga LIMIT per INJ: Rp25.000", body)
                self.assertIn(f"M05 {mode} BUY ", body)
                self.assertNotIn("fee", body.lower())
                self.assertNotIn("komisi", body.lower())
                self.assertIn("Trading OFF", body)

    def test_buy_true_total_can_include_internal_costs_without_breakdown(self):
        card = render_m05_trade_order_card(
            draft(), mode="MANUAL",
            expected_buy_total_idr=D("100200"),
        )
        self.assertIn("Nominal pembelian (estimasi): Rp100.200", card)
        self.assertIn("Token diperoleh (estimasi): 4 INJ", card)
        self.assertNotIn("fee", card.lower())
        self.assertNotIn("pajak", card.lower())
        with self.assertRaises(ValueError):
            render_m05_trade_order_card(
                draft(), mode="MANUAL",
                expected_buy_total_idr=D("99000"),
            )

    def test_sell_total_available_but_never_invents_buy_capital(self):
        for mode in ("MANUAL", "AUTO"):
            card = render_m05_trade_order_card(draft("SELL"), mode=mode)
            self.assertIn("Total penjualan (estimasi): Rp100.000", card)
            self.assertIn("Modal beli: belum ada lot BUY V2 terverifikasi", card)
            self.assertIn("Untung/rugi bersih: belum dapat dihitung", card)
            self.assertNotIn("Estimasi untung bersih: Rp", card)

    def test_sell_verified_inputs_display_actual_profit_net_without_fee_line(self):
        for mode in ("MANUAL", "AUTO"):
            card = render_m05_trade_order_card(
                draft("SELL"), mode=mode,
                allocated_bot_owned_buy_cost_idr=D("90000"),
                expected_net_sell_proceeds_idr=D("98000"),
            )
            self.assertIn("Total penjualan (estimasi): Rp98.000", card)
            self.assertIn("Modal beli dialokasikan: Rp90.000", card)
            self.assertIn("Estimasi untung bersih: Rp8.000", card)
            self.assertIn("ROI netto estimasi: 8%", card)
            self.assertIn("Target SELL netto: T1 2% | T2 8% | T3 11%", card)
            self.assertIn("Tier ROI tercapai: T2", card)
            self.assertIn(
                "Syarat profit normal (min. Rp10.000 DAN 2% netto): BELUM TERPENUHI",
                card,
            )
            self.assertNotIn("fee", card.lower())
            self.assertNotIn("komisi", card.lower())

    def test_same_actual_buy_100k_and_sell_net_98k_is_loss_not_8k_profit(self):
        # Regression: the owner reported that an unrelated 90k capital
        # was mistakenly used with this 100k BUY in a prior illustration.
        for mode in ("MANUAL", "AUTO"):
            card = render_m05_trade_order_card(
                draft("SELL"), mode=mode,
                allocated_bot_owned_buy_cost_idr=D("100000"),
                expected_net_sell_proceeds_idr=D("98000"),
            )
            self.assertIn("Modal beli dialokasikan: Rp100.000", card)
            self.assertIn("Total penjualan (estimasi): Rp98.000", card)
            self.assertIn("Estimasi rugi bersih: Rp2.000", card)
            self.assertIn("ROI netto estimasi: -2%", card)
            self.assertIn("Tier ROI tercapai: BELUM MENCAPAI T1", card)
            self.assertIn("BELUM TERPENUHI", card)
            self.assertNotIn("Estimasi untung bersih:", card)

    def test_10k_and_two_percent_both_required_and_tiers_are_net(self):
        # No strategy changes: target levels come from original
        # ScalpingPolicy: T1=2%, T2=8%, T3=11%.
        cases = (
            ("102000", "Rp2.000", "2%", "T1", False),
            ("108000", "Rp8.000", "8%", "T2", False),
            ("110000", "Rp10.000", "10%", "T2", True),
            ("111000", "Rp11.000", "11%", "T3", True),
        )
        for net, profit, roi, tier, allowed in cases:
            with self.subTest(net=net):
                # Set gross order price above the projected net proceeds.
                gross = D(net) + D("1000")
                card = render_m05_trade_order_card(
                    draft("SELL", price=str(gross / D("4"))),
                    mode="MANUAL",
                    allocated_bot_owned_buy_cost_idr=D("100000"),
                    expected_net_sell_proceeds_idr=D(net),
                )
                self.assertIn("Estimasi untung bersih: " + profit, card)
                self.assertIn("ROI netto estimasi: " + roi, card)
                self.assertIn("Tier ROI tercapai: " + tier, card)
                self.assertIn(
                    "Syarat profit normal (min. Rp10.000 DAN 2% netto): " +
                    ("TERPENUHI" if allowed else "BELUM TERPENUHI"),
                    card,
                )
                self.assertNotIn("fee", card.lower())

    def test_high_capital_needs_2_percent_even_if_10k_net(self):
        # A 1m capital position making 10k must still fail the 2% ROI gate.
        card = render_m05_trade_order_card(
            draft("SELL",price="255000",qty="4"),
            mode="AUTO",
            allocated_bot_owned_buy_cost_idr=D("1000000"),
            expected_net_sell_proceeds_idr=D("1010000"),
        )
        self.assertIn("Estimasi untung bersih: Rp10.000", card)
        self.assertIn("ROI netto estimasi: 1%", card)
        self.assertIn("Tier ROI tercapai: BELUM MENCAPAI T1", card)
        self.assertIn("BELUM TERPENUHI", card)

    def test_sell_loss_show_explicit_loss_not_positive_profit(self):
        card = render_m05_trade_order_card(
            draft("SELL"), mode="MANUAL",
            allocated_bot_owned_buy_cost_idr=D("90000"),
            expected_net_sell_proceeds_idr=D("85000"),
        )
        self.assertIn("Estimasi rugi bersih: Rp5.000", card)
        self.assertNotIn("Estimasi untung bersih:", card)

    def test_decimal_qty_and_indonesian_separators_keep_exact_value(self):
        card = render_m05_trade_order_card(
            draft(price="1070.25", qty="0.5"), mode="MANUAL",
        )
        self.assertIn("Harga LIMIT per INJ: Rp1.070,25", card)
        self.assertIn("Nominal pembelian (estimasi): Rp535,125", card)
        self.assertIn("Token diperoleh (estimasi): 0,5 INJ", card)

    def test_reject_unverified_half_sell_net_and_invalid_profit_values(self):
        for cost, net in (
            (D("90000"), None),
            (None, D("98000")),
            (D("-1"), D("90000")),
            (D("90000"), D("NaN")),
            (D("90000"), D("-3")),
            (D("90000"), D("110000")),
        ):
            with self.subTest(cost=cost, net=net):
                with self.assertRaises(ValueError):
                    render_m05_trade_order_card(
                        draft("SELL"), mode="MANUAL",
                        allocated_bot_owned_buy_cost_idr=cost,
                        expected_net_sell_proceeds_idr=net,
                    )

    def test_buy_cannot_show_sell_cost_or_pnl_and_mode_must_be_known(self):
        with self.assertRaises(ValueError):
            render_m05_trade_order_card(
                draft(), mode="MANUAL",
                allocated_bot_owned_buy_cost_idr=D("90000"),
                expected_net_sell_proceeds_idr=D("98000"),
            )
        with self.assertRaises(ValueError):
            render_m05_trade_order_card(draft(), mode="SHADOW")

    def test_quote_must_be_original_validated_limit_draft(self):
        bad = M07OrderDraft(
            symbol="INJIDR", side="SELL", order_type="MARKET",
            client_order_id="bad", quantity=D("4"),
        )
        with self.assertRaises(ValueError):
            render_m05_trade_order_card(bad, mode="MANUAL")


if __name__ == "__main__":
    unittest.main()
