"""Original owner-only Telegram /ai: LOCAL readiness, not paid inference."""
from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from indodax_ai_bot.m04_owner_config import M04OwnerConfigDenied
from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.telegram_owner import OfflineTelegramOwnerInterface

OWNER = 55119008
NOW = 1800000140


class OriginalM04M05TelegramAiStatusTests(unittest.TestCase):
    def setUp(self):
        td = TemporaryDirectory(prefix="m04-telegram-status-")
        self.addCleanup(td.cleanup)
        self.ledger = OwnerDecisionLedger(
            Path(td.name) / "owner.sqlite3", owner_user_id=OWNER)
        self.owner = OfflineTelegramOwnerInterface(self.ledger)

    def read(self, user=OWNER, chat=OWNER, chat_type="private"):
        return self.owner.handle_command(
            user_id=user, chat_id=chat, chat_type=chat_type,
            text="/ai", now_epoch=NOW,
        )

    def test_local_protected_models_but_missing_key_never_claim_online(self):
        local = {
            "SUMOPOD_API_KEY": "",
            "SUMOPOD_MODEL_MARKET": "glm-5.3-flash",
            "SUMOPOD_MODEL_RISK": "deepseek-v4.1-flash:netra",
            "SUMOPOD_MODEL_STRATEGY": "glm-5.3-flash",
        }
        with patch.dict(os.environ, {}, clear=True), patch(
                "indodax_ai_bot.m04_owner_config.owner_provider_source",
                return_value=(local, "M04_V2_KEY_FILE_MISSING")) as owner, patch(
                "indodax_ai_bot.sumopod_provider._post",
                side_effect=AssertionError("NEVER_PAID_HTTP")) as paid:
            result = self.read()
        text = result["text"]
        self.assertIn("V2_PROTECTED_LOCAL_FILE", text)
        self.assertIn("MARKET=glm-5.3-flash", text)
        self.assertIn("API key: NOT_DETECTED", text)
        self.assertIn("online: UNVERIFIED", text)
        self.assertIn("Paid AI invoked: NO", text)
        self.assertFalse(result["execution_allowed"])
        self.assertIsNone(result["final_decision"])
        paid.assert_not_called()
        owner.assert_called_once()

    def test_local_key_present_not_evidence_of_live_role_response(self):
        config = {
            "SUMOPOD_API_KEY": "SECRET_NEVER_SEND_TO_TELEGRAM",
            "SUMOPOD_MODEL_MARKET": "glm-5.3-flash",
            "SUMOPOD_MODEL_RISK": "glm-5.3-flash",
            "SUMOPOD_MODEL_STRATEGY": "glm-5.3-flash",
        }
        with patch.dict(os.environ, {}, clear=True), patch(
                "indodax_ai_bot.m04_owner_config.owner_provider_source",
                return_value=(config, "M04_V2_KEY_FILE_PRESENT_LOCAL_ONLY")):
            result = self.read()
        self.assertIn("API key: LOCALLY_CONFIGURED", result["text"])
        self.assertIn("Three live AI responses: UNVERIFIED",
                      result["text"])
        self.assertNotIn(config["SUMOPOD_API_KEY"], str(result))
        self.assertFalse(result["execution_allowed"])

    def test_explicit_process_env_precedes_protected_slot_without_mixing(self):
        config = {
            "SUMOPOD_API_KEY": "private-process-value",
            "SUMOPOD_MODEL_MARKET": "glm-5.3-flash",
            "SUMOPOD_MODEL_RISK": "glm-5.3-flash",
            "SUMOPOD_MODEL_STRATEGY": "glm-5.3-flash",
        }
        with patch.dict(os.environ, config, clear=True), patch(
                "indodax_ai_bot.m04_owner_config.owner_provider_source",
                side_effect=AssertionError("NO_MIXED_OWNER_SOURCE")) as local:
            result = self.read()
        self.assertIn("Source: PROCESS_ENV", result["text"])
        self.assertNotIn("private-process-value", str(result))
        local.assert_not_called()

    def test_missing_and_bad_private_config_are_explicit_and_never_paid(self):
        with patch.dict(os.environ, {}, clear=True), patch(
                "indodax_ai_bot.m04_owner_config.owner_provider_source",
                return_value=None):
            no_config = self.read()
        self.assertIn("M04_CONFIG_NOT_FOUND", no_config["text"])
        self.assertIn("NOT_DETECTED", no_config["text"])
        with patch.dict(os.environ, {}, clear=True), patch(
                "indodax_ai_bot.m04_owner_config.owner_provider_source",
                side_effect=M04OwnerConfigDenied("DO_NOT_EXPOSE_TOKEN")):
            invalid = self.read()
        self.assertIn("CONFIGURATION_UNVERIFIED",
                      invalid["text"])
        self.assertNotIn("DO_NOT_EXPOSE_TOKEN", str(invalid))

    def test_non_owner_cannot_read_ai_status_or_provider_file(self):
        with patch("indodax_ai_bot.m04_owner_config.owner_provider_source",
                   side_effect=AssertionError("NO_PRIVATE_READ")) as source:
            bad = self.read(user=OWNER + 1, chat=OWNER + 1)
            group = self.read(chat_type="group")
        self.assertEqual(bad["reason"], "OWNER_NOT_AUTHORIZED")
        self.assertEqual(group["reason"], "OWNER_NOT_AUTHORIZED")
        source.assert_not_called()


if __name__ == "__main__":
    unittest.main()
