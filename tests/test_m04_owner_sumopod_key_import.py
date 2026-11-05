"""Original V2 M04 hidden-TTY key import unit tests, no network/secret logs."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from indodax_ai_bot.m04_owner_config import (
    M04OwnerConfigDenied,
    owner_provider_source,
    stage_owner_default_models,
)
from scripts.m04_install_owner_sumopod_key import (
    import_owner_m04_key, main,
)

SECRET = "unit-test-secret-never-show"


class M04V2OwnerKeyImportTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory(prefix="v2-m04-key-unit-")
        self.addCleanup(folder.cleanup)
        root = Path(folder.name) / ".config/indodax-ai-bot-v2"
        root.mkdir(parents=True, mode=0o700)
        root.chmod(0o700)
        self.directory = root / "m04"
        self.config = self.directory / "provider.json"
        self.key = self.directory / "sumopod.api-key"

    def stage(self):
        return stage_owner_default_models(
            directory=self.directory, config=self.config, key_file=self.key,
        )

    def install(self, candidate=SECRET):
        return import_owner_m04_key(
            candidate, directory=self.directory, key_file=self.key,
        )

    def test_one_owner_0600_key_staged_preserving_three_model_config(self):
        self.stage()
        model_before = self.config.read_bytes()
        out = self.install()
        self.assertEqual(out["status"], "M04_V2_LOCAL_SUMOPOD_KEY_INSTALLED_UNVERIFIED")
        self.assertTrue(out["local_key_file_present"])
        self.assertFalse(out["paid_provider_network_used"])
        self.assertFalse(out["paid_ai_go_authorized"])
        self.assertFalse(out["g1_authenticated"])
        self.assertFalse(out["trading_enabled"])
        self.assertEqual(self.key.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.config.read_bytes(), model_before)
        src, status = owner_provider_source(
            directory=self.directory, config=self.config, key_file=self.key,
        )
        self.assertEqual(status, "M04_V2_KEY_FILE_PRESENT_LOCAL_ONLY")
        self.assertEqual(src["SUMOPOD_API_KEY"], SECRET)
        self.assertEqual(src["SUMOPOD_MODEL_MARKET"], "glm-5.3-flash")
        self.assertNotIn(SECRET, json.dumps(out))

    def test_never_overwrite_existing_owner_key(self):
        self.stage()
        self.install()
        before = self.key.read_bytes()
        with self.assertRaisesRegex(
            M04OwnerConfigDenied, "M04_V2_KEY_IMPORT_ALREADY_PRESENT"
        ):
            self.install("different-api-key")
        self.assertEqual(self.key.read_bytes(), before)

    def test_without_original_v2_model_slot_must_refuse(self):
        with self.assertRaisesRegex(
            M04OwnerConfigDenied, "M04_V2_KEY_IMPORT_MODELS_NOT_STAGED"
        ):
            self.install()
        self.assertFalse(self.key.exists())

    def test_invalid_key_format_does_not_create_file(self):
        self.stage()
        for value in ("", "has whitespace", "key-with-newline\n", "é", "x" * 513):
            with self.assertRaisesRegex(
                M04OwnerConfigDenied, "M04_V2_KEY_IMPORT_INVALID_SECRET_FORMAT"
            ):
                self.install(value)
            self.assertFalse(self.key.exists())

    def test_existing_symlink_not_overwritten(self):
        self.stage()
        outside = self.directory / "another-secret"
        outside.write_text("unrelated", encoding="ascii")
        outside.chmod(0o600)
        self.key.symlink_to(outside)
        with self.assertRaises(M04OwnerConfigDenied):
            self.install()
        self.assertEqual(outside.read_text(), "unrelated")

    def test_non_interactive_cli_refuses_without_reading_secret(self):
        out = StringIO()
        with patch("sys.stdin.isatty", return_value=False), \
             patch("getpass.getpass", side_effect=AssertionError("NO_PROMPT")), \
             redirect_stdout(out):
            rc = main()
        self.assertEqual(rc, 2)
        self.assertIn("M04_V2_KEY_IMPORT_INTERACTIVE_TERMINAL_REQUIRED", out.getvalue())
        self.assertNotIn(SECRET, out.getvalue())

    def test_disallows_arbitrary_file_path(self):
        self.stage()
        with self.assertRaisesRegex(
            M04OwnerConfigDenied, "M04_V2_KEY_IMPORT_INVALID_PATH"
        ):
            import_owner_m04_key(
                SECRET, directory=self.directory,
                key_file=self.directory / "other.api-key",
            )
        self.assertFalse(self.key.exists())


if __name__ == "__main__":
    unittest.main()
