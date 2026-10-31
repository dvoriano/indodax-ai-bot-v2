"""M04-V: existing PUBLIC bulk CLI connects Sumopod safely, OFF by default.

No paid requests or Indodax private/orders. The source gate stays REJECT.
"""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
import unittest

from indodax_ai_bot.__main__ import main
from test_m03g_bulk import FakeBulkMarket, fake_screen

GOOD_ENV = {
    "SUMOPOD_API_KEY": "test-not-a-real-secret",
    "SUMOPOD_MODEL_MARKET": "test-model-market",
    "SUMOPOD_MODEL_RISK": "test-model-risk",
    "SUMOPOD_MODEL_STRATEGY": "test-model-strategy",
    "INDODAX_TRADING_ENABLED": "NO",
    "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
    "INDODAX_EXECUTION_MODE": "DRY_RUN",
}


class M04VCLIShadowWiringTests(unittest.TestCase):
    def run_public_bulk(self, *, flag, env=GOOD_ENV):
        with TemporaryDirectory() as td:
            out, err = StringIO(), StringIO()
            args = ["--bulk-deep-once", "--db", str(Path(td) / "decisions.sqlite3")]
            if flag:
                args.append("--sumopod-shadow")
            with patch.dict("os.environ", env, clear=True):
                with patch("indodax_ai_bot.orchestrator.DailyPairScanner") as scanner:
                    scanner.return_value.scan.return_value = fake_screen()
                    with patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket",
                               return_value=FakeBulkMarket()):
                        with redirect_stdout(out), redirect_stderr(err):
                            code = main(args)
            return code, json.loads(out.getvalue()), err.getvalue()

    def test_default_bulk_cli_never_constructs_paid_adapter(self):
        with patch("indodax_ai_bot.__main__.SumopodChatAdapter.configured_from_env") as adapter:
            rc, result, err = self.run_public_bulk(flag=False, env={})
        adapter.assert_not_called()
        self.assertEqual(rc, 0)
        self.assertEqual(err, "")
        self.assertFalse(result["sumopod_shadow"]["configured"])
        self.assertFalse(result["sumopod_shadow"]["paid_network_allowed"])
        self.assertEqual(result["ai_shadow"]["completed_roles"], [])
        self.assertEqual(result["final_decision"], "REJECT")

    def test_explicit_three_model_adapter_is_connected_but_never_called(self):
        with patch("indodax_ai_bot.sumopod_provider._post",
                   side_effect=AssertionError("NO_SUMOPOD_NETWORK")) as post:
            rc, result, err = self.run_public_bulk(flag=True)
        self.assertEqual(rc, 0)
        self.assertEqual(err, "")
        self.assertTrue(result["sumopod_shadow"]["configured"])
        self.assertFalse(result["sumopod_shadow"]["paid_network_allowed"])
        self.assertFalse(result["sumopod_shadow"]["g1_issuer_connected"])
        self.assertEqual(result["sumopod_shadow"]["role_usage_events"], 0)
        self.assertEqual(result["ai_shadow"]["completed_roles"], [])
        self.assertEqual(result["final_decision"], "REJECT")
        self.assertFalse(result["execution_allowed"])
        self.assertNotIn(GOOD_ENV["SUMOPOD_API_KEY"], json.dumps(result))
        post.assert_not_called()

    def test_owner_glm53_three_role_selection_real_cli_still_rejects_untrusted_source(self):
        # Real CLI -> original app/orchestrator -> original M04 ThreeRoleShadow
        # with fake public market and fake-only model boundary. NO network.
        safe = {
            "SUMOPOD_API_KEY": "test-no-real-provider-key",
            "INDODAX_TRADING_ENABLED": "NO",
            "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
            "INDODAX_EXECUTION_MODE": "DRY_RUN",
        }
        with patch("indodax_ai_bot.sumopod_provider._post",
                   side_effect=AssertionError("NO_PAID_NETWORK")) as post:
            rc, result, err = self.run_public_bulk(flag=True, env=safe)
        self.assertEqual(rc, 0, err)
        self.assertTrue(result["sumopod_shadow"]["configured"])
        self.assertEqual(result["sumopod_shadow"]["role_usage_events"], 0)
        self.assertEqual(result["ai_shadow"]["completed_roles"], [])
        self.assertEqual(result["final_decision"], "REJECT")
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["sumopod_shadow"]["paid_network_allowed"])
        post.assert_not_called()

    def test_malformed_model_config_fails_without_disclosing_key(self):
        invalid = dict(GOOD_ENV)
        invalid["SUMOPOD_MODEL_RISK"] = ""
        with patch("indodax_ai_bot.sumopod_provider._post",
                   side_effect=AssertionError("NO_SUMOPOD_NETWORK")) as post:
            with TemporaryDirectory() as td:
                out, err = StringIO(), StringIO()
                with patch.dict("os.environ", invalid, clear=True):
                    with redirect_stdout(out), redirect_stderr(err):
                        rc = main([
                            "--bulk-deep-once", "--sumopod-shadow",
                            "--db", str(Path(td) / "decisions.sqlite3"),
                        ])
        self.assertEqual(rc, 2)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("INVALID_SUMOPOD_MODEL_ID", err.getvalue())
        self.assertNotIn(GOOD_ENV["SUMOPOD_API_KEY"], err.getvalue())
        post.assert_not_called()

    def test_sumopod_shadow_not_accepted_for_unrelated_health_commands(self):
        with patch("indodax_ai_bot.sumopod_provider._post",
                   side_effect=AssertionError("NO_SUMOPOD_NETWORK")) as post:
            out, err = StringIO(), StringIO()
            with patch.dict("os.environ", GOOD_ENV, clear=True):
                with redirect_stdout(out), redirect_stderr(err):
                    rc = main(["--health", "--sumopod-shadow"])
        self.assertEqual(rc, 2)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("SUMOPOD_SHADOW_REQUIRES_BULK_DEEP_ONCE", err.getvalue())
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
