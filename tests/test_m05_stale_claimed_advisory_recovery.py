"""Original M05 TTL recovery: stale CLAIMED stays uncertain; fresh cards resume.

Only temporary SQLite. No token, Bot API, exchange, AI, order or systemd.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from indodax_ai_bot.owner_control import OwnerDecisionLedger


OWNER = 45912007
BASE = 1794000000  # exact closed 1-minute boundary


class StaleClaimAdvisoryRecoveryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="m05-stale-claimed-")
        self.addCleanup(folder.cleanup)
        self.db_path = Path(folder.name) / "owner.sqlite3"
        self.ledger = OwnerDecisionLedger(self.db_path, owner_user_id=OWNER)
        self.db_path.chmod(0o600)

    def issue(self, pair, closed=BASE, now=BASE + 10):
        row = self.ledger.issue_market_advisory_proposal(
            pair=pair, digest="a" * 64,
            closed_1m_epoch=closed, now_epoch=now,
            source_expires_epoch=closed + 90,
            setup="BREAKOUT_CONTINUATION_WATCH",
            market_status="M04_PUBLIC_UNTRUSTED",
            risk_status="UNVERIFIED_ACCOUNT_AND_G1",
        )
        self.assertTrue(row["accepted"], row)
        return row["proposal_id"]

    def rows(self):
        with sqlite3.connect(self.db_path.as_uri() + "?mode=ro",
                             uri=True) as db:
            db.execute("PRAGMA query_only=ON")
            return {
                p: (state, sid, proposal_state, reason)
                for p, state, sid, proposal_state, reason in db.execute(
                    """SELECT o.proposal_id,o.delivery_state,o.sent_message_id,
                              p.state,p.reason
                       FROM m05_market_advisory_outbox o
                       JOIN m05_proposals p ON p.proposal_id=o.proposal_id"""
                )
            }

    def test_expired_claim_preserved_old_queue_expired_fresh_claim_admitted(self):
        old = self.issue("btc_idr")
        self.assertEqual(
            self.ledger.claim_market_advisory_delivery(now_epoch=BASE+10)[
                "proposal_id"], old
        )
        queued = self.issue("eth_idr")
        with self.assertRaisesRegex(
            ValueError, "M05_ADVISORY_INCOMPLETE_DELIVERY_REVIEW"
        ):
            self.ledger.claim_market_advisory_delivery(now_epoch=BASE + 20)
        self.assertEqual(self.rows()[old][0], "CLAIMED")
        self.assertEqual(self.rows()[queued][0], "QUEUED")

        # One old unresolved claim does NOT keep old QUEUED cards pending.
        self.assertIsNone(
            self.ledger.claim_market_advisory_delivery(now_epoch=BASE+130)
        )
        recorded = self.rows()
        self.assertEqual(recorded[old][:2], ("CLAIMED", None))
        self.assertEqual(recorded[old][2:], (
            "REJECT", "EXPIRED_UNCERTAIN_DELIVERY"))
        self.assertEqual(recorded[queued][0], "EXPIRED")
        self.assertEqual(recorded[queued][2:], (
            "REJECT", "EXPIRED_UNSENT"))

        fresh = self.issue(
            "link_idr", closed=BASE+120, now=BASE+130
        )
        new_claim = self.ledger.claim_market_advisory_delivery(
            now_epoch=BASE+130
        )
        self.assertEqual(new_claim["proposal_id"], fresh)
        self.assertNotEqual(new_claim["proposal_id"], old)
        self.assertEqual(self.rows()[old][0], "CLAIMED")
        self.assertEqual(self.rows()[fresh][0], "CLAIMED")
        # Current unexpired CLAIMED must continue preventing replay.
        with self.assertRaisesRegex(
            ValueError, "M05_ADVISORY_INCOMPLETE_DELIVERY_REVIEW"
        ):
            self.ledger.claim_market_advisory_delivery(now_epoch=BASE+131)

    def test_expired_sent_remains_sent_and_unchanged(self):
        sent = self.issue("xrp_idr")
        claim = self.ledger.claim_market_advisory_delivery(
            now_epoch=BASE+10
        )
        self.assertEqual(claim["proposal_id"], sent)
        self.ledger.confirm_market_advisory_delivery(
            proposal_id=sent, message_id=501
        )
        before = self.rows()[sent]
        self.assertEqual(before, ("SENT", 501, "PENDING", None))
        self.assertIsNone(
            self.ledger.claim_market_advisory_delivery(now_epoch=BASE+130)
        )
        self.assertEqual(self.rows()[sent], before)

    def test_expired_claim_cannot_be_resent_even_when_new_card_exists(self):
        old = self.issue("btc_idr")
        self.ledger.claim_market_advisory_delivery(now_epoch=BASE+10)
        fresh = self.issue(
            "eth_idr", closed=BASE+120, now=BASE+130
        )
        claimed = self.ledger.claim_market_advisory_delivery(
            now_epoch=BASE+130
        )
        self.assertEqual(claimed["proposal_id"], fresh)
        self.assertEqual(self.rows()[old], (
            "CLAIMED", None, "REJECT", "EXPIRED_UNCERTAIN_DELIVERY"))
        self.assertEqual(self.rows()[fresh][0], "CLAIMED")


if __name__ == "__main__":
    unittest.main()
