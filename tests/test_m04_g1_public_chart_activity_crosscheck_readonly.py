"""Offline public chart versus local activity tests; no real network."""
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from scripts import m04_g1_public_chart_activity_crosscheck_readonly as m

END=1795508400
PAIR="enj_idr"


def bar(stamp, volume="1", low="10", high="12"):
    return {"Time":stamp,"Open":"11","High":high,"Low":low,
            "Close":"11","Volume":volume}


class ChartActivityTests(unittest.TestCase):
    def check(self,payload,rows):
        return m.compare_activity(payload=payload,rows=rows,
                                  pair=PAIR,end=END,now=END+30)

    def test_activity_discrepancies_missing_minutes_and_late_rows(self):
        r=self.check(
            [bar(END-180),bar(END-120,"0"),bar(END-60),
             bar(END-900),bar(END-3600),bar(END)],
            [(END-119,1,"11",END-118),
             (END-59,2,"13",END-58),
             (END-179,3,"11",END+1)])
        w=r["windows"]["15m"]
        self.assertEqual(w["chart_minutes_available"],4)
        self.assertEqual(w["chart_minutes_missing_unknown"],11)
        self.assertEqual(w["positive_chart_volume_without_local_asof_trade_minutes"],2)
        self.assertEqual(w["zero_chart_volume_with_local_asof_trade_minutes"],1)
        self.assertEqual(w["local_prices_outside_chart_range_minutes"],1)
        self.assertEqual(w["local_late_trade_rows"],1)
        self.assertEqual(w["local_asof_trade_rows"],2)
        self.assertEqual(r["windows"]["1h"]["chart_minutes_available"],5)
        self.assertFalse(r["complete_trade_tape_verified"])
        self.assertFalse(r["independent_g1_verified"])
        self.assertFalse(r["execution_allowed"])

    def test_all_matching_chart_still_not_tape(self):
        r=self.check([bar(END-60)],[(END-59,1,"11",END)])
        w=r["windows"]["1h"]
        self.assertEqual(w["positive_chart_volume_without_local_asof_trade_minutes"],0)
        self.assertEqual(w["chart_minutes_missing_unknown"],59)
        self.assertFalse(r["chart_volume_unit_equivalence_verified"])
        self.assertFalse(r["chart_asof_historical_cutoff_verified"])

    def test_invalid_bar_or_duplicate_trade_blocks(self):
        for payload,rows in (
            ([bar(END-60),bar(END-60)],[]),
            ([bar(END-60,low="13")],[]),
            ([],[(END-59,1,"11",END-60)]),
            ([],[(END-59,1,"11",END),(END-59,1,"11",END)]),
        ):
            with self.subTest(payload=payload,rows=rows):
                with self.assertRaises(ValueError):
                    self.check(payload,rows)

    def test_readonly_actual_index_and_single_opt_in_fetch(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/"decisions.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute("""CREATE TABLE m03_ws3_trade_samples(
                    pair TEXT,exchange_epoch INTEGER,sequence INTEGER,
                    price_idr TEXT,recorded_epoch INTEGER)""")
                db.execute("INSERT INTO m03_ws3_trade_samples VALUES(?,?,?,?,?)",
                           (PAIR,END-59,1,"11",END))
            before=path.read_bytes()
            fetch=Mock(return_value=[bar(END-60)])
            with patch.dict(os.environ,m._LOCKS,clear=True):
                r=m.run(source_db=path,pair=PAIR,end=END,now=END+30,fetch=fetch)
            fetch.assert_called_once_with(PAIR,from_epoch=END-3600,to_epoch=END)
            self.assertEqual(r["status"],"PUBLIC_CHART_LOCAL_ACTIVITY_RESEARCH_ONLY")
            self.assertEqual(r["public_chart_gets_attempted"],1)
            self.assertEqual(r["windows"]["1h"]["local_asof_trade_rows"],1)
            self.assertEqual(path.read_bytes(),before)

    def test_missing_locks_and_invalid_source_never_call_fetch(self):
        fetch=Mock(side_effect=AssertionError("no network"))
        with patch.dict(os.environ,{},clear=True):
            r=m.run(source_db=Path("/nonexistent"),pair=PAIR,end=END,
                    now=END+30,fetch=fetch)
        self.assertEqual(r["public_chart_gets_attempted"],0)
        with patch.dict(os.environ,m._LOCKS,clear=True):
            r=m.run(source_db=Path("/nonexistent"),pair=PAIR,end=END,
                    now=END+30,fetch=fetch)
        self.assertEqual(r["public_chart_gets_attempted"],0)
        fetch.assert_not_called()


if __name__=="__main__":
    unittest.main()
