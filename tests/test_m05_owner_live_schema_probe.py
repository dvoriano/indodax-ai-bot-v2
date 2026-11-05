"""Offline NO-NETWORK tests for original V2 M05 live SQLite schema probe."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.m05_owner_live_schema_probe import (
    OwnerSchemaProbeError,
    _database_path_from_env,
    _PREVIEW_COLUMNS,
    inspect_original_owner_schema,
)


class OwnerLiveSchemaReadonlyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="v2-m05-schema-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.state_dir = self.home / "state"
        self.state_dir.mkdir(mode=0o700)
        self.db = self.state_dir / "owner.sqlite3"
        with sqlite3.connect(self.db) as con:
            con.executescript("""
                CREATE TABLE m05_config(
                    singleton INTEGER PRIMARY KEY,owner_user_id INTEGER,mode TEXT);
                INSERT INTO m05_config VALUES (1,1234567,'MANUAL');
                CREATE TABLE m05_market_advisory_outbox(
                    pair TEXT,closed_1m_epoch INTEGER,
                    sent_message_id INTEGER,delivery_state TEXT);
                INSERT INTO m05_market_advisory_outbox
                    VALUES ('reviewtest_idr',1793845500,12,'SENT');
                INSERT INTO m05_market_advisory_outbox
                    VALUES ('onl_idr',1793845500,44,'SENT');
                INSERT INTO m05_market_advisory_outbox
                    VALUES ('uai_idr',1793845800,48,'SENT');
                INSERT INTO m05_market_advisory_outbox
                    VALUES ('zerebro_idr',1793846100,50,'SENT');
                INSERT INTO m05_market_advisory_outbox
                    VALUES ('uai_idr',1793846200,NULL,'QUEUED');
            """)
        self.db.chmod(0o600)
        self.envdir = self.home / ".config" / "indodax-ai-bot-v2" / "m05"
        self.envdir.mkdir(parents=True, mode=0o700)
        self.env = self.envdir / "service.env"
        self.env.write_text(
            f"M05_TELEGRAM_OWNER_DB={self.db}\n"
            "M05_TELEGRAM_OWNER_ID=1234567\n"
            "M05_TELEGRAM_TOKEN_FILE=/redacted-do-not-read\n",
            encoding="utf-8",
        )
        self.env.chmod(0o600)

    def test_missing_manual_preview_preserves_real_market_evidence(self):
        before = self.db.stat()
        result = inspect_original_owner_schema(self.db)
        after = self.db.stat()
        self.assertEqual(result["status"], "M05_OWNER_SCHEMA_READONLY_OBSERVED")
        self.assertEqual(result["manual_preview_table"], "ABSENT")
        self.assertEqual(set(result["preview_missing_required_columns"]), _PREVIEW_COLUMNS)
        self.assertEqual(result["real_market_db_ack_non_test_count"], 3)
        self.assertEqual(result["queued_advisories"], 1)
        self.assertEqual(result["quick_check"], "ok")
        self.assertEqual((before.st_mtime_ns, before.st_size),
                         (after.st_mtime_ns, after.st_size))
        self.assertFalse(result["telegram_network_used"])
        self.assertFalse(result["database_contents_modified"])
        self.assertNotIn("redacted-do-not-read", json.dumps(result))

    def test_present_required_manual_preview_schema(self):
        columns = ",".join(f"{key} TEXT" for key in sorted(_PREVIEW_COLUMNS))
        with sqlite3.connect(self.db) as con:
            con.execute(f"CREATE TABLE m05_manual_trade_previews({columns})")
        report = inspect_original_owner_schema(self.db)
        self.assertEqual(report["manual_preview_table"], "PRESENT_REQUIRED_COLUMNS")
        self.assertEqual(report["preview_missing_required_columns"], [])

    def test_partial_manual_preview_schema_is_not_accepted(self):
        with sqlite3.connect(self.db) as con:
            con.execute("CREATE TABLE m05_manual_trade_previews(owner_choice TEXT)")
        self.assertEqual(
            inspect_original_owner_schema(self.db)["manual_preview_table"],
            "UNEXPECTED_OR_PARTIAL_SCHEMA"
        )

    def test_wrong_owner_mode_rejected_without_writes(self):
        with sqlite3.connect(self.db) as con:
            con.execute("UPDATE m05_config SET mode='AUTO'")
        with self.assertRaisesRegex(OwnerSchemaProbeError, "M05_PROBE_OWNER_MODE_NOT_MANUAL"):
            inspect_original_owner_schema(self.db)

    def test_reads_only_allowlisted_owner_db_path_not_token(self):
        self.assertEqual(_database_path_from_env(self.env, self.home), self.db)
        self.env.write_text(
            f"M05_TELEGRAM_OWNER_DB={self.db}\n"
            f"M05_TELEGRAM_OWNER_DB={self.db}\n"
            "M05_TELEGRAM_TOKEN_FILE=SECRET_SHOULD_NOT_ECHO\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(OwnerSchemaProbeError, "M05_PROBE_OWNER_DB_CONFIG_UNVERIFIED"):
            _database_path_from_env(self.env, self.home)


if __name__ == "__main__":
    unittest.main()
