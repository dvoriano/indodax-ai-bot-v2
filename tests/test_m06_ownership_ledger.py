"""M06 ownership persistence, same ID replay and cross-account denial."""
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from indodax_ai_bot.m06_ownership_ledger import M06OwnershipLedger, OwnershipConflict
from indodax_ai_bot.m06_private_payloads import OrderRecord, FillRecord


class OwnershipLedgerTests(unittest.TestCase):
    def test_restart_replay_and_account_isolation(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"m06.sqlite3"
            store=M06OwnershipLedger(path)
            store.record_local_intent(account_key="account-a",order_id="o1",
                client_id="v2-local-1",symbol="btcidr",side="BUY")
            order=OrderRecord("o1","btcidr","BUY","FILLED",
                               Decimal("2"),Decimal("1"),"v2-local-1")
            fill=FillRecord("t1","o1","btcidr",Decimal("1"),
                            Decimal("100"),Decimal("1"))
            first=store.observe(account_key="account-a",order=order,fills=(fill,))
            self.assertTrue(first.account_match)
            self.assertFalse(first.bot_owned_verified)
            self.assertFalse(first.exchange_fills_attested)
            second=M06OwnershipLedger(path).observe(
                account_key="account-a",order=order,fills=(fill,))
            self.assertEqual(first,second)
            with self.assertRaisesRegex(OwnershipConflict,"UNATTRIBUTED"):
                store.observe(account_key="account-b",order=order,fills=(fill,))
            with self.assertRaisesRegex(OwnershipConflict,"FILL_REPLAY_CONFLICT"):
                store.observe(account_key="account-a",order=order,fills=(
                    FillRecord("t1","o1","btcidr",Decimal("2"),
                               Decimal("100"),Decimal("1")),))

    def test_foreign_fill_and_order_identity_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            store=M06OwnershipLedger(Path(td)/"m06.sqlite3")
            store.record_local_intent(account_key="a",order_id="o",
                client_id="v2-1",symbol="btcidr",side="BUY")
            with self.assertRaisesRegex(OwnershipConflict,"REPLAY_CONFLICT"):
                store.record_local_intent(account_key="a",order_id="o",
                    client_id="v2-2",symbol="btcidr",side="BUY")
            order=OrderRecord("o","btcidr","BUY","NEW",
                              Decimal("1"),Decimal("0"),"v2-1")
            foreign=FillRecord("t","elsewhere","btcidr",
                               Decimal("1"),Decimal("100"),Decimal("0"))
            with self.assertRaisesRegex(OwnershipConflict,"FOREIGN_FILL"):
                store.observe(account_key="a",order=order,fills=(foreign,))
            mismatched=OrderRecord("o","btcidr","BUY","NEW",
                                   Decimal("1"),Decimal("0"),"foreign")
            self.assertFalse(store.observe(
                account_key="a",order=mismatched,fills=()).account_match)


if __name__=="__main__":
    unittest.main()
