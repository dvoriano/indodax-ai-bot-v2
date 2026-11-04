"""One ORIGINAL V2 M03/M05 owner VPS metadata probe, purely offline.

Fake fixed-name systemctl and isolated private temp home; NO network,
no token data, source file writes, systemd start/restart or order.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts.m03_m05_owner_vps_snapshot import (
    collect_original_v2_runtime_snapshot, _owner_env_metadata,
    _safe_private_file,
)

NOW = 1800000000
SHA = "a" * 40
OWNER = 1234567


class OriginalV2M03M05RuntimeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-m03m05-readonly-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.root = self.home / "indodax-ai-bot-v2"
        pkg = self.root / "indodax_ai_bot"
        pkg.mkdir(parents=True)
        (pkg / "__main__.py").write_text("# original V2 synthetic placeholder\n")
        self.deploy = self.root / "deploy"
        self.deploy.mkdir()
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.ws = self.runtime / "public_ws_btc.sqlite3"
        self.ws.write_bytes(b"FAKE_BTC_WS_DATABASE_METADATA_ONLY")
        os.utime(self.ws, (NOW - 44, NOW - 44))
        self.status = self.runtime / "public_ws_btc.status.json"
        self.status.write_text(json.dumps({
            "status": "PUBLIC_COLLECTION_BATCH_OK_UNATTESTED",
            "requested_sessions": 10,
            "attempted_sessions": 10,
            "completed_safe_sessions": 10,
            "last_completed_batch_epoch": NOW - 80,
            "restart_only_after_clean_batch": True,
            "collector_failure_code": None,
            "stream_status": "DISCONNECTED",
        }))
        self.status.chmod(0o600)
        for p, name in (
            ("indodax-v2-m03-mainpath.service", False),
            ("indodax-v2-m03-owner-advisory.conf", True),
        ):
            pass
        template = (
            "WorkingDirectory=%h/indodax-ai-bot-v2\n"
            "ExecStart=%h/indodax-ai-bot-v2/.venv/bin/python -B -m indodax_ai_bot "
            "--scheduled-public-scanner-once\n"
            "Environment=INDODAX_TRADING_ENABLED=NO\n"
            "Environment=INDODAX_ALLOW_LIVE_EXECUTION=NO\n"
            "Environment=INDODAX_EXECUTION_MODE=DRY_RUN\n"
        )
        for name in ("indodax-v2-m03-mainpath.service",
                     "indodax-v2-m03-owner-advisory.conf"):
            (self.deploy / name).write_text(template)
        cfg = self.home / ".config" / "indodax-ai-bot-v2"
        cfg.mkdir(parents=True, mode=0o700)
        cfg.chmod(0o700)
        self.policy = cfg / "m03-egress-admission.json"
        self.policy.write_text("{}")
        self.policy.chmod(0o600)
        m05 = cfg / "m05"
        m05.mkdir(mode=0o700)
        m05.chmod(0o700)
        secrets = self.home / "secrets"
        secrets.mkdir(mode=0o700)
        secrets.chmod(0o700)
        self.token = secrets / "token"
        self.token.write_text("DO_NOT_READ_FAKE_TOKEN")
        self.token.chmod(0o600)
        state = self.home / "state"
        state.mkdir(mode=0o700)
        state.chmod(0o700)
        self.db = state / "owner.sqlite3"
        with sqlite3.connect(self.db) as con:
            con.executescript("""
                CREATE TABLE m05_config(
                    singleton INTEGER PRIMARY KEY,
                    owner_user_id INTEGER, mode TEXT);
                INSERT INTO m05_config VALUES(1,1234567,'MANUAL');
                CREATE TABLE m05_market_advisory_outbox(delivery_state TEXT);
                INSERT INTO m05_market_advisory_outbox VALUES('QUEUED');
                INSERT INTO m05_market_advisory_outbox VALUES('SENT');
                CREATE TABLE m05_manual_trade_previews(
                    owner_choice TEXT,send_state TEXT);
                INSERT INTO m05_manual_trade_previews VALUES(NULL,'QUEUED');
            """)
        self.db.chmod(0o600)
        self.owner_env = m05 / "service.env"
        self.owner_env.write_text(
            f"M05_TELEGRAM_OWNER_DB={self.db}\n"
            f"M05_TELEGRAM_OWNER_ID={OWNER}\n"
            f"M05_TELEGRAM_TOKEN_FILE={self.token}\n"
            "OTHER_SECRET_THAT_MUST_NEVER_APPEAR=DoNotEchoThisSecret\n"
        )
        self.owner_env.chmod(0o600)
        self.config = cfg

    def fake_show(self, unit):
        base = (self.home / ".config" / "systemd" / "user" / unit)
        if "m03-mainpath.timer" in unit:
            active = "inactive"
            enabled = "disabled"
        elif "m05-telegram.timer" in unit:
            active = "active"
            enabled = "enabled"
        else:
            active = "inactive"
            enabled = "static"
        return (
            "LoadState=loaded\n" +
            "ActiveState=" + active + "\n" +
            "UnitFileState=" + enabled + "\n" +
            "FragmentPath=" + str(base) + "\n" +
            "NeedDaemonReload=no\n"
        )

    def inspect(self, **overrides):
        args = {
            "root": self.root, "home": self.home,
            "clock": lambda: NOW,
            "unit_reader": self.fake_show,
            "source_sha_reader": lambda p: SHA,
            "egress_validator": lambda p: 110,
        }
        args.update(overrides)
        return collect_original_v2_runtime_snapshot(**args)

    def test_bounded_snapshot_is_informational_only(self):
        report = self.inspect()
        self.assertEqual(report["scope"],
                         "SINGLE_CANONICAL_V2_OWNER_VPS_READONLY_SNAPSHOT")
        self.assertEqual(report["original_v2_git_head_sha"], SHA)
        self.assertEqual(report["observed_epoch"], NOW)
        self.assertTrue(report["btc_ws_database_file_present"])
        self.assertEqual(report["btc_ws_database_file_age_seconds_metadata_only"],44)
        self.assertEqual(report["btc_ws_last_batch"]["last_batch_age_seconds"],80)
        self.assertEqual(report["m03_egress"]["max_local_requests_rolling_60s_declared"],110)
        self.assertTrue(report["m03_advisory_source_templates_nonlive"])
        self.assertTrue(report["ready_for_nonlive_scanner_canary"])
        self.assertFalse(report["scanner_canary_start_authorized"])
        self.assertFalse(report["manual_order_sent"])
        self.assertFalse(report["auto_execution_enabled"])
        self.assertFalse(report["live_trading_authorized"])
        self.assertEqual(report["exchange_gets_or_orders_sent"],0)
        self.assertEqual(report["telegram_api_calls_sent"],0)

    def test_owner_ledger_counts_only_without_secrets(self):
        report = self.inspect()
        owner = report["m05_owner"]
        self.assertTrue(owner["owner_ledger_source_identity_verified"])
        self.assertTrue(owner["telegram_token_file_permissions_ok"])
        self.assertEqual(owner["m05_queued_advisories"],1)
        self.assertEqual(owner["m05_sent_advisories"],1)
        self.assertEqual(owner["m05_pending_manual_previews"],1)
        raw = json.dumps(report)
        self.assertNotIn("DoNotEchoThisSecret", raw)
        self.assertNotIn("DO_NOT_READ_FAKE_TOKEN", raw)
        self.assertNotIn(str(self.token), raw)
        self.assertNotIn(str(self.db), raw)
        self.assertNotIn(str(self.policy), raw)

    def test_never_interpret_timer_metadata_as_real_scanner_proof(self):
        report = self.inspect()
        self.assertFalse(report["m03_scanner_timer_active_metadata_only"])
        self.assertTrue(report["m05_telegram_timer_active_metadata_only"])
        self.assertFalse(report["scanner_recommendations_actually_delivered_verified"])
        self.assertFalse(report["three_ai_paid_model_outputs_observed"])
        self.assertFalse(report["source_sha_is_running_process_attestation"])

    def test_active_m03_timer_does_not_authorize_orders_or_go(self):
        def active(unit):
            raw = self.fake_show(unit)
            if "m03-mainpath.timer" in unit:
                return raw.replace("ActiveState=inactive", "ActiveState=active").replace(
                    "UnitFileState=disabled","UnitFileState=enabled")
            return raw
        report=self.inspect(unit_reader=active)
        self.assertTrue(report["m03_scanner_timer_active_metadata_only"])
        self.assertFalse(report["scanner_recommendations_actually_delivered_verified"])
        self.assertFalse(report["scanner_canary_start_authorized"])
        self.assertFalse(report["live_trading_authorized"])

    def test_egress_unknown_cannot_report_canary_ready(self):
        report=self.inspect(egress_validator=lambda p: 0)
        self.assertFalse(report["ready_for_nonlive_scanner_canary"])
        self.assertEqual(report["m03_egress"]["status"],"NOT_VERIFIED")

    def test_egress_expired_rejected_without_untrusted_policy_content(self):
        def fail(_):
            from indodax_ai_bot.m03_egress_admission import M03EgressAdmissionError
            raise M03EgressAdmissionError("M03_EGRESS_ATTESTATION_EXPIRED")
        report=self.inspect(egress_validator=fail)
        self.assertEqual(report["m03_egress"]["status"],
                         "M03_EGRESS_POLICY_UNVERIFIED_OR_EXPIRED")
        self.assertFalse(report["ready_for_nonlive_scanner_canary"])

    def test_invalid_sha_unverified_not_attestation(self):
        report=self.inspect(source_sha_reader=lambda p: "fake_sha")
        self.assertIsNone(report["original_v2_git_head_sha"])
        self.assertFalse(report["ready_for_nonlive_scanner_canary"])

    def test_old_v1_or_secondary_checkout_not_inspected(self):
        other=self.home/"indodax-ai-bot-v2-m03-runtime"
        other.mkdir()
        with self.assertRaisesRegex(ValueError,"M03_M05_ONLY_CANONICAL_ORIGINAL_V2"):
            self.inspect(root=other)
        with self.assertRaisesRegex(ValueError,"M03_M05_ONLY_CANONICAL_ORIGINAL_V2"):
            self.inspect(root=self.home/"crypto-bot")

    def test_missing_btc_db_never_claims_operational(self):
        self.ws.unlink()
        report=self.inspect()
        self.assertFalse(report["btc_ws_database_file_present"])
        self.assertFalse(report["ready_for_nonlive_scanner_canary"])

    def test_stale_btc_status_does_not_create_heartbeat(self):
        doc=json.loads(self.status.read_text())
        doc["last_completed_batch_epoch"]=NOW-7200
        self.status.write_text(json.dumps(doc))
        self.status.chmod(0o600)
        report=self.inspect()
        self.assertIn("BTC_WS_BATCH_STALE",report["btc_ws_alert_codes"])
        self.assertFalse(report["scanner_recommendations_actually_delivered_verified"])

    def test_owner_db_wal_present_refuses_stale_counts(self):
        wal=Path(str(self.db)+"-wal")
        wal.write_bytes(b"SYNTHETIC_WAL_NO_READ")
        try:
            owner=self.inspect()["m05_owner"]
            self.assertEqual(owner["m05_readonly_inspection_status"],
                             "WAL_PRESENT_UNVERIFIED")
            self.assertIsNone(owner["m05_queued_advisories"])
        finally:
            wal.unlink()

    def test_owner_db_wrong_mode_rejected(self):
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE m05_config SET mode='AUTO'")
        owner=self.inspect()["m05_owner"]
        self.assertEqual(owner["m05_readonly_inspection_status"],
                         "OWNER_OR_MODE_UNVERIFIED")
        self.assertFalse(owner["owner_ledger_source_identity_verified"])

    def test_owner_token_permission_only_metadata_rejected(self):
        self.token.chmod(0o644)
        owner=self.inspect()["m05_owner"]
        self.assertFalse(owner["telegram_token_file_permissions_ok"])
        self.assertFalse(self.inspect()["ready_for_nonlive_scanner_canary"])

    def test_symlink_owner_env_denied(self):
        real=self.owner_env.with_name("real.env")
        self.owner_env.rename(real)
        self.owner_env.symlink_to(real)
        self.assertFalse(self.inspect()["m05_owner"]["owner_service_env_permissions_ok"])

    def test_wrong_owner_db_permissions_denied(self):
        self.db.chmod(0o644)
        owner=self.inspect()["m05_owner"]
        self.assertFalse(owner["owner_db_permissions_ok"])
        self.assertFalse(owner["owner_ledger_source_identity_verified"])

    def test_no_source_or_sqlite_bytes_change(self):
        initial={
            p:sha256(p.read_bytes()).hexdigest()
            for p in (self.db,self.owner_env,self.token,self.ws,self.status)
        }
        self.inspect()
        self.assertEqual(initial,{
            p:sha256(p.read_bytes()).hexdigest() for p in initial
        })
        for suffix in ("-wal","-shm","-journal"):
            self.assertFalse(Path(str(self.db)+suffix).exists())

    def test_no_network_or_systemctl_side_effects(self):
        with (patch("socket.socket",side_effect=AssertionError("NETWORK_DENIED")),
              patch("subprocess.run",
                    side_effect=AssertionError("SHELL_DENIED"))):
            result=self.inspect()
        self.assertFalse(result["m03_timer_started_or_modified"])
        self.assertFalse(result["m05_timer_started_or_modified"])

    def test_bad_source_template_detected_without_disabling_services(self):
        p=self.deploy/"indodax-v2-m03-mainpath.service"
        p.write_text(p.read_text().replace(
            "indodax-ai-bot-v2/.venv",
            "indodax-ai-bot-v2-m03-runtime/.venv"))
        r=self.inspect()
        self.assertFalse(r["m03_advisory_source_templates_nonlive"])
        self.assertFalse(r["ready_for_nonlive_scanner_canary"])

    def test_private_file_metadata_validation(self):
        self.assertTrue(_safe_private_file(self.token,max_bytes=256))
        self.token.chmod(0o666)
        self.assertFalse(_safe_private_file(self.token,max_bytes=256))

    def test_missing_owner_env_or_advisory_is_unverified(self):
        self.owner_env.unlink()
        r=self.inspect()
        self.assertFalse(r["m05_owner"]["owner_service_env_permissions_ok"])
        self.assertFalse(r["ready_for_nonlive_scanner_canary"])


if __name__=="__main__":
    unittest.main()
