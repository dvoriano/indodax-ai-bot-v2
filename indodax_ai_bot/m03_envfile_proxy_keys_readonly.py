"""One-shot, zero-network M03 user systemd EnvironmentFile proxy-key audit.

ONLY the exact original M03 unit is queried. Only eligible private files
under ~/.config/indodax-ai-bot-v2 are opened. No paths, contents, proxy
URLs, tokens, credentials, external services or other projects are output.
No unit change, trading, public REST quota, or outbound GET.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import subprocess

from .m03_http_access_diagnostic import _proxy_names

_UNIT = "indodax-v2-m03-mainpath.service"
_ENVFILE_RE = re.compile(
    r"(?P<file>/[^\s()]+)\s+\(ignore_errors=(?:yes|no)\)"
)
_KEY_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
_LIMIT = 65536
_FILES_MAX = 8


def _parse_unit_env_files(metadata: str) -> list[str] | None:
    """Accept only unambiguous unescaped absolute paths from systemctl."""
    if metadata == "":
        return []
    found = list(_ENVFILE_RE.finditer(metadata))
    if not found or len(found) > _FILES_MAX:
        return None
    remainder = _ENVFILE_RE.sub("", metadata)
    if remainder.strip() or len({m.group("file") for m in found}) != len(found):
        return None
    return [m.group("file") for m in found]


def _read_private_v2_proxy_keys(path: str, home: Path) -> list[str] | None:
    """Do not follow symlinks; only owner-private ORIGINAL V2 env files."""
    cfg = home / ".config" / "indodax-ai-bot-v2"
    target = Path(path)
    if not target.is_absolute() or not target.is_relative_to(cfg):
        return None
    if target == cfg or len(target.parts) <= len(cfg.parts):
        return None
    try:
        # All components from user home to leaf must be owner-controlled,
        # non-symlink, and not group/world writable.
        for parent in (home, *reversed(target.parents[:-len(home.parts)])):
            st = parent.lstat()
            if (not stat.S_ISDIR(st.st_mode)
                    or st.st_uid != os.geteuid()
                    or (st.st_mode & 0o022) != 0):
                return None
        st = target.lstat()
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid()
                or st.st_nlink != 1 or (st.st_mode & 0o077) != 0
                or not 0 <= st.st_size <= _LIMIT):
            return None
        fd = os.open(
            target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            actual = os.fstat(fd)
            if (not stat.S_ISREG(actual.st_mode)
                    or actual.st_uid != os.geteuid()
                    or actual.st_nlink != 1
                    or (actual.st_mode & 0o077) != 0
                    or actual.st_size > _LIMIT
                    or (actual.st_dev, actual.st_ino) != (st.st_dev, st.st_ino)):
                return None
            raw = os.read(fd, _LIMIT + 1)
        finally:
            os.close(fd)
        if len(raw) > _LIMIT:
            return None
        # Metadata only. Proxy values and other secrets are never printed.
        keys = [
            match.group(1)
            for line in raw.decode("utf-8").splitlines()
            if (match := _KEY_RE.match(line)) is not None
        ]
        return _proxy_names(keys)
    except (OSError, ValueError, UnicodeError, OverflowError):
        return None


def inspect_m03_envfile_proxy_keys(
    *, metadata: dict[str, str] | None, home: Path,
) -> dict:
    """Report only booleans, counts and a fixed proxy-key name allowlist."""
    result = {
        "status": "M03_ENVFILE_PROXY_KEYS_READONLY",
        "unit_trust_verified": False,
        "environment_files_declared": None,
        "environment_files_inspected": 0,
        "environment_files_skipped": 0,
        "environment_proxy_keys": None,
        "environment_files_all_checked": False,
        "unit_dropins_present": None,
        "no_network_calls": True,
        "order_sent": False,
        "trading_enabled": False,
        "cloudflare_403_root_cause_verified": False,
    }
    if metadata is None:
        return result
    fragment = home / ".config" / "systemd" / "user" / _UNIT
    text = metadata.get("ExecStart", "")
    ok = (metadata.get("FragmentPath") == str(fragment)
          and str(home / "indodax-ai-bot-v2" / ".venv/bin/python") in text)
    result["unit_trust_verified"] = ok
    result["unit_dropins_present"] = bool(
        metadata.get("DropInPaths", "").strip()
    )
    if not ok:
        return result
    paths = _parse_unit_env_files(metadata.get("EnvironmentFiles", ""))
    if paths is None:
        return result
    result["environment_files_declared"] = len(paths)
    seen = set()
    for path in paths:
        keys = _read_private_v2_proxy_keys(path, home)
        if keys is None:
            result["environment_files_skipped"] += 1
        else:
            result["environment_files_inspected"] += 1
            seen.update(keys)
    result["environment_proxy_keys"] = sorted(seen)
    result["environment_files_all_checked"] = (
        result["environment_files_inspected"] == len(paths)
    )
    return result


def _unit_metadata() -> dict[str, str] | None:
    fields = ("EnvironmentFiles", "FragmentPath", "ExecStart", "DropInPaths")
    cmd = ["systemctl", "--user", "show", _UNIT, "--no-pager"]
    cmd += [a for field in fields for a in ("-p", field)]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=8
        )
        if proc.returncode != 0:
            return None
        rows = {}
        for line in proc.stdout.splitlines():
            name, sep, value = line.partition("=")
            if sep and name in fields:
                rows[name] = value
        return rows
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return None


def main() -> None:
    try:
        report = inspect_m03_envfile_proxy_keys(
            metadata=_unit_metadata(), home=Path.home()
        )
    except Exception:
        # Diagnostic cannot leak exception args, private paths or values.
        report = {
            "status": "M03_ENVFILE_PROXY_KEYS_DIAGNOSTIC_UNAVAILABLE",
            "no_network_calls": True,
            "order_sent": False,
            "trading_enabled": False,
            "cloudflare_403_root_cause_verified": False,
        }
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
