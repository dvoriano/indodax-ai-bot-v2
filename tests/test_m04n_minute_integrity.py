"""M04-N focused digest-integrity regressions.

The minute-filtering JSON1 query must not hide a publication whose stored
payload was modified before it was selected. These are LOCAL integrity guards
only, not independent authentication of market origin or complete trade tape.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from test_m04h_closed_minute_join import MINUTE, prepared_journal


class M04NMinuteIntegrityTests(unittest.TestCase):
    def test_modified_target_timestamp_cannot_escape_minute_digest_check(self):
        with TemporaryDirectory() as td:
            db=Path(td)/"public.sqlite3"
            journal=prepared_journal(db)
            with sqlite3.connect(db) as con:
                raw=con.execute(
                    "SELECT rows_json FROM ws_trade_publications "
                    "WHERE pair='btc_idr' AND offset=101"
                ).fetchone()[0]
                modified=json.loads(raw)
                modified[0][1]=MINUTE-240
                con.execute(
                    "UPDATE ws_trade_publications SET rows_json=? "
                    "WHERE pair='btc_idr' AND offset=101",
                    (json.dumps(modified),),
                )
            report=journal.closed_minute_sample(
                "btc_idr",closed_1m_epoch=MINUTE,now_epoch=MINUTE+90,
            )
            self.assertEqual(report["status"],"REJECT")
            self.assertEqual(report["reason"],"REJECT_WS_JOURNAL_DIGEST_MISMATCH")
            self.assertFalse(report["minute_trade_coverage_complete"])
            self.assertFalse(report["g1_receipt_issuance_allowed"])
            self.assertFalse(report["g2_provider_call_allowed"])
            self.assertFalse(report["execution_allowed"])

    def test_damaged_outside_target_minute_does_not_silently_pass(self):
        with TemporaryDirectory() as td:
            db=Path(td)/"public.sqlite3"
            journal=prepared_journal(db)
            with sqlite3.connect(db) as con:
                con.execute(
                    "UPDATE ws_trade_publications SET rows_json='[]' "
                    "WHERE pair='btc_idr' AND offset=103"
                )
            report=journal.closed_minute_sample(
                "btc_idr",closed_1m_epoch=MINUTE,now_epoch=MINUTE+90,
            )
            self.assertEqual(report["status"],"REJECT")
            self.assertEqual(report["reason"],"REJECT_WS_JOURNAL_DIGEST_MISMATCH")
            self.assertFalse(report["execution_allowed"])

    def test_clean_minute_reports_epoch_digests_checked_no_auth_upgrade(self):
        with TemporaryDirectory() as td:
            db=Path(td)/"public.sqlite3"
            journal=prepared_journal(db)
            report=journal.closed_minute_sample(
                "btc_idr",closed_1m_epoch=MINUTE,now_epoch=MINUTE+90,
            )
            self.assertEqual(report["status"],"PUBLIC_SAMPLE_OBSERVED")
            self.assertEqual(report["epoch_publication_digests_checked"],3)
            self.assertEqual(report["sampled_publication_count_total_epoch"],3)
            self.assertFalse(report["minute_trade_coverage_complete"])
            self.assertFalse(report["independent_indodax_origin_verified"])
            self.assertFalse(report["g1_receipt_issuance_allowed"])
            self.assertFalse(report["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
