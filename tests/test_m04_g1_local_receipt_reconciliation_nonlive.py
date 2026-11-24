"""Offline fixtures for the original local G1 receipt reconciliation."""
from hashlib import sha256
import json
import sqlite3
import unittest

from indodax_ai_bot.m04_g1_local_receipt_reconciliation_nonlive import (
    reconcile_local_trade_windows_nonlive,
)
from indodax_ai_bot.m04_g1_15m_1h_source_inventory_nonlive import (
    inventory_g1_windows_nonlive,
)

END = 1795497840
PAIR = "anoa_idr"


class LocalReceiptReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.executescript("""
            CREATE TABLE ws_trade_publications(
                pair TEXT, epoch TEXT, offset INTEGER,
                payload_sha256 TEXT, rows_json TEXT,
                PRIMARY KEY(pair,epoch,offset));
            CREATE TABLE m03_ws3_trade_publication_times(
                pair TEXT, epoch TEXT, offset INTEGER, recorded_epoch INTEGER,
                PRIMARY KEY(pair,epoch,offset));
            CREATE TABLE m03_ws3_trade_samples(
                pair TEXT,exchange_epoch INTEGER,sequence INTEGER,
                price_idr TEXT,quote_idr TEXT,recorded_epoch INTEGER,
                PRIMARY KEY(pair,exchange_epoch,sequence));
        """)

    def publication(self, *, seq=1, exchange=END-60, receipt=END-59,
                    offset=1, epoch="123", price="100", quote="500",
                    bad_digest=False):
        payload = json.dumps([["anoaidr", exchange, seq, "buy",
                               price, quote, "5"]], separators=(",", ":"))
        digest = "0"*64 if bad_digest else sha256(payload.encode()).hexdigest()
        self.db.execute("INSERT INTO ws_trade_publications VALUES(?,?,?,?,?)",
                        (PAIR, epoch, offset, digest, payload))
        if receipt is not None:
            self.db.execute("INSERT INTO m03_ws3_trade_publication_times VALUES(?,?,?,?)",
                            (PAIR, epoch, offset, receipt))

    def index(self, *, seq=1, exchange=END-60, receipt=END-59,
              price="100", quote="500"):
        self.db.execute("INSERT INTO m03_ws3_trade_samples VALUES(?,?,?,?,?,?)",
                        (PAIR, exchange, seq, price, quote, receipt))

    def inspect(self):
        return reconcile_local_trade_windows_nonlive(
            self.db, pair=PAIR, end_epoch=END)

    def test_exact_agreement_is_local_only(self):
        self.publication()
        self.index(price="100.0")
        result = self.inspect()
        self.assertEqual(result["status"],
                         "LOCAL_PUBLICATION_INDEX_RECONCILIATION_RESEARCH_ONLY")
        for w in result["windows"].values():
            self.assertEqual(w["matched_identity_values_and_receipt_asof"], 1)
            self.assertTrue(w["nonempty_asof_local_identity_value_receipt_agreement"])
            self.assertFalse(w["complete_trade_tape_verified"])
        self.assertFalse(result["independent_upstream_source_verified"])
        self.assertFalse(result["g1_receipt_issuance_allowed"])
        self.assertFalse(result["execution_allowed"])

    def test_late_unknown_and_exclusive_exchange_end(self):
        self.publication(seq=1, receipt=END+1)
        self.index(seq=1, receipt=END+1)
        self.publication(seq=2, receipt=None, offset=2)
        self.index(seq=2, receipt=END-61)
        self.publication(seq=3, receipt=END, offset=3)
        self.index(seq=3, receipt=END)
        self.publication(seq=4, exchange=END, receipt=END, offset=4)
        self.index(seq=4, exchange=END, receipt=END)
        w = self.inspect()["windows"]["15m"]
        self.assertEqual(w["raw_unique_trade_identities"], 3)
        self.assertEqual(w["raw_received_by_cutoff"], 1)
        self.assertEqual(w["raw_receipt_after_cutoff"], 1)
        self.assertEqual(w["raw_receipt_missing_or_invalid"], 1)
        self.assertEqual(w["index_receipt_missing_or_invalid"], 1)
        self.assertEqual(w["matched_identity_values_and_receipt_asof"], 1)

    def test_cross_epoch_repeated_identity_uses_earliest_valid_receipt(self):
        self.publication(receipt=END+1)
        self.publication(epoch="456", receipt=END-58)
        self.index(receipt=END-58)
        r = self.inspect()
        self.assertEqual(r["repeated_raw_window_identities_collapsed"], 1)
        self.assertEqual(r["windows"]["1h"]["raw_unique_trade_identities"], 1)
        self.assertEqual(r["windows"]["1h"]["matched_identity_values_and_receipt_asof"], 1)

    def test_disjoint_rows_and_conflicting_values_are_visible(self):
        self.publication(seq=1, offset=1)
        self.publication(seq=2, offset=2)
        self.index(seq=2, quote="501")
        self.index(seq=3)
        self.publication(seq=4, offset=4)
        self.index(seq=4, receipt=END-58)
        w = self.inspect()["windows"]["15m"]
        self.assertEqual(w["raw_only_asof_identities"], 1)
        self.assertEqual(w["index_only_asof_identities"], 1)
        self.assertEqual(w["value_conflicts_asof"], 1)
        self.assertEqual(w["receipt_timestamp_differences_asof"], 1)
        self.assertFalse(w["nonempty_asof_local_identity_value_receipt_agreement"])

    def test_digest_and_raw_identity_conflict_block(self):
        self.publication(bad_digest=True)
        self.assertEqual(self.inspect()["status"],
                         "LOCAL_RECONCILIATION_PUBLICATION_DIGEST_MISMATCH")
        self.db.execute("DELETE FROM ws_trade_publications")
        self.db.execute("DELETE FROM m03_ws3_trade_publication_times")
        self.publication()
        self.publication(epoch="456", quote="999")
        self.assertEqual(self.inspect()["status"],
                         "LOCAL_RECONCILIATION_RAW_IDENTITY_CONFLICT")

    def test_empty_windows_do_not_imply_tape_complete(self):
        r = self.inspect()
        self.assertEqual(r["windows"]["1h"]["raw_received_by_cutoff"], 0)
        self.assertFalse(r["windows"]["1h"]["nonempty_asof_local_identity_value_receipt_agreement"])
        self.db.execute("DROP TABLE m03_ws3_trade_publication_times")
        self.assertEqual(self.inspect()["status"],
                         "LOCAL_RECONCILIATION_TABLES_UNAVAILABLE")

    def test_window_boundaries_and_existing_inventory_integration(self):
        self.publication(exchange=END-3600, receipt=END-3599)
        self.index(exchange=END-3600, receipt=END-3599)
        self.publication(seq=2, exchange=END-900, receipt=END-899, offset=2)
        self.index(seq=2, exchange=END-900, receipt=END-899)
        self.publication(seq=3, exchange=END-3601, receipt=END-3600, offset=3)
        self.index(seq=3, exchange=END-3601, receipt=END-3600)
        r = self.inspect()
        self.assertEqual(r["windows"]["15m"]["raw_received_by_cutoff"], 1)
        self.assertEqual(r["windows"]["1h"]["raw_received_by_cutoff"], 2)
        inventory = inventory_g1_windows_nonlive(
            micro_db=self.db, journal_db=self.db, pairs=[PAIR], end_epoch=END)
        self.assertEqual(inventory["results"][0][
            "local_publication_index_receipt_reconciliation"], r)
        self.assertFalse(inventory["g1_independent_issuer_verified"])
        self.assertFalse(inventory["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
