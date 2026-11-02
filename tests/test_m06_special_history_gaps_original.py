"""M06 V2 offline scoped-history completeness gate, no private requests."""
from __future__ import annotations

from decimal import Decimal as D
import unittest

from indodax_ai_bot.m06_special_history_gaps import review_m06_special_history_gaps
from indodax_ai_bot.m06_special_sale_window import (
    observe_m06_special_sale_window,
)
from indodax_ai_bot.m06_private_payloads import FillRecord

FIRST = 1792774800000
WINDOWS = ((FIRST,FIRST+3600000-1),
           (FIRST+3600000,FIRST+7200000-1))
CHECKED = FIRST + 7200000 + 5000


def empty(asset):
    return observe_m06_special_sale_window(
        symbol=asset, orders=(), fills=(),
    )


def report(*, windows=WINDOWS, assets=None, now=CHECKED):
    return review_m06_special_history_gaps(
        windows=windows,
        assets=(empty("flokiidr"),empty("pepeidr")) if assets is None else assets,
        checked_at_ms=now,
    )


class M06ScopedHistoryGapsV2Tests(unittest.TestCase):
    def test_two_contiguous_windows_two_assets_never_attest_all_time(self):
        v=report()
        safe=v.safe_summary()
        self.assertEqual(safe["status"],"M06_SPECIAL_SALE_HISTORY_GAPS_UNRESOLVED")
        self.assertEqual(safe["queried_consecutive_windows"],2)
        self.assertEqual(safe["scoped_sell_gross_idr_observed"],
                         {"FLOKI":"0","PEPE":"0"})
        for k in (
            "special_sale_before_first_window_unverified",
            "special_sale_after_last_window_unverified",
            "special_initial_liability_unverified",
            "rebuy_consumption_provenance_unverified",
            "external_peer_liabilities_unverified",
        ):
            self.assertTrue(safe[k])
        self.assertFalse(safe["full_history_covered"])
        self.assertFalse(safe["balance_source_funds_verified"])
        self.assertFalse(safe["execution_allowed"])
        self.assertIsNone(safe["safe_general_idr_from_this_report"])
        self.assertNotIn("UID",repr(v))

    def test_nonzero_sell_fills_in_window_still_not_whole_history(self):
        floki=empty("flokiidr")
        pepe=empty("pepeidr")
        from dataclasses import replace
        floki=replace(floki,scoped_sell_gross_idr=D("123456"),
                      scoped_sell_fill_count=2)
        res=report(assets=(pepe,floki)).safe_summary()
        self.assertEqual(res["scoped_sell_gross_idr_observed"],
                         {"FLOKI":"123456","PEPE":"0"})
        self.assertFalse(res["full_history_covered"])
        self.assertIsNone(res["safe_general_idr_from_this_report"])

    def test_unlinked_unknown_side_is_explicitly_unresolved(self):
        orphan=FillRecord("orphan","unknown","flokiidr",D("1"),
                          D("20"),D("0"),False,None,False,None,"idr")
        floki=observe_m06_special_sale_window(
            symbol="flokiidr",orders=(),fills=(orphan,))
        out=report(assets=(floki,empty("pepeidr"))).safe_summary()
        self.assertTrue(out["observed_pair_records_unresolved"])
        self.assertEqual(out["scoped_sell_gross_idr_observed"]["FLOKI"],"0")
        self.assertFalse(out["execution_allowed"])

    def test_time_gaps_overlap_future_or_noninteger_rejected(self):
        cases=(
            ((WINDOWS[0],(WINDOWS[0][1]+2,WINDOWS[1][1])),CHECKED),
            ((WINDOWS[0],(WINDOWS[0][1],WINDOWS[1][1])),CHECKED),
            ((WINDOWS[0],(WINDOWS[1][0],CHECKED)),CHECKED),
            (((FIRST,FIRST+100),(FIRST+101,"bad")),CHECKED),
            ((),CHECKED),
            (WINDOWS,WINDOWS[-1][1]),
        )
        for windows,now in cases:
            with self.subTest(window=windows,now=now):
                with self.assertRaises((ValueError,TypeError)):
                    report(windows=windows,now=now)

    def test_missing_repeated_or_forged_asset_inputs_rejected(self):
        for assets in (
            (empty("flokiidr"),),
            (empty("flokiidr"),empty("flokiidr")),
            (),
            ("FLOKI","PEPE"),
        ):
            with self.subTest(assets=assets):
                with self.assertRaises(ValueError):
                    report(assets=assets)

    def test_window_datetime_are_iso_utc_but_no_pnl_or_capital_grant(self):
        summary=report().safe_summary()
        self.assertTrue(summary["observed_interval_utc"]["start"].endswith("Z"))
        self.assertTrue(summary["observed_interval_utc"]["end"].endswith("Z"))
        self.assertTrue(summary["checked_at_utc"].endswith("Z"))
        self.assertNotIn("profit",summary)
        self.assertNotIn("available_idr",summary)


if __name__ == "__main__":
    unittest.main()
