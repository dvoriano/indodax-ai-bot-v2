"""M04 original CLI provider readiness: zero network, no ledger or payment."""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.sumopod_provider import inspect_provider_configuration_offline

SAFE = {
    "INDODAX_TRADING_ENABLED": "NO",
    "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
    "INDODAX_EXECUTION_MODE": "DRY_RUN",
}
SAMPLE_KEY = "private-test-key-do-not-print"


class M04OriginalProviderReadinessTests(unittest.TestCase):
    def cli(self, config: dict[str, str]):
        with TemporaryDirectory() as td:
            db = Path(td) / "journal-must-not-be-created.sqlite3"
            stdout, stderr = StringIO(), StringIO()
            with patch.dict(os.environ, {**SAFE, **config}, clear=True):
                with patch(
                    "indodax_ai_bot.sumopod_provider._post",
                    side_effect=AssertionError("PAID_CALL_FORBIDDEN"),
                ) as paid:
                    with patch(
                        "indodax_ai_bot.__main__.DecisionJournal",
                        side_effect=AssertionError("JOURNAL_CREATION_FORBIDDEN"),
                    ) as journal:
                        with redirect_stdout(stdout), redirect_stderr(stderr):
                            rc = main(["--m04-provider-readiness-once", "--db", str(db)])
                    paid.assert_not_called()
                    journal.assert_not_called()
            self.assertFalse(db.exists())
            self.assertEqual(stderr.getvalue(), "")
            return rc, json.loads(stdout.getvalue())

    def test_safe_absent_credential_and_models(self):
        rc, report = self.cli({})
        self.assertEqual(rc, 0)
        a = report["m04_provider_readiness"]
        self.assertEqual(a["status"], "M04_PROVIDER_LOCAL_CONFIG_INCOMPLETE")
        self.assertEqual(a["configured_distinct_model_count"], 0)
        self.assertFalse(a["sumopod_key_present_nonempty"])
        self.assertFalse(a["provider_http_attempted"])
        self.assertEqual(a["billable_provider_calls"], 0)
        self.assertEqual(report["execution_allowed"], False)
        self.assertIsNone(report["final_decision"])

    def test_three_models_present_but_no_paid_go_or_issuer(self):
        rc, report = self.cli({
            "SUMOPOD_API_KEY": SAMPLE_KEY,
            "SUMOPOD_MODEL_MARKET": "glm-5.3-flash",
            "SUMOPOD_MODEL_RISK": "deepseek-v4.1-flash:netra",
            "SUMOPOD_MODEL_STRATEGY": "glm-5.3-flash",
            "SUMOPOD_ALLOW_PAID": "YES",
        })
        self.assertEqual(rc, 0)
        a = report["m04_provider_readiness"]
        self.assertEqual(a["status"], "M04_PROVIDER_LOCAL_CONFIG_PRESENT_UNVERIFIED")
        self.assertEqual(a["configured_distinct_model_count"], 2)
        self.assertEqual(a["configured_models_by_role"]["MARKET"], "glm-5.3-flash")
        self.assertFalse(a["actual_provider_model_availability_verified"])
        self.assertFalse(a["actual_provider_pricing_verified"])
        self.assertFalse(a["g1_independent_issuer_verified"])
        self.assertFalse(a["paid_ai_go_authorized"])
        self.assertEqual(a["billable_provider_calls"], 0)
        self.assertNotIn(SAMPLE_KEY, json.dumps(report))
        self.assertFalse(report["execution_allowed"])

    def test_invalid_models_are_redacted_and_never_call_provider(self):
        rc, report = self.cli({
            "SUMOPOD_API_KEY": SAMPLE_KEY,
            "SUMOPOD_MODEL_MARKET": "bad model with spaces",
            "SUMOPOD_MODEL_RISK": "valid-id",
        })
        self.assertEqual(rc, 0)
        a = report["m04_provider_readiness"]
        self.assertEqual(a["configured_models_by_role"]["MARKET"], None)
        self.assertEqual(a["configured_models_by_role"]["STRATEGY"], None)
        self.assertIn("M04_SUMOPOD_ROLE_MODEL_CONFIG_MISSING_OR_INVALID", a["blockers"])
        self.assertNotIn("bad model with spaces", json.dumps(report))
        self.assertNotIn(SAMPLE_KEY, json.dumps(report))

    def test_no_credential_does_not_promote_valid_three_model_config(self):
        report = inspect_provider_configuration_offline({
            "SUMOPOD_MODEL_MARKET": "glm-5.3-flash",
            "SUMOPOD_MODEL_RISK": "glm-5.3-flash",
            "SUMOPOD_MODEL_STRATEGY": "glm-5.3-flash",
        })
        self.assertEqual(report["configured_distinct_model_count"], 1)
        self.assertEqual(report["status"], "M04_PROVIDER_LOCAL_CONFIG_INCOMPLETE")
        self.assertEqual(report["role_contract"], ["MARKET", "RISK", "STRATEGY"])
        self.assertFalse(report["trading_enabled"])


if __name__ == "__main__":
    unittest.main()
