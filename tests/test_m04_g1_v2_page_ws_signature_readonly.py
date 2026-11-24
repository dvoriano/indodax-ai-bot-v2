"""Offline V2 page vs original WS signature-multiplicity research."""
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from scripts import m04_g1_v2_page_ws_signature_readonly as m

END=1795508400
PAIR="enj_idr"


def trade(t, tid, price="11", quote="55"):
    return {"time":t*1000+100,"id":tid,"price":price,"qty":"5",
            "quoteQty":quote,"isBestMatch":True}


def row(t, seq, price="11", quote="55", receipt=None):
    return (t,seq,price,quote,t+1 if receipt is None else receipt)


class SignaturePageTests(unittest.TestCase):
    def check(self,payload,rows):
        return m.compare_page(pair=PAIR,payload=payload,rows=rows,
                              requested_epoch=END+10,received_epoch=END+11)

    def test_matching_multiset_preserves_duplicate_economic_signatures(self):
        payload=[trade(END-3601,1),trade(END-60,2),
                 trade(END-60,3),trade(END,4)]
        local=[row(END-60,700),row(END-60,701,"11.0","55.00")]
        r=self.check(payload,local)
        for w in r["windows"].values():
            self.assertTrue(w["page_interior_spans_full_window"])
            self.assertEqual(w["matching_signature_multiplicity"],2)
            self.assertTrue(w["nonempty_exact_signature_multiset_agreement"])
        self.assertFalse(r["rest_id_equals_ws_sequence_verified"])
        self.assertFalse(r["independent_g1_verified"])
        self.assertFalse(r["execution_allowed"])

    def test_page_edges_and_late_rows_excluded(self):
        payload=[trade(END-120,1),trade(END-60,2),trade(END-1,3)]
        local=[row(END-120,7),row(END-60,8),row(END-1,9),
               row(END-70,10,receipt=END+1),row(END-200,11)]
        w=self.check(payload,local)["windows"]["15m"]
        self.assertEqual(w["page_interior_overlap_seconds"],118)
        self.assertEqual(w["local_asof_rows_total"],4)
        self.assertEqual(w["local_comparable_rows"],1)
        self.assertEqual(w["local_asof_rows_outside_comparable_seconds"],3)
        self.assertEqual(w["matching_signature_multiplicity"],1)
        self.assertEqual(w["local_late_rows"],1)
        self.assertFalse(w["page_interior_spans_full_window"])

    def test_disagreements_and_empty_page_are_not_complete_tape(self):
        payload=[trade(END-901,1),trade(END-60,2),trade(END-60,3),
                 trade(END-30,4,quote="99"),trade(END,5)]
        local=[row(END-60,8),row(END-30,9,quote="100")]
        w=self.check(payload,local)["windows"]["15m"]
        self.assertEqual(w["matching_signature_multiplicity"],1)
        self.assertEqual(w["rest_only_signature_multiplicity"],2)
        self.assertEqual(w["ws_only_signature_multiplicity"],1)
        self.assertFalse(w["nonempty_exact_signature_multiset_agreement"])
        w=self.check([],local)["windows"]["1h"]
        self.assertEqual(w["local_asof_rows_outside_comparable_seconds"],2)
        self.assertEqual(w["page_interior_overlap_seconds"],0)
        self.assertFalse(w["nonempty_exact_signature_multiset_agreement"])

    def test_duplicate_id_and_local_identity_rejected(self):
        with self.assertRaises(ValueError):
            self.check([trade(END-60,1),trade(END-59,1)],[])
        with self.assertRaises(ValueError):
            self.check([],[row(END-60,1),row(END-60,1)])

    def test_existing_admitted_method_called_once_and_db_unchanged(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/"decisions.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute("""CREATE TABLE m03_ws3_trade_samples(
                    pair TEXT,exchange_epoch INTEGER,sequence INTEGER,
                    price_idr TEXT,quote_idr TEXT,recorded_epoch INTEGER)""")
                db.execute("INSERT INTO m03_ws3_trade_samples VALUES(?,?,?,?,?,?)",
                           (PAIR,*row(END-60,700)))
            before=path.read_bytes()
            market=Mock()
            market.recent_trades_sample.return_value=[
                trade(END-3601,1),trade(END-60,2),trade(END,3)]
            factory=Mock(return_value=market)
            with patch.dict(os.environ,m._LOCKS,clear=True):
                r=m.run(pair=PAIR,source_db=path,market_factory=factory,
                        clock=lambda:END+10)
            market.recent_trades_sample.assert_called_once_with(PAIR)
            self.assertEqual(r["status"],"V2_PAGE_WS_SIGNATURE_RESEARCH_ONLY")
            self.assertEqual(r["recent_trade_method_calls"],1)
            self.assertEqual(path.read_bytes(),before)
            market.reset_mock()
            market.recent_trades_sample.side_effect=m.PublicDataError(
                "PUBLIC_REST_HTTP_403_FORBIDDEN")
            with patch.dict(os.environ,m._LOCKS,clear=True):
                blocked=m.run(pair=PAIR,source_db=path,market_factory=factory,
                              clock=lambda:END+10)
            market.recent_trades_sample.assert_called_once_with(PAIR)
            self.assertEqual(blocked["reason"],"PUBLIC_REST_HTTP_403_FORBIDDEN")
            self.assertFalse(blocked["execution_allowed"])

    def test_guard_and_invalid_source_never_construct_transport(self):
        factory=Mock(side_effect=AssertionError("no transport"))
        with patch.dict(os.environ,{},clear=True):
            r=m.run(pair=PAIR,market_factory=factory)
        self.assertEqual(r["recent_trade_method_calls"],0)
        with patch.dict(os.environ,m._LOCKS,clear=True):
            r=m.run(pair=PAIR,source_db=Path("/nonexistent"),
                    market_factory=factory,clock=lambda:END+10)
        self.assertEqual(r["recent_trade_method_calls"],0)
        factory.assert_not_called()


if __name__=="__main__":
    unittest.main()
