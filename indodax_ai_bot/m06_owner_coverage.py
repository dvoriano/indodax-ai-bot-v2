"""Owner foreground one-shot M06 account coverage, original V2 main CLI only.

No new daemon; no secret printed. Requires explicit coverage GO environment,
TTY confirmation and a fresh VIEW ONLY Indodax API key, never a trade key.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import tempfile
import termios
import time

COVERAGE_ID = "M06_PRIVATE_ACCOUNT_COVERAGE_20261101"
_START = 1792774800000  # 2026-10-24 00:00 WIB
_PAIR_SCOPE = ["injidr", "flokiidr", "pepeidr", "usdtidr"]
_BUDGET = (Path.home() / ".local" / "state" / "indodax-ai-bot-v2" /
           "m06-account-coverage-20261101.sqlite3")


def _put_private_json(path: Path, value: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream)


def main() -> int:
    # Import signed transport only AFTER the owner provides exact fresh GO.
    if os.environ.get("M06_OWNER_ONE_SHOT_PRIVATE_GO") != COVERAGE_ID:
        print("M06_COVERAGE_OWNER_GO_REQUIRED", file=sys.stderr)
        return 3
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("M06_COVERAGE_REQUIRES_PRIVATE_INTERACTIVE_TTY", file=sys.stderr)
        return 3
    if os.path.lexists(_BUDGET):
        print("M06_COVERAGE_ONCE_ALREADY_ATTEMPTED", file=sys.stderr)
        return 3
    from .config import Settings
    from .m06_owner_retry import _read_connectbot_secret, _valid_secret
    from .m06_account_coverage import coverage_windows, MAX_REQUESTS
    try:
        Settings.from_env().require_m01_safety()
        now = int(time.time() * 1000)
        coverage_windows(_START, now)
    except Exception:
        print("M06_COVERAGE_SAFETY_OR_DATE_SCOPE_STOP", file=sys.stderr)
        return 3
    print("M06 READ ONLY: 18 GET / 4 pair / 2 jendela 7 hari; trading OFF.", flush=True)
    print("PAKAI API KEY BARU VIEW ONLY (Trade=OFF, Withdraw=OFF).", flush=True)
    print("Rahasia dibaca tanpa echo. Jangan tempel API Key di chat.", flush=True)
    try:
        key = _read_connectbot_secret("API KEY VIEW ONLY")
        secret = _read_connectbot_secret("SECRET KEY VIEW ONLY")
    except (EOFError, KeyboardInterrupt, OSError, ValueError, termios.error):
        print("M06_COVERAGE_TTY_INPUT_CANCELLED", file=sys.stderr)
        return 3
    if not _valid_secret(key) or not _valid_secret(secret) or key == secret:
        print("M06_COVERAGE_KEY_INVALID", file=sys.stderr)
        return 3
    try:
        with tempfile.TemporaryDirectory(prefix="m06-viewonly-") as d:
            td = Path(d)
            _put_private_json(td / "credentials.json", {
                "api_key":key, "secret_key":secret,
            })
            now = int(time.time() * 1000)
            coverage_windows(_START, now)
            _put_private_json(td / "go.json", {
                "approval_id":COVERAGE_ID,
                "issued_ms":now,
                "expires_ms":now + 900000,
                "max_requests":MAX_REQUESTS,
                "start_ms":_START,
                "end_ms":now,
                "symbols":_PAIR_SCOPE,
            })
            # One CLI/orchestrator; temporary journal, existing V2 ledger RO.
            from .__main__ import main as original_main
            existing = (Path.home() / "indodax-ai-bot-v2" /
                        "runtime" / "decisions.sqlite3")
            argv = [
                "--db", str(td / "decisions.sqlite3"),
                "--m06-account-coverage-once",
                "--m06-go-file", str(td / "go.json"),
                "--m06-credentials-file", str(td / "credentials.json"),
            ]
            if existing.is_file() and not existing.is_symlink():
                argv.extend(("--m06-ownership-db", str(existing)))
            return original_main(argv)
    except (OSError, ValueError):
        print("M06_COVERAGE_SESSION_LOCAL_STOP", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
