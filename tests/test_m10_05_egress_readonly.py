"""Offline no-network tests for V2-only M10 local quota snapshot.

Tests create only isolated temp fixtures. The production diagnostic must
never write runtime files, call HTTP, inspect other repos, or authorize M03.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from indodax_ai_bot.m10_egress_readonly import (
    inspect_v2_local_egress, main,
)

NOW = 1800000100.0


def database(path: Path, *, cap: int | None = 4, with_reservations: bool = True):
    with sqlite3.connect(path) as con:
        if with_reservations:
            con.execute(
                "CREATE TABLE rest_get_reservations "
                "(id INTEGER PRIMARY KEY, requested_at REAL NOT NULL)"
            )
            for t in (NOW - 61, NOW - 59, NOW - 5):
                con.execute(
                    "INSERT INTO rest_get_reservations (requested_at) VALUES (?)",
                    (t,),
                )
        if cap is not None:
            con.execute(
                "CREATE TABLE rest_shared_egress_cap "
                "(id INTEGER PRIMARY KEY CHECK(id=1), "
                "max_local_requests_60s INTEGER NOT NULL)"
            )
            con.execute(
                "INSERT INTO rest_shared_egress_cap VALUES (1,?)", (cap,),
            )
    path.chmod(0o600)


class M10EgressReadonlyTests(unittest.TestCase):
    def assert_external_unverified(self, result):
        self.assertFalse(result["outbound_public_ip_independently_verified"])
        self.assertFalse(result["all_egress_ip_consumers_verified"])
        self.assertFalse(result["external_rolling_60s_peak_verified"])
        self.assertIsNone(result["external_rolling_60s_peak_requests"])
        self.assertFalse(result["live_v2_clients_same_db_verified"])
        self.assertFalse(result["m03_timer_go_authorized"])
        self.assertFalse(result["m10_05_full_acceptance"])
        self.assertNotIn("8.8.8.8", json.dumps(result))

    def test_v2_local_cap_and_bounded_rolling_reservations(self):
        with TemporaryDirectory() as td:
            db = Path(td) / "v2.sqlite3"
            database(db, cap=3)
            before = db.read_bytes()
            result = inspect_v2_local_egress(db, now_epoch=NOW)
            self.assertEqual(
                result["status"], "V2_LOCAL_SNAPSHOT_ONLY_EXTERNAL_UNVERIFIED"
            )
            self.assertTrue(result["database_present"])
            self.assertEqual(result["rolling_60s_v2_reserved_requests"], 2)
            self.assertEqual(result["durable_v2_cap_requests_60s"], 3)
            self.assertFalse(result["snapshot_wal_possible"])
            self.assert_external_unverified(result)
            self.assertEqual(db.read_bytes(), before)
            self.assertEqual(
                sorted(p.name for p in Path(td).iterdir()), ["v2.sqlite3"]
            )

    def test_absent_db_does_not_create_any_dir(self):
        with TemporaryDirectory() as td:
            db = Path(td) / "missing" / "v2.sqlite3"
            result = inspect_v2_local_egress(db, now_epoch=NOW)
            self.assertEqual(result["status"], "V2_LOCAL_DB_ABSENT")
            self.assertFalse(result["database_present"])
            self.assertFalse(db.parent.exists())
            self.assert_external_unverified(result)

    def test_active_wal_or_journal_never_trusted_as_current(self):
        for suffix in ("-wal", "-journal"):
            with self.subTest(suffix=suffix), TemporaryDirectory() as td:
                db = Path(td) / "quota.sqlite3"
                database(db, cap=3)
                sidecar = Path(str(db) + suffix)
                sidecar.write_bytes(b"unverified runtime sidecar")
                before = {p.name: p.read_bytes() for p in Path(td).iterdir()}
                result = inspect_v2_local_egress(db, now_epoch=NOW)
                self.assertEqual(
                    result["status"],
                    "V2_LOCAL_DB_ACTIVE_JOURNAL_SNAPSHOT_UNRELIABLE",
                )
                self.assertTrue(result["snapshot_wal_possible"])
                self.assertIsNone(result["rolling_60s_v2_reserved_requests"])
                self.assertIsNone(result["durable_v2_cap_requests_60s"])
                self.assert_external_unverified(result)
                self.assertEqual(
                    before,
                    {p.name: p.read_bytes() for p in Path(td).iterdir()},
                )

    def test_dangerous_symlink_and_writable_file_are_untrusted(self):
        with TemporaryDirectory() as td:
            db = Path(td) / "quota.sqlite3"
            database(db)
            linked = Path(td) / "linked.sqlite3"
            linked.symlink_to(db)
            for unsafe in (linked, db):
                with self.subTest(name=unsafe.name):
                    if unsafe == db:
                        db.chmod(0o666)
                    result = inspect_v2_local_egress(unsafe, now_epoch=NOW)
                    self.assertEqual(result["status"], "V2_LOCAL_DB_UNTRUSTED")
                    self.assert_external_unverified(result)

    def test_db_without_reservations_and_bad_cap_does_not_promote(self):
        with TemporaryDirectory() as td:
            db = Path(td) / "quota.sqlite3"
            database(db, with_reservations=False)
            result = inspect_v2_local_egress(db, now_epoch=NOW)
            self.assertEqual(result["status"], "V2_LOCAL_DB_NO_RESERVATION_TABLE")
            self.assert_external_unverified(result)
        with TemporaryDirectory() as td:
            db = Path(td) / "quota.sqlite3"
            database(db, cap=900)
            result = inspect_v2_local_egress(db, now_epoch=NOW)
            self.assertEqual(result["status"], "V2_LOCAL_DB_INVALID_CAP")
            self.assertIsNone(result["rolling_60s_v2_reserved_requests"])
            self.assert_external_unverified(result)

    def test_corrupt_database_never_promotes(self):
        with TemporaryDirectory() as td:
            db = Path(td) / "quota.sqlite3"
            db.write_bytes(b"not a sqlite database")
            db.chmod(0o600)
            result = inspect_v2_local_egress(db, now_epoch=NOW)
            self.assertEqual(result["status"], "V2_LOCAL_DB_READ_UNAVAILABLE")
            self.assert_external_unverified(result)

    def test_env_override_is_flagged_but_not_followed(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            default = root / ".cache" / "indodax-ai-bot-v2" / "rest-rate.sqlite3"
            default.parent.mkdir(parents=True)
            database(default, cap=3)
            other = root / "other.sqlite3"
            database(other, cap=99)
            with (patch("indodax_ai_bot.m10_egress_readonly.Path.home",
                        return_value=root),
                  patch.dict(os.environ, {
                      "INDODAX_PUBLIC_REST_RATE_DB": str(other),
                  })):
                result = inspect_v2_local_egress(now_epoch=NOW)
            self.assertEqual(result["db_path_scope"], "V2_DEFAULT")
            self.assertTrue(result["caller_env_override_declared"])
            self.assertEqual(result["durable_v2_cap_requests_60s"], 3)
            self.assert_external_unverified(result)

    def test_cli_emits_safe_json_without_creating_db(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            out = StringIO()
            with (patch("indodax_ai_bot.m10_egress_readonly.Path.home",
                        return_value=root),
                  redirect_stdout(out)):
                code = main()
            self.assertEqual(code, 0)
            result = json.loads(out.getvalue())
            self.assertEqual(result["status"], "V2_LOCAL_DB_ABSENT")
            self.assertFalse((root / ".cache").exists())
            self.assert_external_unverified(result)
            raw = out.getvalue()
            for secret_key in ("api_key", "api_secret", "owner_user_id"):
                self.assertNotIn(secret_key, raw)

    def test_invalid_clock_blocks_no_disk_mutation(self):
        with TemporaryDirectory() as td:
            for bad in (float("nan"), 0, -3, True, "bad"):
                with self.subTest(bad=repr(bad)):
                    with self.assertRaises(ValueError):
                        inspect_v2_local_egress(
                            Path(td) / "nothing.sqlite3", now_epoch=bad,
                        )
            self.assertEqual(list(Path(td).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
