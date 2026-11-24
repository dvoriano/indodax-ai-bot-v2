"""Bounded post-400m M04 WATCH research collector; one invocation, no trading.

Reads existing original M04/M03 only. Writes exclusively owner-private
research evidence files under the dedicated post-policy directory.
No original database mutation, extra scanner, provider, WS/REST, Telegram,
model invocation, order, or service control. An external owner-enabled
systemd timer may invoke this one-shot entry point. Never starts a timer itself.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import tempfile
import time

from indodax_ai_bot.m05_radar_momentum_nonlive import DEFAULT_DB
from scripts.m04_ws3_watch_channel_coverage_readonly import (
    inspect_m04_watch_trade_channels_readonly,
)
from scripts.m04_ws3_watch_multicohort_readonly import (
    _validate_report, _read_private_report,
    summarize_original_current_watch_reports_readonly,
)

DEFAULT_DIR = DEFAULT_DB.parent / "m04-watch-cohorts-post400"
MAX_REPORTS = 32
FLOOR = "400000000"
# Require all three explicit locks in the actual invoking environment.
LOCKS = {
    "INDODAX_TRADING_ENABLED": "NO",
    "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
    "INDODAX_EXECUTION_MODE": "DRY_RUN",
}


def _result(status: str, **fields) -> dict:
    return {
        "stage": "M04_POST400_CURRENT_WATCH_COLLECTION_RESEARCH_ONLY",
        "status": status,
        "execution_allowed": False,
        "independent_g1_verified": False,
        "pair_specific_ack_verified": False,
        "provider_calls": 0,
        "telegram_messages_sent": 0,
        "order_sent": False,
        "paper_fill_created": False,
        **fields,
    }


def _validate_post400(report: dict) -> tuple[int, set[tuple]]:
    epoch, _, _ = _validate_report(report)
    if report.get("current_m04_ws3_handoff_min_rolling_24h_idr") != FLOOR:
        raise ValueError("POST400_POLICY_MARKER_MISSING")
    keys = set()
    for row in report["observations"]:
        value = row.get("current_m04_source_rolling_24h_turnover_idr")
        if (type(value) is not str or not 1 <= len(value) <= 64
                or not Decimal(value).is_finite()
                or Decimal(value) < Decimal(FLOOR)):
            raise ValueError("POST400_TURNOVER_INVALID")
        keys.add((row["pair"], row["m04_source_observed_epoch"]))
    return epoch, keys


def _private_directory(path: Path, *, create: bool) -> None:
    # Only the final dedicated directory may be created. Never change an
    # existing directory's permission or follow a symlink to create evidence.
    parent = path.parent.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid()
            or parent.st_mode & 0o022):
        raise ValueError("PRIVATE_EVIDENCE_PARENT_REQUIRED")
    if create:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError("PRIVATE_POST400_DIRECTORY_REQUIRED")


def _existing(path: Path) -> tuple[list[Path], set[tuple]]:
    files = []
    keys = set()
    for p in path.glob("watch-*.json"):
        files.append(p)
        if len(files) > MAX_REPORTS:
            raise ValueError("POST400_STORAGE_LIMIT_EXCEEDED")
        _, identities = _validate_post400(_read_private_report(p))
        keys.update(identities)
    return sorted(files), keys


def collect_once(*, source_db: Path, evidence_dir: Path,
                 now_epoch: int) -> dict:
    if any(os.environ.get(k) != v for k, v in LOCKS.items()):
        return _result("POST400_EXPLICIT_NONLIVE_LOCKS_REQUIRED")
    if type(now_epoch) is not int or now_epoch <= 0:
        return _result("POST400_CLOCK_INVALID")
    directory = Path(evidence_dir).absolute()
    try:
        _private_directory(directory, create=True)
        # flock spans existing-file validation, capture and atomic publish.
        # O_NOFOLLOW and fstat reject redirected or shared lock files.
        fd = os.open(directory / ".collector.lock",
                     os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError("POST400_LOCK_FILE_INVALID")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return _result("POST400_COLLECTOR_ALREADY_RUNNING")
            files, seen = _existing(directory)
            if len(files) >= MAX_REPORTS:
                # Stop rather than delete previous evidence to make space.
                return _result("POST400_STORAGE_CAP_REACHED",
                               saved_reports=len(files))
            report = inspect_m04_watch_trade_channels_readonly(
                source_db=source_db, evaluation_epoch=now_epoch)
            status = report.get("status")
            if status == "ORIGINAL_M04_NO_ELIGIBLE_UNCALLED_WATCH":
                # Existing wrapper does not distinguish no watch from some
                # rejected original inputs. Do not claim no market activity.
                return _result("POST400_NO_VALID_CURRENT_WATCH",
                               saved_reports=len(files))
            if status != "M04_WATCH_TRADE_CHANNEL_COVERAGE_RESEARCH_ONLY":
                return _result("POST400_SOURCE_CAPTURE_BLOCKED",
                               saved_reports=len(files))
            epoch, keys = _validate_post400(report)
            if epoch != now_epoch:
                raise ValueError("POST400_CAPTURE_CLOCK_MISMATCH")
            new_keys = keys - seen
            if not new_keys:
                return _result("POST400_DUPLICATE_SOURCE_OBSERVATIONS",
                               saved_reports=len(files))
            raw = json.dumps(report, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode("ascii")
            if not 0 < len(raw) <= 262144:
                raise ValueError("POST400_REPORT_SIZE_INVALID")
            suffix = sha256(raw).hexdigest()
            dest = directory / ("watch-" + str(epoch) + "-" + suffix + ".json")
            if dest.exists() or dest.is_symlink():
                raise ValueError("POST400_DESTINATION_ALREADY_EXISTS")
            pending = None
            try:
                with tempfile.NamedTemporaryFile(
                        dir=directory, prefix=".pending-", delete=False) as out:
                    pending = Path(out.name)
                    out.write(raw)
                    out.flush()
                    os.fsync(out.fileno())
                # Under the owner-only directory and exclusive lock, no
                # collector can overwrite a previously published report.
                os.replace(pending, dest)
                pending = None
            finally:
                if pending is not None:
                    pending.unlink(missing_ok=True)
            return _result("POST400_CURRENT_WATCH_SAVED",
                           saved_reports=len(files) + 1,
                           new_source_observations=len(new_keys),
                           candidates=report["candidate_count"])
        finally:
            os.close(fd)
    except (OSError, ValueError, TypeError, KeyError, InvalidOperation):
        return _result("POST400_STORAGE_OR_CONTRACT_BLOCKED")


def summarize(*, evidence_dir: Path) -> dict:
    directory = Path(evidence_dir).absolute()
    try:
        _private_directory(directory, create=False)
        files, _ = _existing(directory)
        if not files:
            return _result("POST400_NO_SAVED_REPORTS", saved_reports=0)
        report = summarize_original_current_watch_reports_readonly(
            evidence_files=files, post400_only=True)
        if report["status"] != "M04_CURRENT_WATCH_MULTICOHORT_RESEARCH_ONLY":
            return _result("POST400_SUMMARY_BLOCKED")
        return _result("POST400_SUMMARY_RESEARCH_ONLY",
                       saved_reports=len(files), summary=report)
    except FileNotFoundError:
        return _result("POST400_EVIDENCE_DIRECTORY_UNAVAILABLE")
    except (OSError, ValueError, TypeError, KeyError, InvalidOperation):
        return _result("POST400_STORAGE_OR_CONTRACT_BLOCKED")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--collect-once", action="store_true")
    mode.add_argument("--summarize", action="store_true")
    args = parser.parse_args(argv)
    result = (collect_once(source_db=DEFAULT_DB, evidence_dir=DEFAULT_DIR,
                           now_epoch=int(time.time()))
              if args.collect_once else summarize(evidence_dir=DEFAULT_DIR))
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 2 if ("BLOCKED" in result["status"] or
                 result["status"] in ("POST400_EXPLICIT_NONLIVE_LOCKS_REQUIRED",
                                      "POST400_CLOCK_INVALID")) else 0


if __name__ == "__main__":
    raise SystemExit(main())
