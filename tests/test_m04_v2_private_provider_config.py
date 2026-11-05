"""Original V2 M04 protected local config, no HTTP, keys, or trade tests."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from indodax_ai_bot.__main__ import main as original_cli
from indodax_ai_bot.m04_owner_config import (
    M04OwnerConfigDenied,
    owner_provider_source,
    stage_owner_default_models,
)

KEY = "do-not-log-v2-local-test-secret"
SAFE = {
    "INDODAX_TRADING_ENABLED": "NO",
    "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
    "INDODAX_EXECUTION_MODE": "DRY_RUN",
}


class M04V2LocalPrivateSourceTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix="original-v2-m04-secure-")
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.root = self.home / ".config" / "indodax-ai-bot-v2"
        self.root.mkdir(parents=True, mode=0o700)
        self.root.chmod(0o700)
        self.directory = self.root / "m04"
        self.config = self.directory / "provider.json"
        self.key_file = self.directory / "sumopod.api-key"

    def stage(self):
        return stage_owner_default_models(
            directory=self.directory, config=self.config,
            key_file=self.key_file,
        )

    def load(self):
        return owner_provider_source(
            directory=self.directory, config=self.config,
            key_file=self.key_file,
        )

    def cli(self, *, owner_loader=None, environment=None):
        out, err = StringIO(), StringIO()
        with patch.dict(os.environ, {**SAFE, **(environment or {})}, clear=True):
            with patch(
                "indodax_ai_bot.__main__.owner_provider_source",
                return_value=owner_loader,
            ) as read:
                with patch(
                    "indodax_ai_bot.sumopod_provider._post",
                    side_effect=AssertionError("NEVER_USE_PAID_HTTP"),
                ) as post:
                    with redirect_stdout(out), redirect_stderr(err):
                        code = original_cli(["--m04-provider-readiness-once"])
                    post.assert_not_called()
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(code, 0)
        return json.loads(out.getvalue())["m04_provider_readiness"], read

    def test_nonsecret_default_stage_is_idempotent_and_private(self):
        first = self.stage()
        self.assertTrue(first["created_new_config"])
        self.assertFalse(first["key_file_created"])
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(set(first["role_models"].values()), {"glm-5.3-flash"})
        self.assertFalse(self.key_file.exists())
        self.assertFalse(self.stage()["created_new_config"])
        source, status = self.load()
        self.assertEqual(source["SUMOPOD_MODEL_RISK"], "glm-5.3-flash")
        self.assertEqual(source["SUMOPOD_API_KEY"], "")
        self.assertEqual(status, "M04_V2_KEY_FILE_MISSING")

    def test_private_existing_key_is_read_without_ever_printing(self):
        self.stage()
        self.key_file.write_text(KEY + "\n", encoding="ascii")
        self.key_file.chmod(0o600)
        source, status = self.load()
        self.assertEqual(source["SUMOPOD_API_KEY"], KEY)
        self.assertEqual(status, "M04_V2_KEY_FILE_PRESENT_LOCAL_ONLY")
        report, read = self.cli(owner_loader=(source, status))
        read.assert_called_once()
        self.assertEqual(report["config_source"], "V2_PROTECTED_LOCAL_FILE")
        self.assertEqual(report["configured_distinct_model_count"], 1)
        self.assertTrue(report["sumopod_key_present_nonempty"])
        self.assertFalse(report["provider_http_attempted"])
        self.assertFalse(report["paid_ai_go_authorized"])
        self.assertFalse(report["three_real_role_responses_observed"])
        self.assertNotIn(KEY, json.dumps(report))

    def test_environment_has_explicit_priority_not_mixed_with_key_file(self):
        source = {"SUMOPOD_API_KEY": KEY}
        report, read = self.cli(
            owner_loader=(source, "M04_V2_KEY_FILE_PRESENT_LOCAL_ONLY"),
            environment={"SUMOPOD_MODEL_MARKET": "market-only"},
        )
        read.assert_not_called()
        self.assertEqual(report["config_source"], "PROCESS_ENV")
        self.assertFalse(report["sumopod_key_present_nonempty"])
        self.assertIsNone(report["configured_models_by_role"]["RISK"])

    def test_symlink_or_world_readable_secret_is_not_accepted(self):
        self.stage()
        external = self.home / "other-file"
        external.write_text(KEY)
        external.chmod(0o600)
        self.key_file.symlink_to(external)
        with self.assertRaisesRegex(
            M04OwnerConfigDenied, "M04_V2_CONFIG_FILE_UNAVAILABLE"
        ):
            self.load()
        self.key_file.unlink()
        self.key_file.write_text(KEY)
        self.key_file.chmod(0o644)
        with self.assertRaisesRegex(
            M04OwnerConfigDenied, "M04_V2_CONFIG_FILE_UNTRUSTED"
        ):
            self.load()
        self.key_file.chmod(0o600)
        self.assertEqual(self.load()[1], "M04_V2_KEY_FILE_PRESENT_LOCAL_ONLY")

    def test_invalid_config_is_refused_not_replaced(self):
        self.stage()
        original = self.config.read_bytes()
        altered = json.loads(original)
        altered["models"]["STRATEGY"] = "INVALID MODEL STRING"
        self.config.write_text(json.dumps(altered))
        self.config.chmod(0o600)
        with self.assertRaisesRegex(
            M04OwnerConfigDenied, "M04_V2_CONFIG_MODELS_INVALID"
        ):
            self.load()
        with self.assertRaisesRegex(
            M04OwnerConfigDenied, "M04_V2_EXISTING_CONFIG_UNREVIEWED"
        ):
            self.stage()

    def test_missing_file_keeps_legacy_no_config_status(self):
        self.assertIsNone(self.load())
        report, read = self.cli(owner_loader=None)
        read.assert_called_once()
        self.assertEqual(report["config_source"], "NO_V2_LOCAL_CONFIG")
        self.assertFalse(report["sumopod_key_present_nonempty"])
        self.assertEqual(report["configured_distinct_model_count"], 0)


if __name__ == "__main__":
    unittest.main()
