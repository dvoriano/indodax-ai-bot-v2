"""M10-05 V2-only local REST budget diagnostic. No network or mutations.

The output is deliberately NOT proof of complete outbound IP consumers.
It never opens V1/Hermes, outside services, or owner secret policy JSON.
Use: python -B -m indodax_ai_bot.m10_egress_readonly
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import stat
import time
from urllib.parse import quote


def inspect_v2_local_egress(
    db_path: str | Path | None = None, *, now_epoch: float | None = None,
) -> dict:
    """Unprivileged, immutable SQLite snapshot; ALWAYS rejects global proof.

    db_path is for offline tests; the CLI deliberately accepts no path input
    and reads only the V2 default namespaced user quota file.
    An immutable read can miss uncheckpointed WAL activity: never use a
    sidecar-bearing snapshot as positive quota evidence.
    """
    current = time.time() if now_epoch is None else now_epoch
    if isinstance(current, bool) or not isinstance(current, (int, float)) or not (0 < current < 1e12):
        raise ValueError("M10_INVALID_DIAGNOSTIC_CLOCK")

    path = (Path(db_path) if db_path is not None else
            Path.home() / ".cache" / "indodax-ai-bot-v2"
            / "rest-rate.sqlite3")
    result = {
        "scope": "V2_LOCAL_SQLITE_ONLY",
        "status": "LOCAL_DB_UNKNOWN",
        "db_path_scope": (
            "V2_DEFAULT" if db_path is None else "EXPLICIT_TEST_ONLY"
        ),
        "caller_env_override_declared": (
            bool(os.environ.get("INDODAX_PUBLIC_REST_RATE_DB"))
            if db_path is None else None
        ),
        "database_present": False,
        "rolling_60s_v2_reserved_requests": None,
        "durable_v2_cap_requests_60s": None,
        "snapshot_wal_possible": None,
        "live_v2_clients_same_db_verified": False,
        "outbound_public_ip_independently_verified": False,
        "all_egress_ip_consumers_verified": False,
        "external_rolling_60s_peak_verified": False,
        "external_rolling_60s_peak_requests": None,
        "m03_timer_go_authorized": False,
        "m10_05_full_acceptance": False,
    }

    try:
        info = path.lstat()  # no symlink following
    except FileNotFoundError:
        result["status"] = "V2_LOCAL_DB_ABSENT"
        return result
    except (OSError, ValueError):
        result["status"] = "V2_LOCAL_DB_STAT_UNAVAILABLE"
        return result

    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_size <= 0 or info.st_size > 268435456
            or info.st_mode & 0o022):
        result["status"] = "V2_LOCAL_DB_UNTRUSTED"
        return result
    result["database_present"] = True

    # Check sidecar existence without reading or writing them.
    try:
        wal = Path(str(path) + "-wal")
        journal = Path(str(path) + "-journal")
        if wal.exists() or journal.exists():
            result["snapshot_wal_possible"] = True
            result["status"] = "V2_LOCAL_DB_ACTIVE_JOURNAL_SNAPSHOT_UNRELIABLE"
            return result
        result["snapshot_wal_possible"] = False

        # SQLite's immutable URI avoids creating WAL/SHM files and acquires
        # no writer lock. Values remain local reservations, NOT actual HTTP.
        uri = "file:" + quote(str(path.absolute()), safe="/") + "?mode=ro&immutable=1"
        with sqlite3.connect(uri, uri=True, timeout=1.0) as con:
            con.execute("PRAGMA query_only=ON")
            tables = {
                row[0] for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "rest_get_reservations" not in tables:
                result["status"] = "V2_LOCAL_DB_NO_RESERVATION_TABLE"
                return result
            count_row = con.execute(
                "SELECT COUNT(*) FROM rest_get_reservations "
                "WHERE requested_at > ?",
                (float(current) - 60.0,),
            ).fetchone()
            if (not count_row or type(count_row[0]) is not int
                    or count_row[0] < 0):
                result["status"] = "V2_LOCAL_DB_INVALID_RESERVATIONS"
                return result
            result["rolling_60s_v2_reserved_requests"] = count_row[0]
            if "rest_shared_egress_cap" in tables:
                cap_row = con.execute(
                    "SELECT max_local_requests_60s "
                    "FROM rest_shared_egress_cap WHERE id=1"
                ).fetchone()
                if cap_row is not None:
                    if (type(cap_row[0]) is not int
                            or not 1 <= cap_row[0] <= 168):
                        result["status"] = "V2_LOCAL_DB_INVALID_CAP"
                        result["rolling_60s_v2_reserved_requests"] = None
                        return result
                    result["durable_v2_cap_requests_60s"] = cap_row[0]
            result["status"] = "V2_LOCAL_SNAPSHOT_ONLY_EXTERNAL_UNVERIFIED"
    except (OSError, sqlite3.Error, ValueError, TypeError):
        result["status"] = "V2_LOCAL_DB_READ_UNAVAILABLE"
        result["rolling_60s_v2_reserved_requests"] = None
        result["durable_v2_cap_requests_60s"] = None
    return result


def main() -> int:
    # No parser/flags, files, sockets, subprocesses, or host actions.
    result = inspect_v2_local_egress()
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    # Diagnostics complete != acceptance, even when this CLI exits 0.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
