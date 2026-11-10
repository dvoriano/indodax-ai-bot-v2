"""M10-07 original CLI M03/M04/M05 read-only runtime join, no HTTP."""
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from indodax_ai_bot.m10_07_original_mainpath_readonly import (
    inspect_m10_07_original_mainpath_readonly,
)

NOW = 1793933200


def row(role, active, enabled, trusted=True):
    return {
        "role": role,
        "observed_load": "loaded",
        "observed_active": active,
        "observed_enabled": enabled,
        "v2_user_fragment_path_matches": trusted,
        "unit_needs_daemon_reload": "no",
    }


def units(*, scanner=True, owner=True, timer=False):
    return {"units": [
        row("m03_scanner", "inactive", "static"),
        row("m03_scanner_timer", "active" if scanner else "inactive",
            "enabled" if scanner else "disabled"),
        row("m05_telegram", "active" if owner else "inactive",
            "enabled" if owner else "disabled"),
        row("m05_telegram_timer", "active" if timer else "inactive",
            "enabled" if timer else "disabled"),
    ]}


def provider(*, configured=True):
    return {
        "configured_models_by_role": {
            role: "owner-selected-model" if configured else None
            for role in ("MARKET", "RISK", "STRATEGY")
        },
        "sumopod_key_present_nonempty": configured,
    }


class OriginalM1007RuntimeReadiness(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        parent = Path(temp.name) / "state"
        parent.mkdir(mode=0o700)
        d = parent / "m05-telegram"
        d.mkdir(mode=0o700)
        self.db = d / "owner.sqlite3"
        with sqlite3.connect(self.db) as con:
            con.execute("""
                CREATE TABLE m05_market_advisory_outbox (
                    closed_1m_epoch INTEGER NOT NULL,
                    delivery_state TEXT NOT NULL
                )
            """)
        os.chmod(self.db, 0o600)
        runtime = parent / "runtime"
        runtime.mkdir(mode=0o700)
        self.m03db = runtime / "decisions.sqlite3"
        with sqlite3.connect(self.m03db) as con:
            con.execute("""
                CREATE TABLE public_ticker_minute_samples (
                    observed_epoch INTEGER NOT NULL
                )
            """)
        os.chmod(self.m03db, 0o600)

    def sent(self, close):
        with sqlite3.connect(self.db) as con:
            con.execute(
                "INSERT INTO m05_market_advisory_outbox VALUES (?, 'SENT')",
                (close,),
            )

    def sample(self, observed_epoch):
        with sqlite3.connect(self.m03db) as con:
            con.execute(
                "INSERT INTO public_ticker_minute_samples VALUES (?)",
                (observed_epoch,),
            )

    def inspect(self, u=None, p=None):
        return inspect_m10_07_original_mainpath_readonly(
            units_report=units() if u is None else u,
            provider_report=provider() if p is None else p,
            owner_db=self.db, m03_db=self.m03db, now_epoch=NOW,
        )

    def test_real_source_outbox_and_roles_still_nonlive(self):
        self.sent(NOW - 60)
        self.sample(NOW - 10)
        report = self.inspect()
        self.assertTrue(report["m03_m05_source_runtime_metadata_join_observed"])
        self.assertTrue(report["m03_original_service_not_failed_metadata_observed"])
        self.assertTrue(report["m03_original_market_sample_recent_observed"])
        self.assertEqual(report["m03_original_last_sample_age_seconds"], 10)
        self.assertFalse(report["m03_source_freshness_is_independent_provenance"])
        self.assertTrue(report["m04_local_models_and_credential_present"])
        self.assertEqual(report["last_sent_public_advisory_age_seconds"], 60)
        self.assertFalse(report["g1_independent_issuer_verified"])
        self.assertFalse(report["three_paid_ai_role_responses_verified"])
        self.assertFalse(report["m10_07_full_acceptance"])
        self.assertFalse(report["execution_allowed"])
        self.assertEqual(report["billable_provider_calls"], 0)

    def test_failed_m03_service_vetoes_join_despite_fresh_sample_and_owner_sent(self):
        self.sent(NOW - 30)
        self.sample(NOW - 10)
        snapshot = units()
        for item in snapshot["units"]:
            if item["role"] == "m03_scanner":
                item["observed_active"] = "failed"
        report = self.inspect(snapshot)
        self.assertTrue(report["m03_original_timer_active_metadata_observed"])
        self.assertFalse(report["m03_original_service_not_failed_metadata_observed"])
        self.assertTrue(report["m03_original_market_sample_recent_observed"])
        self.assertTrue(report["m03_m05_recent_sent_public_advisory_observed"])
        self.assertFalse(report["m03_m05_source_runtime_metadata_join_observed"])

    def test_no_m03_service_metadata_cannot_be_treated_as_healthy(self):
        self.sent(NOW - 30)
        self.sample(NOW - 10)
        snapshot = units()
        snapshot["units"] = [
            r for r in snapshot["units"] if r["role"] != "m03_scanner"
        ]
        report = self.inspect(snapshot)
        self.assertFalse(report["m03_original_service_not_failed_metadata_observed"])
        self.assertFalse(report["m03_m05_source_runtime_metadata_join_observed"])

    def test_m03_without_fresh_sample_does_not_claim_join(self):
        self.sent(NOW - 30)
        report = self.inspect()
        self.assertTrue(report["m03_original_market_db_trusted"])
        self.assertFalse(report["m03_original_market_sample_recent_observed"])
        self.assertFalse(report["m03_m05_source_runtime_metadata_join_observed"])
        self.sample(NOW - 248519)
        report = self.inspect()
        self.assertGreater(report["m03_original_last_sample_age_seconds"], 90)
        self.assertFalse(report["m03_m05_source_runtime_metadata_join_observed"])

    def test_future_clock_replayed_m03_sample_rejected(self):
        self.sent(NOW - 30)
        self.sample(NOW + 1)
        report = self.inspect()
        self.assertFalse(report["m03_original_market_sample_recent_observed"])
        self.assertFalse(report["m03_m05_source_runtime_metadata_join_observed"])

    def test_m03_symlink_or_nonprivate_sample_db_rejected(self):
        self.sent(NOW - 30)
        self.sample(NOW - 10)
        alias = self.m03db.with_name("alias.sqlite3")
        alias.symlink_to(self.m03db)
        report = inspect_m10_07_original_mainpath_readonly(
            units_report=units(), provider_report=provider(),
            owner_db=self.db, m03_db=alias, now_epoch=NOW,
        )
        self.assertFalse(report["m03_original_market_db_trusted"])
        self.assertFalse(report["m03_m05_source_runtime_metadata_join_observed"])
        self.m03db.chmod(0o644)
        report = self.inspect()
        self.assertFalse(report["m03_original_market_db_trusted"])
        self.assertFalse(report["m03_m05_source_runtime_metadata_join_observed"])

    def test_no_actual_sent_receipt_never_joins(self):
        report = self.inspect()
        self.assertFalse(report["m03_m05_source_runtime_metadata_join_observed"])
        self.assertTrue(report["m05_original_owner_db_trusted"])

    def test_stale_owner_receipt_not_online_evidence(self):
        self.sent(NOW - 5000)
        report = self.inspect()
        self.assertFalse(report["m03_m05_recent_sent_public_advisory_observed"])

    def test_inactive_scanner_does_not_claim_online(self):
        self.sent(NOW - 60)
        report = self.inspect(units(scanner=False))
        self.assertFalse(report["m03_m05_source_runtime_metadata_join_observed"])

    def test_competing_m05_timer_is_not_one_receiver(self):
        self.sent(NOW - 60)
        report = self.inspect(units(timer=True))
        self.assertFalse(report["m05_original_receiver_active_metadata_observed"])
        self.assertFalse(report["m03_m05_source_runtime_metadata_join_observed"])

    def test_key_absent_is_not_three_paid_roles(self):
        self.sent(NOW - 60)
        report = self.inspect(p=provider(configured=False))
        self.assertFalse(report["m04_local_models_and_credential_present"])
        self.assertFalse(report["three_paid_ai_role_responses_verified"])

    def test_unsafe_symlink_owner_db_fails_closed(self):
        link = self.db.with_name("alias.sqlite3")
        link.symlink_to(self.db)
        report = inspect_m10_07_original_mainpath_readonly(
            units_report=units(), provider_report=provider(),
            owner_db=link, now_epoch=NOW,
        )
        self.assertFalse(report["m05_original_owner_db_trusted"])

    def test_original_cli_invocation_no_network_no_journal(self):
        from indodax_ai_bot import __main__ as cli
        with (patch.object(cli, "_m04_original_provider_scope",
                           return_value=({}, "NO_V2_LOCAL_CONFIG", "NOT_PRESENT")),
              patch.object(cli, "inspect_provider_configuration_offline",
                           return_value=provider(configured=False)),
              patch.object(cli, "inspect_m10_07_original_mainpath_readonly",
                           return_value=self.inspect()),
              patch.object(cli.Settings, "require_m01_safety", return_value=None),
              patch.object(cli, "DecisionJournal", side_effect=AssertionError(
                  "CLI_MUST_NOT_CREATE_ORDER_JOURNAL")),
              patch.object(cli, "SumopodChatAdapter", side_effect=AssertionError(
                  "CLI_MUST_NOT_CALL_PROVIDER"))):
            sink = io.StringIO()
            with redirect_stdout(sink):
                status = cli.main(["--m10-07-mainpath-readiness-once"])
        self.assertEqual(status, 0)
        data = json.loads(sink.getvalue())
        self.assertFalse(data["m10_07_original_mainpath"]["execution_allowed"])
        self.assertEqual(data["m10_07_original_mainpath"]["m04_provider_source_kind"],
                         "NO_V2_LOCAL_CONFIG")


if __name__ == "__main__":
    unittest.main()
