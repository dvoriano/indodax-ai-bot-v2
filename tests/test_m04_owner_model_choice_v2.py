"""Original V2 M04 owner model-switch only: no key touch, prices or paid AI."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from indodax_ai_bot.m04_owner_config import (
    M04OwnerConfigDenied, stage_owner_default_models, owner_provider_source,
    change_owner_models_explicitly,
)
from scripts import m04_owner_select_models as cli


class OriginalOwnerModelSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="m04-owner-models-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / ".config/indodax-ai-bot-v2"
        root.mkdir(parents=True, mode=0o700)
        root.chmod(0o700)
        self.folder = root / "m04"
        self.config = self.folder / "provider.json"
        self.key = self.folder / "sumopod.api-key"
        stage_owner_default_models(
            directory=self.folder, config=self.config, key_file=self.key)
        self.key.write_text("private-do-not-print\n", encoding="ascii")
        self.key.chmod(0o600)

    def change(self, *, market="glm-5.3-flash", risk="deepseek-v4.1-flash:netra",
               strategy="mimo-v2.6-flash"):
        return change_owner_models_explicitly(
            {"MARKET": market, "RISK": risk, "STRATEGY": strategy},
            directory=self.folder, config=self.config, key_file=self.key,
        )

    def test_changes_model_ids_without_reading_or_rewriting_private_key(self):
        before_key = self.key.read_bytes()
        with patch("indodax_ai_bot.m04_owner_config._private_read",
                   wraps=__import__("indodax_ai_bot.m04_owner_config", fromlist=["_private_read"])._private_read) as reader:
            report = self.change()
            self.assertFalse(any(call.args[0] == self.key for call in reader.call_args_list))
        self.assertTrue(report["changed"])
        self.assertEqual(report["distinct_model_count"], 3)
        self.assertFalse(report["provider_http_attempted"])
        self.assertFalse(report["paid_ai_go_authorized"])
        self.assertFalse(report["credential_file_read_or_changed"])
        self.assertFalse(report["trading_enabled"])
        self.assertEqual(before_key, self.key.read_bytes())
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("private-do-not-print", json.dumps(report))
        src, status = owner_provider_source(
            directory=self.folder, config=self.config, key_file=self.key,
        )
        self.assertEqual(status, "M04_V2_KEY_FILE_PRESENT_LOCAL_ONLY")
        self.assertEqual(src["SUMOPOD_MODEL_RISK"], "deepseek-v4.1-flash:netra")
        self.assertEqual(src["SUMOPOD_MODEL_STRATEGY"], "mimo-v2.6-flash")

    def test_repeat_exact_selection_is_idempotent(self):
        self.change()
        previous = self.config.read_bytes()
        out = self.change()
        self.assertFalse(out["changed"])
        self.assertEqual(self.config.read_bytes(), previous)

    def test_owner_can_choose_one_shared_model_three_independent_roles(self):
        result = self.change(market="glm-5.3-flash",
                             risk="glm-5.3-flash",strategy="glm-5.3-flash")
        self.assertEqual(result["distinct_model_count"], 1)
        self.assertFalse(result["changed"])

    def test_invalid_model_ids_or_missing_roles_refuse_without_file_changes(self):
        before = self.config.read_bytes()
        for invalid in ("model with spaces", "", "x" * 129, "../model"):
            with self.subTest(invalid=invalid), self.assertRaises(
                M04OwnerConfigDenied
            ):
                self.change(market=invalid)
            self.assertEqual(self.config.read_bytes(), before)
        with self.assertRaisesRegex(
            M04OwnerConfigDenied, "M04_V2_MODEL_CHANGE_REQUIRES_THREE_ROLES"
        ):
            change_owner_models_explicitly(
                {"MARKET":"other"},directory=self.folder,
                config=self.config,key_file=self.key)
        self.assertEqual(self.config.read_bytes(), before)

    def test_unexpected_existing_schema_is_not_overwritten(self):
        old = self.config.read_bytes()
        self.config.write_text('{"models":{"MARKET":"bad"}}')
        with self.assertRaisesRegex(
            M04OwnerConfigDenied, "M04_V2_MODEL_CHANGE_CONFIG_INVALID"
        ):
            self.change()
        self.assertEqual(self.config.read_bytes(), b'{"models":{"MARKET":"bad"}}')
        self.config.write_bytes(old)

    def test_symlink_config_refused_even_when_replacement_would_work(self):
        existing = self.config.read_bytes()
        original = self.folder / "original.json"
        original.write_bytes(existing)
        original.chmod(0o600)
        self.config.unlink()
        self.config.symlink_to(original)
        with self.assertRaises(M04OwnerConfigDenied):
            self.change()
        self.assertEqual(original.read_bytes(), existing)

    def test_cli_needs_explicit_owner_approval(self):
        out = StringIO()
        with redirect_stdout(out):
            code=cli.main([
                "--market","glm-5.3-flash",
                "--risk","glm-5.3-flash",
                "--strategy","glm-5.3-flash",
            ])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out.getvalue())["reason"],
                         "M04_MODEL_CHANGE_OWNER_APPROVAL_REQUIRED")

    def test_cli_explicit_approval_uses_original_selected_models(self):
        out=StringIO()
        with patch.object(cli,"change_owner_models_explicitly",
                          return_value={"status":"M04_MODEL_TEST"}) as change:
            with redirect_stdout(out):
                code=cli.main([
                    "--market","m1", "--risk","m2", "--strategy","m2",
                    "--owner-approve-model-selection",
                ])
        self.assertEqual(code, 0)
        change.assert_called_once_with(
            {"MARKET":"m1","RISK":"m2","STRATEGY":"m2"})
        self.assertEqual(json.loads(out.getvalue())["status"],"M04_MODEL_TEST")


if __name__ == "__main__":
    unittest.main()
