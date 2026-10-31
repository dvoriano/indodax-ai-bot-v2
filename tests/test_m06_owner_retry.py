"""M06 owner retry ergonomics, safety and no private connections."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from indodax_ai_bot import m06_owner_retry as retry


class FakeTTY:
    def isatty(self):
        return True


class M06OwnerRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "new-budget.sqlite3"
        self.env = {
            "M06_OWNER_ONE_SHOT_PRIVATE_GO":
                "M06_PRIVATE_VIEW_ONLY_RETRY_20261031",
            "INDODAX_TRADING_ENABLED": "NO",
            "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
            "INDODAX_EXECUTION_MODE": "DRY_RUN",
        }

    def test_connectbot_reader_disables_echo_and_prints_ready(self):
        import contextlib
        import io
        import termios
        fake_fd = 25
        term = [0, 0, 0, termios.ECHO | termios.ECHONL, 0, 0, [0] * 32]
        changed = []
        sequence = iter((bytes([97]), bytes([98]), bytes([99]), bytes([10])))
        def tcsetattr(fd, how, attributes):
            self.assertEqual(fd, fake_fd)
            changed.append(attributes[3])
        def tcgetattr(fd):
            self.assertEqual(fd, fake_fd)
            if changed:
                attrs = term.copy()
                attrs[3] = changed[-1]
                return attrs
            return term.copy()
        out = io.StringIO()
        with patch("indodax_ai_bot.m06_owner_retry.os.open",
                   return_value=fake_fd), patch(
                   "indodax_ai_bot.m06_owner_retry.os.close"), patch(
                   "indodax_ai_bot.m06_owner_retry.os.read",
                   side_effect=lambda fd, n: next(sequence)), patch(
                   "indodax_ai_bot.m06_owner_retry.termios.tcgetattr",
                   side_effect=tcgetattr), patch(
                   "indodax_ai_bot.m06_owner_retry.termios.tcsetattr",
                   side_effect=tcsetattr), contextlib.redirect_stdout(out):
            answer = retry._read_connectbot_secret("LANGKAH API")
        self.assertEqual(answer, "abc")
        self.assertEqual(changed[0] & (termios.ECHO | termios.ECHONL), 0)
        self.assertEqual(changed[-1] & (termios.ECHO | termios.ECHONL),
                         termios.ECHO | termios.ECHONL)
        self.assertIn("PASTE SATU NILAI", out.getvalue())

    def test_no_owner_go_never_asks_for_credentials(self):
        with patch.dict(os.environ, self.env, clear=True):
            with patch.dict(os.environ, {
                    "M06_OWNER_ONE_SHOT_PRIVATE_GO": ""
            }):
                with patch.object(retry, "_BUDGET", self.path):
                    with patch("indodax_ai_bot.m06_owner_retry._read_connectbot_secret") as prompt:
                        self.assertEqual(retry.main(), 3)
                        prompt.assert_not_called()

    def test_existing_retry_budget_refuses_second_input(self):
        self.path.touch()
        with patch.dict(os.environ, self.env):
            with patch.object(retry, "_BUDGET", self.path):
                with patch("indodax_ai_bot.m06_owner_retry._read_connectbot_secret") as prompt:
                    self.assertEqual(retry.main(), 3)
                    prompt.assert_not_called()

    def test_bad_key_format_stops_without_db_or_transport(self):
        with patch.dict(os.environ, self.env):
            with patch.object(retry, "_BUDGET", self.path):
                with patch.object(retry.sys, "stdin", FakeTTY()):
                    with patch(
                        "indodax_ai_bot.m06_owner_retry._read_connectbot_secret",
                        side_effect=["fixture-key", "bad secret with spaces"],
                    ):
                        with patch("indodax_ai_bot.__main__.main") as original:
                            self.assertEqual(retry.main(), 3)
                            original.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_pasting_same_key_twice_stops_before_any_private_call(self):
        with patch.dict(os.environ, self.env):
            with patch.object(retry, "_BUDGET", self.path):
                with patch.object(retry.sys, "stdin", FakeTTY()):
                    with patch(
                        "indodax_ai_bot.m06_owner_retry._read_connectbot_secret",
                        side_effect=["same-key", "same-key"],
                    ):
                        with patch("indodax_ai_bot.__main__.main") as original:
                            self.assertEqual(retry.main(), 3)
                            original.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_hidden_prompts_pass_only_to_original_cli_and_clean_files(self):
        snapshots = []
        def original(argv):
            assert argv[:2] == ["--db", argv[1]]
            go = Path(argv[argv.index("--m06-go-file") + 1])
            keys = Path(argv[argv.index("--m06-credentials-file") + 1])
            snapshots.append((
                json.loads(go.read_text()),
                json.loads(keys.read_text()),
                keys.stat().st_mode & 0o777,
                str(keys),
            ))
            return 0
        with patch.dict(os.environ, self.env):
            with patch.object(retry, "_BUDGET", self.path):
                with patch.object(retry.sys, "stdin", FakeTTY()):
                    with patch(
                        "indodax_ai_bot.m06_owner_retry._read_connectbot_secret",
                        side_effect=["fixture-key", "fixture-secret"],
                    ):
                        with patch("indodax_ai_bot.__main__.main",
                                   side_effect=original) as func:
                            self.assertEqual(retry.main(), 0)
                            func.assert_called_once()
        self.assertEqual(len(snapshots), 1)
        go, creds, mode, oldpath = snapshots[0]
        self.assertEqual(go["approval_id"],
                         "M06_PRIVATE_VIEW_ONLY_RETRY_20261031")
        self.assertEqual(go["max_requests"], 4)
        self.assertEqual(go["symbol"], "injidr")
        self.assertEqual(creds["api_key"], "fixture-key")
        self.assertEqual(creds["secret_key"], "fixture-secret")
        self.assertEqual(mode, 0o600)
        self.assertFalse(Path(oldpath).exists())
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
