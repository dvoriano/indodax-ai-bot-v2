"""M03 scheduled original CLI -> original M05 owner ledger opt-in boundary.

Synthetic market transport ONLY. Existing 0600 DB and explicit queue GO must
both be present; no Telegram, paid AI, trading, new owner ledger or HTTP.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from test_m05_market_advisory_mainpath import public_cohort

OWNER = 5678001
NOW = 1800000010
CLOSED = 1800000000


class M03ScheduledOwnerQueueTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.ws = self.dir / "existing-ws.sqlite3"
        self.ws.write_bytes(b"NO_LIVE_WS_NEEDED_BY_SYNTHETIC_TEST")
        self.owner = self.dir / "original-owner.sqlite3"
        self.decisions = self.dir / "main.sqlite3"
        self.ledger = OwnerDecisionLedger(self.owner, owner_user_id=OWNER)
        self.owner.chmod(0o600)
        self.market = public_cohort(closed_epoch=CLOSED)
        self.market["screening_observed_epoch"] = NOW

    def argv(self, *extra):
        return [
            "--scheduled-public-scanner-once",
            "--ws-db", str(self.ws),
            "--db", str(self.decisions),
            "--bulk-min-volume-idr", "150000000",
            "--bulk-deep-limit", "20",
            *extra,
        ]

    def invoke(self, args, *, go=False):
        out, err = StringIO(), StringIO()
        old = dict(os.environ)
        try:
            os.environ.clear()
            os.environ.update({
                "INDODAX_TRADING_ENABLED": "NO",
                "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
                "INDODAX_EXECUTION_MODE": "DRY_RUN",
            })
            if go:
                os.environ["M05_MARKET_ADVISORY_QUEUE_GO"] = "YES"
            with (
                patch("indodax_ai_bot.__main__.time.time", return_value=NOW),
                patch.object(DailyScalpingOrchestrator,
                             "_scan_with_early_momentum",
                             return_value=object()),
                patch("indodax_ai_bot.orchestrator.apply_deep_volume_floor",
                      return_value=object()),
                patch("indodax_ai_bot.orchestrator.bulk_public_deep_scan",
                      return_value=self.market),
                redirect_stdout(out),
                redirect_stderr(err),
            ):
                rc = main(args)
        finally:
            os.environ.clear()
            os.environ.update(old)
        return rc, json.loads(out.getvalue()) if out.getvalue() else None, err.getvalue()

    def test_scheduled_original_cli_queues_and_does_not_send(self):
        rc, result, stderr = self.invoke(self.argv(
            "--m05-shadow-db", str(self.owner),
            "--m05-shadow-owner-id", str(OWNER),
            "--m05-market-advisory-queue",
        ), go=True)
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(result["scheduled_m03_lane"], "DEEP20_EVERY_FIVE_MINUTES")
        self.assertEqual(result["owner_market_advisory"]["queued"], 1)
        self.assertEqual(result["owner_market_advisory"]["status"],
                         "M05_OWNER_ADVISORY_QUEUED")
        self.assertIsNone(result["final_decision"])
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["paid_network_allowed"])
        self.assertNotIn("m5:", json.dumps(result))
        self.assertEqual(
            self.ledger.status(user_id=OWNER, chat_id=OWNER)["pending_shadow_proposals"],
            1,
        )

    def test_missing_separate_go_does_not_create_proposal_or_touch_scanner(self):
        args = self.argv(
            "--m05-shadow-db", str(self.owner),
            "--m05-shadow-owner-id", str(OWNER),
            "--m05-market-advisory-queue",
        )
        with patch.object(DailyScalpingOrchestrator, "run_cycle") as scanned:
            rc, data, err = self.invoke(args, go=False)
        self.assertEqual(rc, 2)
        self.assertIsNone(data)
        self.assertIn("M05_QUEUE_SEPARATE_GO_REQUIRED", err)
        scanned.assert_not_called()
        self.assertEqual(
            self.ledger.status(user_id=OWNER, chat_id=OWNER)["pending_shadow_proposals"],
            0,
        )

    def test_scheduled_db_requires_explicit_queue_flag(self):
        rc, data, err = self.invoke(self.argv(
            "--m05-shadow-db", str(self.owner),
            "--m05-shadow-owner-id", str(OWNER),
        ), go=True)
        self.assertEqual(rc, 2)
        self.assertIsNone(data)
        self.assertIn("M05_SCHEDULED_QUEUE_OPTIN_REQUIRED", err)

    def test_bad_owner_or_unsafe_permissions_rejected_before_scan(self):
        for owner_id, mode, reason in (
            (OWNER + 1, 0o600, "M05_QUEUE_OWNER_ID_MISMATCH"),
            (OWNER, 0o644, "M05_TICK_UNSAFE_PRIVATE_FILE"),
        ):
            with self.subTest(reason=reason):
                self.owner.chmod(mode)
                args = self.argv(
                    "--m05-shadow-db", str(self.owner),
                    "--m05-shadow-owner-id", str(owner_id),
                    "--m05-market-advisory-queue",
                )
                with patch.object(DailyScalpingOrchestrator, "run_cycle") as scanned:
                    rc, data, err = self.invoke(args, go=True)
                self.assertEqual(rc, 2)
                self.assertIsNone(data)
                self.assertIn(reason, err)
                scanned.assert_not_called()

    def test_dropin_is_optin_and_preserves_original_safety(self):
        root = Path(__file__).resolve().parents[1]
        original = (root / "deploy/indodax-v2-m03-mainpath.service").read_text()
        dropin = (root / "deploy/indodax-v2-m03-owner-advisory.conf").read_text()
        self.assertNotIn("--m05-market-advisory-queue", original)
        self.assertIn("ExecStart=", dropin)
        self.assertIn("--scheduled-public-scanner-once", dropin)
        self.assertIn("--bulk-deep-limit 20", dropin)
        self.assertIn("--bulk-min-volume-idr 150000000", dropin)
        self.assertIn("--m05-market-advisory-queue", dropin)
        self.assertIn("${M05_TELEGRAM_OWNER_DB}", dropin)
        self.assertIn("${M05_TELEGRAM_OWNER_ID}", dropin)
        self.assertIn("/usr/bin/flock -n ", dropin)
        self.assertIn("INDODAX_TRADING_ENABLED=NO", dropin)
        self.assertIn("INDODAX_ALLOW_LIVE_EXECUTION=NO", dropin)
        self.assertIn("INDODAX_EXECUTION_MODE=DRY_RUN", dropin)
        self.assertNotIn("systemctl enable", dropin)
        self.assertNotIn("M05_TELEGRAM_BOT_TOKEN=", dropin)

    def test_nonexistent_owner_db_is_never_created(self):
        absent = self.dir / "absent-owner.sqlite3"
        args = self.argv(
            "--m05-shadow-db", str(absent),
            "--m05-shadow-owner-id", str(OWNER),
            "--m05-market-advisory-queue",
        )
        rc, data, err = self.invoke(args, go=True)
        self.assertEqual(rc, 2)
        self.assertIn("M05_TICK_PRIVATE_FILE_UNAVAILABLE", err)
        self.assertFalse(absent.exists())


if __name__ == "__main__":
    unittest.main()
