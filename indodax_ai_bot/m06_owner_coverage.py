"""Owner foreground one-shot M06 account coverage, original V2 main CLI only.

No new daemon; no secret printed. Requires explicit coverage GO environment
and a pre-existing, owner-only V2 View Only credentials file. Never prompts
for a key again. No credential discovery outside the one V2 checkout and its
standard V2-owned config/state directories.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import tempfile
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


def _existing_v2_credentials() -> Path | None:
    """Use ONLY a pre-existing 0600 V2-owned View Only credential JSON.

    No broad search for secrets. An operator can point to an existing V2
    credential file with M06_V2_VIEWONLY_CREDENTIALS_FILE (a PATH, not key).
    Otherwise check explicit documented V2-only locations. Never echo path,
    values or permission details to the caller.
    """
    home = Path.home()
    roots = (
        home / "indodax-ai-bot-v2",
        home / ".local" / "state" / "indodax-ai-bot-v2",
        home / ".config" / "indodax-ai-bot-v2",
    )
    existing = os.environ.get("M06_V2_VIEWONLY_CREDENTIALS_FILE")
    if existing is not None:
        if not existing.startswith("/") or not existing.strip():
            raise ValueError("M06_EXISTING_V2_CREDENTIAL_PATH_INVALID")
        candidate = Path(existing)
    else:
        candidates = (
            roots[1] / "viewonly-credentials.json",
            roots[1] / "m06-viewonly-credentials.json",
            roots[2] / "viewonly-credentials.json",
            roots[0] / "runtime" / "m06-viewonly-credentials.json",
        )
        candidate = next((p for p in candidates if p.exists() or p.is_symlink()),
                         None)
        if candidate is None:
            return None
    try:
        # Reject path traversal and symlinked parent/file, even if it resolves
        # back inside a V2 directory.
        absolute = Path(os.path.abspath(candidate))
        if absolute != candidate or absolute.resolve(strict=True) != absolute:
            raise ValueError("M06_EXISTING_V2_CREDENTIAL_PATH_UNSAFE")
        if not any(absolute.is_relative_to(root) for root in roots):
            raise ValueError("M06_EXISTING_V2_CREDENTIAL_OUTSIDE_ALLOWLIST")
        # Read strictly through the canonical V2 credential parser. It
        # refuses non-owner, non-regular, world-readable or oversized files.
        from .m06_scoped_https import read_view_only_credentials
        key, secret = read_view_only_credentials(str(absolute))
        if key == secret:
            raise ValueError("M06_EXISTING_V2_CREDENTIAL_PAIR_INVALID")
        return absolute
    except (OSError, ValueError, RuntimeError):
        raise ValueError("M06_EXISTING_V2_CREDENTIALS_NOT_SAFE") from None


def _one_account_diagnostic_main() -> int:
    """Owner-approved separate 1-GET scope, original V2 CLI only."""
    from .m06_account_coverage import ACCOUNT_DIAG_ID
    from .config import Settings

    diagnostic_budget = (Path.home() / ".local" / "state" /
                         "indodax-ai-bot-v2" /
                         "m06-account-diagnostic-20261101.sqlite3")
    if os.environ.get("M06_OWNER_ONE_SHOT_PRIVATE_GO") != ACCOUNT_DIAG_ID:
        print("M06_ACCOUNT_DIAG_FRESH_OWNER_GO_REQUIRED_NO_API", file=sys.stderr)
        return 3
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("M06_ACCOUNT_DIAG_INTERACTIVE_OWNER_TTY_REQUIRED_NO_API",
              file=sys.stderr)
        return 3
    if os.path.lexists(diagnostic_budget):
        print("M06_ACCOUNT_DIAG_ALREADY_ATTEMPTED_NO_API", file=sys.stderr)
        return 3
    try:
        Settings.from_env().require_m01_safety()
        credentials = _existing_v2_credentials()
        if credentials is None:
            print("M06_ACCOUNT_DIAG_V2_CREDENTIALS_NOT_FOUND_NO_API",
                  file=sys.stderr)
            return 3
    except (ValueError, OSError):
        print("M06_ACCOUNT_DIAG_SAFETY_OR_CREDENTIAL_STOP_NO_API",
              file=sys.stderr)
        return 3
    try:
        with tempfile.TemporaryDirectory(prefix="m06-account-one-get-") as d:
            td = Path(d)
            issued = int(time.time() * 1000)
            _put_private_json(td / "go.json", {
                "approval_id": ACCOUNT_DIAG_ID,
                "issued_ms": issued, "expires_ms": issued + 900000,
                "max_requests": 1, "symbol": "injidr",
            })
            print("M06_ACCOUNT_ONE_GET_ONLY_TRADE_AND_WITHDRAW_DISABLED",
                  flush=True)
            from .__main__ import main as original_main
            return original_main([
                "--db", str(td / "decisions.sqlite3"),
                "--m06-account-diagnostic-once",
                "--m06-go-file", str(td / "go.json"),
                "--m06-credentials-file", str(credentials),
            ])
    except (OSError, ValueError):
        print("M06_ACCOUNT_DIAG_SESSION_LOCAL_STOP_NO_API", file=sys.stderr)
        return 3


def main() -> int:
    if sys.argv[1:] == ["--account-diagnostic-once"]:
        return _one_account_diagnostic_main()
    if sys.argv[1:]:
        print("M06_OWNER_COVERAGE_ARGUMENT_DENIED_NO_API", file=sys.stderr)
        return 3
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
    from .m06_account_coverage import coverage_windows, MAX_REQUESTS
    try:
        Settings.from_env().require_m01_safety()
        now = int(time.time() * 1000)
        coverage_windows(_START, now)
    except Exception:
        print("M06_COVERAGE_SAFETY_OR_DATE_SCOPE_STOP", file=sys.stderr)
        return 3
    try:
        credentials = _existing_v2_credentials()
    except ValueError:
        print("M06_V2_EXISTING_VIEWONLY_CREDENTIALS_UNSAFE_NO_API",
              file=sys.stderr)
        return 3
    if credentials is None:
        # A historical interactive key may have existed only in a
        # TemporaryDirectory and is not automatically retained on disk.
        print("M06_V2_VIEWONLY_CREDENTIAL_FILE_NOT_FOUND_NO_API",
              file=sys.stderr)
        return 3
    print("M06_VIEWONLY_EXISTING_V2_CREDENTIALS_VERIFIED_LOCALLY", flush=True)
    print("M06_READ_ONLY_18_GET_ONE_SESSION_TRADING_DISABLED", flush=True)
    try:
        with tempfile.TemporaryDirectory(prefix="m06-viewonly-") as d:
            td = Path(d)
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
                "--m06-credentials-file", str(credentials),
            ]
            if existing.is_file() and not existing.is_symlink():
                argv.extend(("--m06-ownership-db", str(existing)))
            return original_main(argv)
    except (OSError, ValueError):
        print("M06_COVERAGE_SESSION_LOCAL_STOP", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
