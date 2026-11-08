"""M10 same-original BTC ExecCondition transient self-heal, offline only."""
from __future__ import annotations

from copy import deepcopy
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from indodax_ai_bot.btc_ops_gate import (
    AdmissionDenied, _policy_checks,
)
from indodax_ai_bot import m10_btc_self_heal as h

NOW = 1794100000


def payload(x):
    return json.dumps(x, sort_keys=True, separators=(",", ":")) + "\n"


class M10OriginalBtcTransientSelfHealTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        root = Path(t.name)
        os.chmod(root, 0o700)
        conf_parent = root / ".config"
        conf_parent.mkdir(mode=0o700)
        conf = conf_parent / "indodax-ai-bot-v2"
        conf.mkdir(mode=0o700)
        rt = root / "runtime"
        rt.mkdir(mode=0o700)
        self.consent = conf / "m10-btc-self-heal.json"
        self.go = rt / "btc_public_ops.go.json"
        self.status = rt / "public_ws_btc.status.json"
        self.hold = rt / "btc_public_ops.hold.json"
        self.db = rt / "public_ws_btc.sqlite3"
        self.state = rt / "btc_public_self_heal.state.json"
        self.lease = rt / "btc_public_self_heal.lease.json"
        self.db.write_bytes(b"FAKE_INJECTED_DATABASE_NOT_A_REAL_WS_JOURNAL")
        os.chmod(self.db, 0o600)
        self.g = {
            "scope": "BTC_PUBLIC_MONITOR_ONLY",
            "mode": "OWNER_STANDING_PUBLIC_READONLY",
            "issued_at_epoch": NOW - 7200,
            "max_gap_count": 87,
        }
        self.s = {
            "stage": "M04J_PUBLIC_WS_SUPERVISION_SLICE",
            "status": "REJECT",
            "pair": "btc_idr",
            "requested_sessions": 10,
            "attempted_sessions": 4,
            "completed_safe_sessions": 3,
            "failed_session_number": 4,
            "last_completed_batch_epoch": NOW - 1200,
            "collector_failure_code":
                "WS_SESSION_REJECTED:PUBLIC_WS_STREAM_RECEIVE_CONNECTION_RESET",
            "stream_status": "DISCONNECTED",
            "unrecoverable_gap_rebaseline_count": 5,
            "restart_only_after_clean_batch": False,
            "g1_receipt_issuance_allowed": False,
            "g2_provider_call_allowed": False,
            "execution_allowed": False,
        }
        self.hold_val = {
            "reason": "PUBLIC_WS_SERVICE_FAILURE_HOLD",
            "scope": "BTC_PUBLIC_MONITOR_ONLY",
        }
        self.consent_val = {
            "schema_version": 1,
            "scope": h.CONSENT_SCOPE,
            "owner_authorized": True,
            "issued_at_epoch": NOW - 60,
            "max_attempts_per_24h": 2,
            "minimum_interval_seconds": 1800,
        }
        for file, val in (
            (self.consent, self.consent_val),
            (self.go, self.g),
            (self.status, self.s),
            (self.hold, self.hold_val),
        ):
            self.put(file, val)

    @staticmethod
    def put(path, data):
        path.write_text(payload(data), encoding="ascii")
        os.chmod(path, 0o600)

    def args(self, *, now=NOW):
        return {
            "now": now, "consent": self.consent,
            "go": self.go, "status": self.status, "hold": self.hold,
            "db": self.db, "state": self.state, "lease": self.lease,
        }

    def make_attempt(self, now=NOW):
        with patch.object(h, "_journal_gap_count", return_value=5):
            return h.maybe_auto_heal_original_gate(**self.args(now=now))

    def ready(self, now=NOW):
        with patch.object(h, "_journal_gap_count", return_value=5):
            return h.ready_for_transient_recovery(**self.args(now=now))

    def assert_denied_untouched(self):
        hold_before = self.hold.read_bytes()
        self.assertFalse(self.make_attempt())
        self.assertEqual(self.hold.read_bytes(), hold_before)
        self.assertFalse(self.lease.exists())

    def test_valid_machine_transport_failure_released_once_not_order(self):
        info = self.ready()
        self.assertEqual(info["status"],
                         "AUTO_HEAL_TRANSIENT_PUBLIC_RECOVERY_ELIGIBLE")
        self.assertEqual(info["next_attempt_count_24h"], 1)
        self.assertTrue(self.make_attempt())
        self.assertFalse(self.hold.exists())
        archives = list(self.hold.parent.glob(
            "btc_public_ops.hold.json.auto-recovered.*.bak"
        ))
        self.assertEqual(len(archives), 1)
        self.assertEqual(json.loads(archives[0].read_text()), self.hold_val)
        lease = json.loads(self.lease.read_text())
        self.assertEqual(lease["issued_at_epoch"], NOW)
        self.assertEqual(
            len(json.loads(self.state.read_text())["attempts"]), 1
        )
        self.assertTrue(h.validated_standing_failure_lease(
            now=NOW, policy_bytes=self.go.read_bytes(),
            status_bytes=self.status.read_bytes(),
            lease=self.lease, state=self.state, consent=self.consent,
        ))

    def test_successful_10_10_still_required_for_m03(self):
        self.assertTrue(self.make_attempt())
        with patch.object(h, "_journal_gap_count", return_value=5), patch(
            "indodax_ai_bot.btc_ops_gate._journal_gap_count",
            return_value=5
        ), patch.object(
            h, "validated_standing_failure_lease",
            side_effect=lambda **kw: h_real_validate(
                self, kw["now"], kw["policy_bytes"],
                kw["status_bytes"]
            ),
        ):
            _policy_checks(self.go, self.status, self.db, self.hold, NOW)
            changed = deepcopy(self.s)
            changed["status"] = "PUBLIC_COLLECTION_BATCH_OK_UNATTESTED"
            changed["attempted_sessions"] = 10
            changed["completed_safe_sessions"] = 10
            changed["failed_session_number"] = None
            changed["collector_failure_code"] = None
            changed["restart_only_after_clean_batch"] = True
            self.put(self.status, changed)
            _policy_checks(self.go, self.status, self.db, self.hold, NOW)
            self.assertFalse(h_real_validate(
                self, NOW, self.go.read_bytes(), self.status.read_bytes()
            ))

    def test_missing_optin_leaves_machine_hold(self):
        self.consent.unlink()
        self.assert_denied_untouched()

    def test_operator_manual_hold_always_preserved(self):
        self.put(self.hold, {"reason": "OPERATOR_MANUAL_HOLD",
                             "scope": "BTC_PUBLIC_MONITOR_ONLY"})
        self.assert_denied_untouched()

    def test_unsafe_other_reject_reason_never_auto_heals(self):
        for reason in (
            "WS_STREAM_GAP_OR_EPOCH_CHANGED",
            "WS_SESSION_UNSAFE_AUTHORITY_CLAIM",
            "WS_CONTINUITY_OR_DISCONNECT_REJECTED",
            "PUBLIC_WS_SESSION_UNAVAILABLE",
            "WS_HEARTBEAT_NOT_CONFIRMED",
            "WS_SESSION_UNAVAILABLE",
            "WS_SESSION_REJECTED:PUBLIC_WS_CONNECTION_OR_FRAME_FAILED",
            "WS_SESSION_REJECTED:PUBLIC_WS_STREAM_RECEIVE_TLS_ERROR",
        ):
            with self.subTest(reason=reason):
                self.s["collector_failure_code"] = reason
                self.put(self.status, self.s)
                self.assert_denied_untouched()

    def test_other_authority_claims_cannot_be_auto_promoted(self):
        for key in (
            "g1_receipt_issuance_allowed", "g2_provider_call_allowed",
            "execution_allowed",
        ):
            with self.subTest(key=key):
                self.s[key] = True
                self.put(self.status, self.s)
                self.assert_denied_untouched()
                self.s[key] = False

    def test_standing_consent_only_no_expiring_or_forged_policy(self):
        for bad in (
            {**self.g, "mode": "UNREVIEWED"},
            {**self.g, "expires_at_epoch": NOW + 100},
            {**self.g, "max_gap_count": 0},
        ):
            with self.subTest(bad=bad):
                self.put(self.go, bad)
                self.assert_denied_untouched()

    def test_forged_completed_10_or_corrupted_source_stays_held(self):
        for changed in (
            {**self.s, "attempted_sessions": 11},
            {**self.s, "completed_safe_sessions": 10},
            {**self.s, "failed_session_number": None},
            {**self.s, "stream_status": "GAP"},
        ):
            with self.subTest(changed=changed):
                self.put(self.status, changed)
                self.assert_denied_untouched()

    def test_missing_owner_private_file_no_mutation(self):
        self.go.unlink()
        self.assert_denied_untouched()

    def test_source_too_old_is_manual_owner_review(self):
        self.s["last_completed_batch_epoch"] = NOW - 90000
        self.put(self.status, self.s)
        self.assert_denied_untouched()

    def test_sqlite_gap_mismatch_denied_before_release(self):
        with patch.object(h, "_journal_gap_count", return_value=6):
            self.assertFalse(h.maybe_auto_heal_original_gate(**self.args()))
        self.assertTrue(self.hold.exists())

    def test_fresh_same_failed_batch_is_only_acknowledged_once(self):
        self.assertTrue(self.make_attempt())
        self.put(self.hold, self.hold_val)
        self.assertFalse(self.make_attempt(NOW + 1801))
        self.assertTrue(self.hold.exists())

    def test_cooldown_blocks_distinct_transient_batch(self):
        self.assertTrue(self.make_attempt())
        self.s["last_completed_batch_epoch"] += 2
        self.put(self.status, self.s)
        self.put(self.hold, self.hold_val)
        self.assertFalse(self.make_attempt(NOW + 60))
        self.assertTrue(self.hold.exists())

    def test_two_distinct_attempts_then_daily_cap(self):
        self.assertTrue(self.make_attempt())
        self.s["last_completed_batch_epoch"] += 1800
        self.put(self.status, self.s)
        self.put(self.hold, self.hold_val)
        self.assertTrue(self.make_attempt(NOW + 1801))
        self.assertFalse(self.hold.exists())
        self.s["last_completed_batch_epoch"] += 1800
        self.put(self.status, self.s)
        self.put(self.hold, self.hold_val)
        self.assertFalse(self.make_attempt(NOW + 3602))
        self.assertTrue(self.hold.exists())

    def test_lease_expires_or_source_changes_without_m03_proof(self):
        self.assertTrue(self.make_attempt())
        self.assertFalse(h.validated_standing_failure_lease(
            now=NOW + 3600, policy_bytes=self.go.read_bytes(),
            status_bytes=self.status.read_bytes(),
            lease=self.lease, state=self.state, consent=self.consent,
        ))
        self.s["last_completed_batch_epoch"] += 1
        self.put(self.status, self.s)
        self.assertFalse(h.validated_standing_failure_lease(
            now=NOW + 10, policy_bytes=self.go.read_bytes(),
            status_bytes=self.status.read_bytes(),
            lease=self.lease, state=self.state, consent=self.consent,
        ))

    def test_consent_revocation_disables_reject_receipt(self):
        self.assertTrue(self.make_attempt())
        self.consent.unlink()
        self.assertFalse(h.validated_standing_failure_lease(
            now=NOW, policy_bytes=self.go.read_bytes(),
            status_bytes=self.status.read_bytes(),
            lease=self.lease, state=self.state, consent=self.consent,
        ))

    def test_corrupt_history_fails_closed(self):
        self.put(self.state, {
            "schema_version":1, "scope":h.CONSENT_SCOPE,
            "attempts":[{"epoch":NOW-10,"status_sha256":"x"}],
        })
        self.assert_denied_untouched()

    def test_symlink_optin_denied(self):
        real = self.consent.with_name("real-consent")
        self.consent.rename(real)
        self.consent.symlink_to(real)
        self.assert_denied_untouched()

    def test_default_readonly_diagnostic_never_changes_source(self):
        with patch.object(h, "ready_for_transient_recovery",
                          return_value={"status":
                            "AUTO_HEAL_TRANSIENT_PUBLIC_RECOVERY_ELIGIBLE",
                            "next_attempt_count_24h": 1}):
            r = h.diagnose_self_heal_readonly(now=NOW)
        self.assertTrue(r["eligible_transient_recovery_snapshot"])
        self.assertFalse(r["hold_or_policy_modified"])
        self.assertFalse(r["service_started_or_restarted"])
        self.assertFalse(r["order_sent"])

    def test_owner_enable_is_only_local_optin_no_service_start(self):
        from scripts import m10_btc_self_heal_owner as cli
        self.consent.unlink()
        drop = self.db.parent / "known-dropin.conf"
        templ = self.db.parent / "reviewed-source.conf"
        drop.write_text("[Service]\nRestart=no\n", encoding="ascii")
        templ.write_text("[Service]\nRestart=no\n", encoding="ascii")
        with patch.object(cli, "ROOT", self.db.parent), patch.object(
            cli, "DROP", drop
        ), patch.object(cli, "TEMPLATE", templ), patch.object(
            cli, "_units", return_value={"fixture": "original"}
        ), patch.object(
            cli, "assess_original_btc_systemd"
        ) as systemd:
            r = cli.enable_owner_optin(
                now=NOW, consent=self.consent, go=self.go,
            )
        systemd.assert_called_once()
        self.assertEqual(r["status"], "M10_PUBLIC_AUTO_HEAL_OPTED_IN")
        self.assertTrue(self.consent.exists())
        self.assertEqual(
            stat.S_IMODE(self.consent.stat().st_mode), 0o600
        )
        self.assertTrue(self.hold.exists())
        self.assertFalse(self.lease.exists())
        self.assertFalse(r["service_started_or_restarted"])
        self.assertFalse(r["order_sent"])

    def test_owner_script_requires_flag_no_mutations(self):
        from scripts import m10_btc_self_heal_owner
        with patch("sys.argv", ["m10_btc_self_heal_owner",
                                 "--owner-enable-standing-transient"]):
            self.assertEqual(m10_btc_self_heal_owner.main(), 2)

    def test_original_execcondition_direct_python_file_imports_optional_guard(self):
        # The deployed drop-in calls this PATH as a Python file, not -m.
        # -I excludes cwd/PYTHONPATH and catches package import failures.
        self.hold.unlink()
        self.db.unlink()
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE ws_stream_discontinuities (pair TEXT)")
            conn.executemany(
                "INSERT INTO ws_stream_discontinuities (pair) VALUES (?)",
                [("btc_idr",)] * 5
            )
        script = Path(h.__file__).with_name("btc_ops_gate.py")
        proc = subprocess.run(
            [
                sys.executable, "-I", "-B", str(script), "diagnose",
                "--go", str(self.go),
                "--status", str(self.status),
                "--db", str(self.db),
                "--hold", str(self.hold),
                "--state", str(self.state),
            ],
            cwd=self.db.parent, capture_output=True,
            text=True, timeout=10, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[:300])
        report = json.loads(proc.stdout)
        self.assertEqual(report["first_blocker"],
                         "PUBLIC_WS_FAILURE_NEEDS_NEW_OWNER_ACK")
        self.assertFalse(report["service_started_or_restarted"])

    def test_existing_gate_without_optin_still_denies_hold(self):
        self.consent.unlink()
        with patch.object(h, "_journal_gap_count", return_value=5):
            with self.assertRaises(AdmissionDenied) as result:
                _policy_checks(self.go, self.status, self.db, self.hold, NOW)
        self.assertEqual(str(result.exception), "PUBLIC_WS_OPERATOR_HOLD")


def h_real_validate(testcase, now, policy_bytes, status_bytes):
    fn = testcase._actual_validate
    return fn(
        now=now, policy_bytes=policy_bytes, status_bytes=status_bytes,
        lease=testcase.lease, state=testcase.state,
        consent=testcase.consent,
    )


# Freeze real function BEFORE patching its module name.
M10OriginalBtcTransientSelfHealTests._actual_validate = staticmethod(
    h.validated_standing_failure_lease
)


if __name__ == "__main__":
    unittest.main()
