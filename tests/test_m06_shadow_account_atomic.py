"""M06 offline shadow capital regression: no private network and no execution."""
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from indodax_ai_bot.m06_shadow_account import (
    AccountObservation, M06ShadowCapital,
)


def observation(**kw):
    data = dict(observed_epoch=1000, free_idr=Decimal("100000"),
                locked_idr=Decimal("20000"),
                floki_rebuy_idr=Decimal("10000"),
                pepe_rebuy_idr=Decimal("5000"),
                other_unlocked_commitments_idr=Decimal("15000"),
                account_key="owner-account-shadow")
    data.update(kw)
    return AccountObservation(**data)


class M06ShadowAccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "journal.sqlite3"
        self.store = M06ShadowCapital(self.db)

    def test_atomic_budget_and_restart(self):
        o = observation()
        first = self.store.reserve_shadow(observation=o, trace_id="a",
                         requested_idr=Decimal("40000"), now_epoch=1001)
        self.assertEqual(first.remaining_idr, Decimal("30000"))
        self.assertFalse(first.execution_allowed)
        again = M06ShadowCapital(self.db).reserve_shadow(
            observation=o, trace_id="a",
            requested_idr=Decimal("40000"), now_epoch=1001)
        self.assertTrue(again.replayed)
        self.assertEqual(again.remaining_idr, Decimal("30000"))
        with self.assertRaisesRegex(ValueError, "INSUFFICIENT"):
            self.store.reserve_shadow(observation=o, trace_id="b",
                requested_idr=Decimal("30001"), now_epoch=1001)
        second = self.store.reserve_shadow(observation=o, trace_id="b",
                         requested_idr=Decimal("30000"), now_epoch=1001)
        self.assertEqual(second.remaining_idr, Decimal("0"))

    def test_replay_conflict_and_release(self):
        o = observation()
        self.store.reserve_shadow(observation=o, trace_id="a",
                                  requested_idr=Decimal("1"), now_epoch=1000)
        with self.assertRaisesRegex(ValueError, "REPLAY_CONFLICT"):
            self.store.reserve_shadow(observation=o, trace_id="a",
                                      requested_idr=Decimal("2"), now_epoch=1000)
        self.store.release_shadow(trace_id="a")
        with self.assertRaisesRegex(ValueError, "RELEASE_UNKNOWN_OR_REPLAY"):
            self.store.release_shadow(trace_id="a")

    def test_stale_account_and_forged_authority_denied(self):
        with self.assertRaisesRegex(ValueError, "STALE"):
            self.store.reserve_shadow(observation=observation(), trace_id="a",
                 requested_idr=Decimal("1"), now_epoch=1016)
        with self.assertRaisesRegex(ValueError, "UNTRUSTED_AUTH"):
            self.store.reserve_shadow(
                observation=observation(exchange_authenticated=True), trace_id="a",
                requested_idr=Decimal("1"), now_epoch=1000)

    def test_locked_not_subtracted_twice_and_negative_denied(self):
        review = self.store.reserve_shadow(
            observation=observation(locked_idr=Decimal("90000")),
            trace_id="a", requested_idr=Decimal("70000"), now_epoch=1000)
        self.assertEqual(review.remaining_idr, Decimal("0"))
        with self.assertRaisesRegex(ValueError, "INVALID_AMOUNT"):
            self.store.reserve_shadow(
                observation=observation(free_idr=Decimal("-1")),
                trace_id="bad", requested_idr=Decimal("1"), now_epoch=1000)


if __name__ == "__main__":
    unittest.main()
