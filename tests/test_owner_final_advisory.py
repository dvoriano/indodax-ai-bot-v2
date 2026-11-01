"""Owner is the only Final Decision authority in the original V2 public mainpath.

These targeted tests do not use private API, AI provider, or exchange orders.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionConflictError, DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.public_market import PublicDataError


class OwnerFinalAdvisoryMainpathTest(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = DecisionJournal(str(Path(self.temp.name) / "decisions.sqlite3"))
        self.app = DailyScalpingOrchestrator(Settings(), self.journal)

    def test_stale_or_missing_ws_does_not_issue_automatic_final_reject(self):
        with patch.object(self.app, "run_bulk_deep_cycle", return_value="sentinel") as run:
            value = self.app.run_cycle("missing-ws", public_scanner=True,
                                       ws_db_path=None,
                                       min_volume_idr=Decimal("150000000"))
        self.assertEqual(value, "sentinel")
        self.assertEqual(run.call_args.kwargs["ws_db_path"], None)
        self.assertEqual(run.call_args.kwargs["min_volume_idr"], Decimal("150000000"))
        self.assertEqual(self.journal.count(), 0)

    def test_valid_public_diagnostic_is_not_owner_reject(self):
        observation = {"coverage_status": "ALL_SELECTED_ANALYZED_UNATTESTED"}
        with (
            patch.object(self.app, "_scan_with_early_momentum", return_value=object()),
            patch("indodax_ai_bot.orchestrator.bulk_public_deep_scan",
                  return_value=observation),
            patch("indodax_ai_bot.orchestrator.ThreeRoleShadow") as shadow,
        ):
            shadow.return_value.run.return_value = {"status": "UNAVAILABLE"}
            result = self.app.run_bulk_deep_cycle("advisory-success", ws_db_path=None)
        self.assertIsNone(result.final_decision)
        self.assertEqual(result.stage_disposition, "ADVISORY_ONLY_NO_OWNER_DECISION")
        self.assertFalse(result.execution_allowed)
        self.assertIs(result.bulk, observation)
        self.assertEqual(self.journal.count(), 0)
        self.assertEqual(self.journal.stage_observation_count(), 1)

    def test_bad_public_diagnostic_remains_visible_without_owner_reject(self):
        with (
            patch.object(self.app, "_scan_with_early_momentum",
                              side_effect=PublicDataError("FEED_UNAVAILABLE")),
            patch("indodax_ai_bot.orchestrator.ThreeRoleShadow") as shadow,
        ):
            shadow.return_value.run.return_value = {"status": "UNAVAILABLE"}
            result = self.app.run_bulk_deep_cycle("advisory-degraded", ws_db_path=None)
        self.assertIsNone(result.final_decision)
        self.assertEqual(result.reason, "PUBLIC_BULK_BLOCKED:FEED_UNAVAILABLE")
        self.assertEqual(result.stage_disposition, "ADVISORY_ONLY_NO_OWNER_DECISION")
        self.assertIsNone(result.bulk)
        self.assertFalse(result.execution_allowed)
        self.assertEqual(self.journal.count(), 0)
        self.assertEqual(self.journal.stage_observation_count(), 1)

    def test_stage_journal_is_idempotent_and_never_writes_final(self):
        self.journal.record_stage_observation("trace1", "M03_M04_PRE_FINAL", "unverified")
        self.journal.record_stage_observation("trace1", "M03_M04_PRE_FINAL", "unverified")
        self.assertEqual(self.journal.stage_observation_count(), 1)
        self.assertEqual(self.journal.count(), 0)
        with self.assertRaises(DecisionConflictError):
            self.journal.record_stage_observation("trace1", "M03_M04_PRE_FINAL", "changed")

    def test_legacy_explicit_final_journal_remains_separate(self):
        self.journal.record_stage_observation("trace", "M03_M04_PRE_FINAL", "unavailable")
        self.journal.record_reject("owner-final-reject", "OWNER_DECISION_REJECT")
        self.assertEqual(self.journal.stage_observation_count(), 1)
        self.assertEqual(self.journal.count(), 1)
