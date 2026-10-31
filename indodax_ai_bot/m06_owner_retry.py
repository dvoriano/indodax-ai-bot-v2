"""Foreground owner input for a single Indodax M06 View Only retry.

Operator-friendly wrapper for the SAME V2 CLI/orchestrator. No second trade
engine, no automatic retry, no raw balances or credentials in stdout.
"""
from __future__ import annotations

import termios
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from .config import Settings


_RETRY = "M06_PRIVATE_VIEW_ONLY_RETRY_20261031"
_BUDGET = (Path.home() / ".local" / "state" / "indodax-ai-bot-v2"
           / "m06-owner-viewonly-retry-20261031.sqlite3")


def _valid_secret(value: object) -> bool:
    return (type(value) is str and 1 <= len(value) <= 256
            and all(33 <= ord(c) <= 126 for c in value))


def _private_json(path: Path, data: dict) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)



def _read_connectbot_secret(label: str) -> str:
    """Read from controlling TTY with remote echo disabled.

    Unlike getpass(), print a visible READY marker on stdout first because
    Android SSH clients may not show a /dev/tty prompt reliably.
    """
    if type(label) is not str:
        raise ValueError("M06_TERMINAL_LABEL_INVALID")
    fd = os.open("/dev/tty", os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
    try:
        original = termios.tcgetattr(fd)
        hidden = original.copy()
        hidden[6] = original[6][:]
        hidden[3] &= ~(termios.ECHO | termios.ECHONL)
        termios.tcsetattr(fd, termios.TCSAFLUSH, hidden)
        try:
            current = termios.tcgetattr(fd)
            if current[3] & (termios.ECHO | termios.ECHONL):
                raise ValueError("M06_TERMINAL_ECHO_NOT_DISABLED")
            print(label + ": PASTE SATU NILAI, LALU TEKAN ENTER", flush=True)
            raw = bytearray()
            while True:
                char = os.read(fd, 1)
                if not char:
                    raise EOFError()
                if char in (b"\${word}", b"\${word}"):
                    break
                if len(raw) >= 256:
                    raise ValueError("M06_CREDENTIAL_TOO_LONG")
                raw.extend(char)
            print("M06_INPUT_RECEIVED", flush=True)
            try:
                return raw.decode("ascii", "strict")
            finally:
                raw[:] = b"\${word}" * len(raw)
        finally:
            termios.tcsetattr(fd, termios.TCSAFLUSH, original)
    finally:
        os.close(fd)

def main() -> int:
    if os.environ.get("M06_OWNER_ONE_SHOT_PRIVATE_GO") != _RETRY:
        print("M06_RETRY_OWNER_GO_REQUIRED", file=sys.stderr)
        return 3
    try:
        Settings.from_env().require_m01_safety()
    except Exception:
        print("M06_RETRY_TRADING_SAFETY_STOP", file=sys.stderr)
        return 3
    if os.path.lexists(_BUDGET):
        print("M06_RETRY_ALREADY_ATTEMPTED_STOP", file=sys.stderr)
        return 3
    if not sys.stdin.isatty():
        print("M06_RETRY_REQUIRES_INTERACTIVE_TERMINAL", file=sys.stderr)
        return 3

    print("M06: gunakan API Key BARU (key di screenshot sebelumnya harus dicabut).", flush=True)
    print("Input tersembunyi di sisi server. Jangan paste script atau JSON.", flush=True)
    try:
        api_key = _read_connectbot_secret("LANGKAH 1/2 API KEY VIEW ONLY")
        secret_key = _read_connectbot_secret("LANGKAH 2/2 SECRET KEY VIEW ONLY")
    except (EOFError, KeyboardInterrupt, OSError, ValueError, termios.error):
        print("M06_RETRY_INPUT_CANCELLED_OR_TERMINAL_UNSAFE", file=sys.stderr)
        return 3
    if not _valid_secret(api_key) or not _valid_secret(secret_key):
        print("M06_RETRY_CREDENTIAL_FORMAT_INVALID", file=sys.stderr)
        return 3
    if api_key == secret_key:
        print("M06_KEY_AND_SECRET_IDENTICAL_STOP", file=sys.stderr)
        return 3

    try:
        with tempfile.TemporaryDirectory(prefix="v2-m06-retry-") as td:
            root = Path(td)
            _private_json(root / "credentials.json", {
                "api_key": api_key, "secret_key": secret_key,
            })
            issued = int(time.time() * 1000)
            _private_json(root / "go.json", {
                "approval_id": _RETRY, "issued_ms": issued,
                "expires_ms": issued + 900000,
                "max_requests": 4, "symbol": "injidr",
            })
            # Invoke one original CLI entrypoint, one orchestrator and no
            # alternative request transport. Budget remains permanent.
            from .__main__ import main as original_main
            return original_main([
                "--db", str(root / "audit.sqlite3"),
                "--m06-private-readonly-once",
                "--m06-go-file", str(root / "go.json"),
                "--m06-credentials-file", str(root / "credentials.json"),
            ])
    except (OSError, ValueError):
        print("M06_RETRY_LOCAL_SESSION_FAILED", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
