"""Secure one-time LOCAL M04 Sumopod credential import for ORIGINAL V2.

Owner runs this directly in an interactive VPS terminal. Key is entered with
echo OFF and is NEVER passed on a command line, logged, printed or pushed to
GitHub. This does not connect to Sumopod or enable paid requests/trades.
"""
from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
import sys

from indodax_ai_bot.m04_owner_config import (
    API_KEY_FILE, CONFIG_DIR, M04OwnerConfigDenied, owner_provider_source,
)


def import_owner_m04_key(
    secret: str, *, directory: Path = CONFIG_DIR, key_file: Path = API_KEY_FILE,
) -> dict:
    # Require original canonical V2 model config to be staged first.
    if key_file != directory / "sumopod.api-key":
        raise M04OwnerConfigDenied("M04_V2_KEY_IMPORT_INVALID_PATH")
    result = owner_provider_source(
        directory=directory,
        config=directory / "provider.json",
        key_file=key_file,
    )
    if result is None:
        raise M04OwnerConfigDenied("M04_V2_KEY_IMPORT_MODELS_NOT_STAGED")
    if result[1] != "M04_V2_KEY_FILE_MISSING":
        raise M04OwnerConfigDenied("M04_V2_KEY_IMPORT_ALREADY_PRESENT")
    if (type(secret) is not str or not 1 <= len(secret) <= 512
            or not all(33 <= ord(c) <= 126 for c in secret)):
        raise M04OwnerConfigDenied("M04_V2_KEY_IMPORT_INVALID_SECRET_FORMAT")
    try:
        fd = os.open(
            key_file,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL |
            getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
    except FileExistsError:
        raise M04OwnerConfigDenied("M04_V2_KEY_IMPORT_ALREADY_PRESENT") from None
    except OSError:
        raise M04OwnerConfigDenied("M04_V2_KEY_IMPORT_CANNOT_CREATE") from None
    # Fail closed: if write/fsync fails, do not leave a truncated key.
    written = False
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(secret.encode("ascii") + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        written = True
    except OSError:
        try:
            key_file.unlink()
        except OSError:
            pass
        raise M04OwnerConfigDenied("M04_V2_KEY_IMPORT_WRITE_FAILED") from None
    if not written:
        raise M04OwnerConfigDenied("M04_V2_KEY_IMPORT_WRITE_FAILED")
    return {
        "status": "M04_V2_LOCAL_SUMOPOD_KEY_INSTALLED_UNVERIFIED",
        "local_key_file_present": True,
        "key_printed": False,
        "paid_provider_network_used": False,
        "provider_account_validated": False,
        "model_availability_validated": False,
        "paid_ai_go_authorized": False,
        "g1_authenticated": False,
        "trading_enabled": False,
    }


def main() -> int:
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        print(json.dumps({
            "status": "DENIED",
            "reason": "M04_V2_KEY_IMPORT_INTERACTIVE_TERMINAL_REQUIRED",
        }, sort_keys=True))
        return 2
    try:
        # Check the file and protected model slot BEFORE requesting a secret
        # so already-present keys never ask for a replacement.
        current = owner_provider_source()
        if current is None:
            raise M04OwnerConfigDenied("M04_V2_KEY_IMPORT_MODELS_NOT_STAGED")
        if current[1] != "M04_V2_KEY_FILE_MISSING":
            raise M04OwnerConfigDenied("M04_V2_KEY_IMPORT_ALREADY_PRESENT")
        key = getpass.getpass(
            "Paste EXISTING Sumopod API key (hidden, not echoed): "
        )
        try:
            response = import_owner_m04_key(key)
        finally:
            # Do not retain a second binding longer than needed.
            key = ""
        print(json.dumps(response, sort_keys=True))
        return 0
    except (EOFError, KeyboardInterrupt):
        print(json.dumps({
            "status": "DENIED",
            "reason": "M04_V2_KEY_IMPORT_CANCELLED",
        }, sort_keys=True))
        return 2
    except M04OwnerConfigDenied as exc:
        print(json.dumps({"status": "DENIED", "reason": str(exc)}, sort_keys=True))
        return 2
    except (OSError, UnicodeError, ValueError):
        print(json.dumps({
            "status": "DENIED",
            "reason": "M04_V2_KEY_IMPORT_LOCAL_ERROR",
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
