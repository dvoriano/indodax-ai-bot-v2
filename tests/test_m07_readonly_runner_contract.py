"""Operator one-shot contract: original V2 CLI only, all trade paths off.

The stubbed CLI captures argv/environment. No public/private HTTP is made.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/m07_readonly_once.sh"


class M07ReadOnlyRunnerContract(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.root = self.home / "indodax-ai-bot-v2"
        (self.root / "scripts").mkdir(parents=True)
        (self.root / "runtime").mkdir()
        (self.root / ".venv" / "bin").mkdir(parents=True)
        (self.root / "runtime" / "public_ws_btc.sqlite3").write_bytes(b"existing")
        shutil.copyfile(SCRIPT, self.root / "scripts" / "m07_readonly_once.sh")
        self.bin = self.home / "mock-bin"
        self.bin.mkdir()
        git = self.bin / "git"
        git.write_text(
            "#!/bin/sh\n"
            'case "$1" in\n'
            '  symbolic-ref) printf "%s\\n" "${FAKE_BRANCH:-main}";;\n'
            '  merge-base|status) exit 0;;\n'
            '  rev-parse) printf "sha-from-test-stub\\n";;\n'
            '  *) exit 6;;\n'
            'esac\n'
        )
        git.chmod(0o700)
        python = self.root / ".venv" / "bin" / "python"
        python.write_text(
            "#!/usr/bin/python3\n"
            "import json, os, pathlib, stat, sys\n"
            "root = pathlib.Path(__file__).resolve().parents[2]\n"
            "args = sys.argv[1:]\n"
            "f = pathlib.Path(args[args.index('--m07-runtime-funding-file')+1])\n"
            "record = {'args':args, 'budget':json.loads(f.read_text()), "
            "'mode':stat.S_IMODE(f.stat().st_mode), "
            "'secret_forwarded':'M07_TEST_SECRET' in os.environ, "
            "'environment':{k:os.environ.get(k) for k in "
            "('INDODAX_TRADING_ENABLED','INDODAX_ALLOW_LIVE_EXECUTION',"
            "'INDODAX_EXECUTION_MODE','PYTHONDONTWRITEBYTECODE')}}\n"
            "(root/'runtime'/'runner-capture.json').write_text(json.dumps(record))\n"
        )
        python.chmod(0o700)

    def run_script(self, *, branch="main"):
        env = dict(os.environ)
        env.update(HOME=str(self.home),
                   PATH=str(self.bin) + os.pathsep + env["PATH"],
                   FAKE_BRANCH=branch,
                   M07_TEST_SECRET="must-not-be-forwarded")
        return subprocess.run(
            ["bash", str(self.root / "scripts" / "m07_readonly_once.sh")],
            env=env, text=True, capture_output=True, timeout=10,
        )

    def test_original_cli_and_nonlive_environment_with_private_budget(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads((self.root / "runtime" / "runner-capture.json").read_text())
        self.assertEqual(data["mode"], 0o600)
        self.assertFalse(data["secret_forwarded"])
        self.assertEqual(data["environment"], {
            "INDODAX_TRADING_ENABLED": "NO",
            "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
            "INDODAX_EXECUTION_MODE": "DRY_RUN",
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        self.assertIn("--m07-main-readonly-cycle-once", data["args"])
        self.assertIn("--bulk-min-volume-idr", data["args"])
        self.assertIn("150000000", data["args"])
        self.assertIn("--bulk-deep-limit", data["args"])
        self.assertIn("20", data["args"])
        self.assertEqual(data["budget"]["requested_ticket_ceiling_idr"], "100000")
        self.assertFalse(list((self.root / "runtime").glob(".m07-once-funding.*")))

    def test_non_main_branch_rejected_without_invoking_cli(self):
        result = self.run_script(branch="feature")
        self.assertEqual(result.returncode, 3)
        self.assertIn("M07_ABORT_SOURCE_NOT_MAIN", result.stderr)
        self.assertFalse((self.root / "runtime" / "runner-capture.json").exists())

    def test_second_concurrent_viewonly_session_is_refused(self):
        lock = self.root / "runtime" / ".m07-readonly-once.lock"
        with lock.open("w") as owner:
            fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_script()
        self.assertEqual(result.returncode, 3)
        self.assertIn("M07_ABORT_CONCURRENT_VIEWONLY_SESSION", result.stderr)
        self.assertFalse((self.root / "runtime" / "runner-capture.json").exists())

    def test_missing_ws_rejected_before_creating_private_budget(self):
        (self.root / "runtime" / "public_ws_btc.sqlite3").unlink()
        result = self.run_script()
        self.assertEqual(result.returncode, 3)
        self.assertIn("M07_ABORT_V2_PYTHON_OR_EXISTING_BTC_WS_MISSING",
                      result.stderr)
        self.assertFalse((self.root / "runtime" / "runner-capture.json").exists())


if __name__ == "__main__":
    unittest.main()
