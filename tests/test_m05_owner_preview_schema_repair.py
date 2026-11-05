"""Original V2 owner preview single-table repair, 100% local offline tests."""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.telegram_dedicated_inbound import dedicated_poller_lock
from indodax_ai_bot.telegram_bot_api import TelegramTransportError
from scripts import m05_owner_preview_schema_repair as repair


class M05OriginalPreviewSchemaRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-m05-preview-repair-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.state = self.home / "indodax-ai-bot-v2" / "state"
        self.state.mkdir(parents=True, mode=0o700)
        self.state.chmod(0o700)
        self.db = self.state / "owner.sqlite3"
        self.owner_id = 1234567
        OwnerDecisionLedger(self.db, owner_user_id=self.owner_id)
        self.db.chmod(0o600)

        # Model a previously working original market outbox whose optional
        # manual-preview table is absent on the real owner VPS.
        with sqlite3.connect(self.db) as con:
            con.execute("DROP TABLE m05_manual_trade_previews")
            samples = [
                ("a" * 24, "onl_idr", 1793845200, 44, "SENT"),
                ("b" * 24, "uai_idr", 1793845200, 45, "SENT"),
                ("c" * 24, "uai_idr", 1793845800, 48, "SENT"),
                ("d" * 24, "zerebro_idr", 1793846100, 50, "SENT"),
                ("e" * 24, "reviewtest_idr", 1793846100, 49, "SENT"),
                ("f" * 24, "inj_idr", 1793846160, None, "QUEUED"),
            ]
            for proposal_id, pair, candle, message_id, state in samples:
                con.execute(
                    "INSERT INTO m05_market_advisory_outbox("
                    "proposal_id,pair,closed_1m_epoch,expires_epoch,"
                    "message_text,pass_callback,reject_callback,"
                    "delivery_state,sent_message_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (proposal_id, pair, candle, candle + 600,
                     "not a real Telegram message", "test-p", "test-r",
                     state, message_id),
                )

    def _repair(self, *, owner_id=None):
        with patch.object(Path, "home", return_value=self.home):
            return repair.repair_preview_table(
                db=self.db,
                owner_id=self.owner_id if owner_id is None else owner_id,
            )

    def _outbox(self, path: Path):
        with sqlite3.connect(path) as con:
            return con.execute(
                "SELECT proposal_id,pair,closed_1m_epoch,delivery_state,"
                "sent_message_id FROM m05_market_advisory_outbox "
                "ORDER BY proposal_id"
            ).fetchall()

    def test_exact_original_schema_created_and_all_old_rows_preserved(self):
        old_rows = self._outbox(self.db)
        result = self._repair()
        self.assertEqual(result["status"], "M05_PREVIEW_SCHEMA_ORIGINAL_V2_RESTORED")
        self.assertTrue(result["database_changed"])
        self.assertTrue(result["backup_created"])
        self.assertEqual(result["real_market_acks_unchanged_during_transaction"], 4)
        self.assertEqual(result["queued_outbox_rows_unchanged_during_transaction"], 1)
        self.assertEqual(result["all_existing_outbox_rows_unchanged_during_transaction"], 6)
        self.assertFalse(result["m05_timer_modified"])
        self.assertFalse(result["m03_timer_modified"])
        self.assertFalse(result["telegram_network_used"])
        self.assertFalse(result["trading_enabled"])
        self.assertEqual(result["owner_mode"], "MANUAL")
        self.assertEqual(result["quick_check"], "ok")
        self.assertEqual(self._outbox(self.db), old_rows)

        backup = self.state / result["backup_file_name"]
        self.assertTrue(backup.exists())
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self._outbox(backup), old_rows)
        with sqlite3.connect(backup) as con:
            self.assertIsNone(con.execute(
                "SELECT name FROM sqlite_master "
                "WHERE name='m05_manual_trade_previews'"
            ).fetchone())
        with sqlite3.connect(self.db) as con:
            observed = {x[1] for x in con.execute(
                "PRAGMA table_info(m05_manual_trade_previews)"
            )}
            self.assertTrue(repair._REQUIRED <= observed)
            self.assertEqual(
                con.execute("PRAGMA quick_check(1)").fetchone(), ("ok",)
            )

    def test_idempotent_existing_schema_no_second_backup(self):
        self._repair()
        before = sorted(x.name for x in self.state.iterdir())
        second = self._repair()
        self.assertEqual(second["status"], "M05_PREVIEW_SCHEMA_ALREADY_COMPLETE")
        self.assertFalse(second["database_changed"])
        self.assertEqual(sorted(x.name for x in self.state.iterdir()), before)

    def test_partial_existing_table_refused_no_ddl_or_backup(self):
        with sqlite3.connect(self.db) as con:
            con.execute(
                "CREATE TABLE m05_manual_trade_previews(owner_choice TEXT)"
            )
        rows = self._outbox(self.db)
        with self.assertRaisesRegex(
            repair.PreviewRepairDenied, "M05_PREVIEW_PARTIAL_TABLE_REVIEW_REQUIRED"
        ):
            self._repair()
        self.assertEqual(rows, self._outbox(self.db))
        with sqlite3.connect(self.db) as con:
            self.assertEqual(len(con.execute(
                "PRAGMA table_info(m05_manual_trade_previews)"
            ).fetchall()), 1)
        self.assertFalse(any(
            ".m05-before-preview-repair-" in p.name for p in self.state.iterdir()
        ))

    def test_auto_mode_denied_without_changes(self):
        with sqlite3.connect(self.db) as con:
            con.execute("UPDATE m05_config SET mode='AUTO'")
        with self.assertRaisesRegex(
            repair.PreviewRepairDenied, "M05_PREVIEW_OWNER_MODE_OR_ID_MISMATCH"
        ):
            self._repair()
        with sqlite3.connect(self.db) as con:
            self.assertFalse(con.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE name='m05_manual_trade_previews'"
            ).fetchone())

    def test_owner_identity_mismatch_denied_without_changes(self):
        with self.assertRaisesRegex(
            repair.PreviewRepairDenied, "M05_PREVIEW_OWNER_MODE_OR_ID_MISMATCH"
        ):
            self._repair(owner_id=9999999)
        self.assertEqual(len(self._outbox(self.db)), 6)

    def test_existing_poller_lock_cannot_be_bypassed(self):
        with dedicated_poller_lock(str(self.db) + ".dedicated-poller.lock"):
            with self.assertRaises(TelegramTransportError):
                self._repair()
        self.assertEqual(len(self._outbox(self.db)), 6)
        with sqlite3.connect(self.db) as con:
            self.assertFalse(con.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE name='m05_manual_trade_previews'"
            ).fetchone())

    def test_backup_failure_does_not_change_original_schema(self):
        with patch.object(
            repair, "_backup_online", side_effect=OSError("simulated IO error")
        ):
            with self.assertRaises(OSError):
                self._repair()
        self.assertEqual(len(self._outbox(self.db)), 6)
        with sqlite3.connect(self.db) as con:
            self.assertFalse(con.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE name='m05_manual_trade_previews'"
            ).fetchone())


if __name__ == "__main__":
    unittest.main()
