"""M03→M05 owner radar WATCH from ORIGINAL V2 minute ticker evidence only.

Optional one-send-per-existing-M05-loop informational notice. Reads the SAME
original M03 DecisionJournal SQLite in query_only mode; uses the SAME M05
owner SQLite to durably claim BEFORE Telegram send. No new scanner, HTTP
price fetch, second bot, order, M04 provider, owner PASS/REJECT proposal,
or G1 independent-source promotion.

A gap between two actual ticker samples is called out, never interpolated
as a 1m candle or certified 1m return. The user must separately opt into
M05_RADAR_MOMENTUM_ALERT_GO=YES on the original M05 service after the ongoing
24h M05 heartbeat audit. Importing this module never changes the service.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import os
import re
import sqlite3
import stat
import time
from zoneinfo import ZoneInfo

from .scanner import MAIN_EXCLUDED_PAIRS

DEFAULT_DB = Path.home() / "indodax-ai-bot-v2" / "runtime" / "decisions.sqlite3"
MIN_TURNOVER_IDR = Decimal("150000000")
MIN_RISE_PCT = Decimal("8")
MIN_EARLY_RISE_PCT = Decimal("5")
MAX_SPREAD_BPS = Decimal("100")
MAX_AGE_SECONDS = 90
LOOKBACK_SECONDS = 300
PAIR_COOLDOWN_SECONDS = 900
GLOBAL_SPACING_SECONDS = 60
PAIR_ID = re.compile(r"^[a-z0-9]+_idr$")


def _owned_private_regular(path: Path) -> bool:
    try:
        st = path.lstat()
        return (stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid()
                and st.st_nlink == 1 and st.st_mode & 0o077 == 0
                and st.st_size > 0)
    except OSError:
        return False


def _finite_number(value, *, positive=False) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        return None
    try:
        n = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if (not n.is_finite() or abs(n.adjusted()) > 20
            or (n <= 0 if positive else n < 0)):
        return None
    return n


def inspect_original_m03_radar_readonly(
    *, db_path: Path = DEFAULT_DB, now_epoch: int | None = None,
) -> dict:
    """Bounded factual top-candidate radar; not an order/entry signal.

    The latest *actual* minute sample must be <=90s old. Referenced 2–5m
    price is the oldest retained sample within a 5-minute period. Missing
    intermediate minutes are disclosed, not filled in. A casebook EARLY_WATCH
    is provenance of an uncalibrated historical watch, never G1 or AI.
    """
    now = int(time.time()) if now_epoch is None else now_epoch
    if type(now) is not int or now <= 0:
        raise ValueError("M03_RADAR_INVALID_CLOCK")
    path = Path(db_path)
    if not _owned_private_regular(path):
        return {"status": "M03_RADAR_ORIGINAL_DB_NOT_TRUSTED", "candidates": []}
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            db.execute("PRAGMA query_only=ON")
            source_tables = {
                x[0] for x in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name IN ('public_ticker_minute_samples','public_surge_cases')"
                ).fetchall()
            }
            if "public_ticker_minute_samples" not in source_tables:
                return {"status": "M03_RADAR_ORIGINAL_SAMPLE_TABLE_ABSENT",
                        "candidates": []}
            # Bounded indexed same ORIGINAL M03 archive, no network I/O.
            rows = db.execute(
                "SELECT pair,minute_epoch,observed_epoch,last_idr,"
                "volume_24h_idr,spread_bps "
                "FROM public_ticker_minute_samples "
                "WHERE minute_epoch BETWEEN ? AND ? "
                "ORDER BY minute_epoch DESC LIMIT 4000",
                (now - LOOKBACK_SECONDS - 60, now),
            ).fetchall()
            watches = set()
            if "public_surge_cases" in source_tables:
                for pair, minute, cohort in db.execute(
                    "SELECT pair,minute_epoch,cohort FROM public_surge_cases "
                    "WHERE minute_epoch BETWEEN ? AND ? LIMIT 1000",
                    (now - LOOKBACK_SECONDS, now),
                ).fetchall():
                    if (type(pair) is str and PAIR_ID.fullmatch(pair)
                            and type(minute) is int
                            and cohort == "EARLY_WATCH"):
                        watches.add((pair, minute))
    except (sqlite3.Error, OSError, ValueError):
        return {"status": "M03_RADAR_ORIGINAL_QUERY_UNAVAILABLE",
                "candidates": []}

    by_pair = defaultdict(list)
    for pair, minute, observed, price, volume, spread in rows:
        if (type(pair) is not str or not PAIR_ID.fullmatch(pair)
                or pair in MAIN_EXCLUDED_PAIRS
                or type(minute) is not int or minute <= 0 or minute % 60 != 0
                or type(observed) is not int
                or observed < minute or observed >= minute + 60
                or observed > now):
            continue
        p = _finite_number(price, positive=True)
        v = _finite_number(volume, positive=True)
        s = _finite_number(spread)
        if p is None or v is None or s is None:
            continue
        by_pair[pair].append((minute, observed, p, v, s))

    candidates = []
    for pair, samples in by_pair.items():
        latest = samples[0]  # ORDER BY minute_epoch DESC
        if (now - latest[1] > MAX_AGE_SECONDS
                or latest[3] < MIN_TURNOVER_IDR
                or latest[4] > MAX_SPREAD_BPS):
            continue
        older = [
            x for x in samples[1:]
            if 60 <= latest[0] - x[0] <= LOOKBACK_SECONDS
        ]
        if not older:
            continue
        reference = min(older, key=lambda x: x[0])
        rise_pct = (latest[2] / reference[2] - 1) * Decimal(100)
        if not rise_pct.is_finite() or rise_pct > Decimal("500"):
            continue
        early = any(
            p == pair and latest[0] - 120 <= minute <= latest[0]
            for p, minute in watches
        )
        if not (
            rise_pct >= MIN_RISE_PCT or
            (early and rise_pct >= MIN_EARLY_RISE_PCT)
        ):
            continue
        actual_minutes = {x[0] for x in samples
                          if reference[0] <= x[0] <= latest[0]}
        expected = (latest[0] - reference[0]) // 60 + 1
        candidates.append({
            "pair": pair,
            "observed_epoch": latest[1],
            "reference_epoch": reference[1],
            "latest_price_idr": str(latest[2]),
            "reference_price_idr": str(reference[2]),
            "observed_rise_pct": format(rise_pct.quantize(Decimal("0.01")), "f"),
            "reference_interval_seconds": latest[0] - reference[0],
            "missing_intermediate_minute_samples": len(actual_minutes) < expected,
            "casebook_early_watch_observed": early,
            "sample_age_seconds": now - latest[1],
            "volume_24h_idr": str(latest[3]),
            "spread_bps": str(latest[4]),
            "no_g1_or_ai_or_order_authority": True,
        })
    candidates.sort(
        key=lambda x: (-Decimal(x["observed_rise_pct"]), x["pair"])
    )
    return {
        "status": "M03_ORIGINAL_PUBLIC_RADAR_OBSERVED_UNATTESTED",
        "candidates": candidates[:5],
        "source_authenticated_for_g1": False,
        "three_ai_role_responses_observed": False,
        "owner_order_specific_final_verified": False,
        "execution_allowed": False,
    }


def _claim_once(ledger, pair: str, observed_epoch: int,
                now_epoch: int) -> bool:
    """At-most-once owner notice attempt; no replay on Telegram uncertainty."""
    dbpath = Path(ledger.db_path)
    if not _owned_private_regular(dbpath):
        return False
    try:
        with sqlite3.connect(dbpath, timeout=2) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""
                CREATE TABLE IF NOT EXISTS m05_radar_momentum_attempts (
                    pair TEXT PRIMARY KEY,
                    claimed_epoch INTEGER NOT NULL,
                    source_observed_epoch INTEGER NOT NULL
                )
            """)
            if db.execute(
                "SELECT 1 FROM m05_radar_momentum_attempts "
                "WHERE claimed_epoch > ? LIMIT 1",
                (now_epoch - GLOBAL_SPACING_SECONDS,),
            ).fetchone():
                return False
            row = db.execute(
                "SELECT claimed_epoch,source_observed_epoch "
                "FROM m05_radar_momentum_attempts WHERE pair=?", (pair,),
            ).fetchone()
            if row and (
                row[0] >= now_epoch - PAIR_COOLDOWN_SECONDS
                or row[0] >= now_epoch
                or row[1] >= observed_epoch
            ):
                return False
            db.execute("""
                INSERT INTO m05_radar_momentum_attempts(
                    pair,claimed_epoch,source_observed_epoch
                ) VALUES(?,?,?)
                ON CONFLICT(pair) DO UPDATE SET
                    claimed_epoch=excluded.claimed_epoch,
                    source_observed_epoch=excluded.source_observed_epoch
            """, (pair, now_epoch, observed_epoch))
        return True
    except (sqlite3.Error, OSError):
        return False


def send_one_owner_radar_momentum_nonlive(
    *, api, ledger, now_epoch: int | None = None,
    db_path: Path = DEFAULT_DB,
) -> dict:
    """Existing M05 private Bot API sender, no callback buttons, no order."""
    now = int(time.time()) if now_epoch is None else now_epoch
    from .owner_control import OwnerDecisionLedger
    if type(now) is not int or now <= 0:
        raise ValueError("M05_RADAR_CLOCK_INVALID")
    if not isinstance(ledger, OwnerDecisionLedger):
        return {"status": "M05_RADAR_OWNER_LEDGER_UNVERIFIED",
                "messages_sent": 0}
    snap = inspect_original_m03_radar_readonly(
        db_path=db_path, now_epoch=now,
    )
    if snap["status"] != "M03_ORIGINAL_PUBLIC_RADAR_OBSERVED_UNATTESTED":
        return {"status": "M05_RADAR_ORIGINAL_SOURCE_UNAVAILABLE",
                "messages_sent": 0}
    if not snap["candidates"]:
        return {"status": "M05_RADAR_NO_CURRENT_QUALIFIED_WATCH",
                "messages_sent": 0}
    for candidate in snap["candidates"]:
        if not _claim_once(
            ledger, candidate["pair"], candidate["observed_epoch"], now
        ):
            continue
        wib = ZoneInfo("Asia/Jakarta")
        from_time = datetime.fromtimestamp(
            candidate["reference_epoch"], wib
        ).strftime("%H:%M WIB")
        to_time = datetime.fromtimestamp(
            candidate["observed_epoch"], wib
        ).strftime("%H:%M WIB")
        risk = (
            "RISIKO: ada menit tanpa sampel; ini BUKAN return candle 1m."
            if candidate["missing_intermediate_minute_samples"]
            else "RISIKO: momentum dapat berbalik; kualitas entry belum dinilai."
        )
        stage = (
            "EARLY_WATCH historis teramati, BELUM terkalibrasi."
            if candidate["casebook_early_watch_observed"]
            else "Lonjakan harga antar-sampel teramati; setup belum diketahui."
        )
        message = (
            "RADAR MOMENTUM M03 | OBSERVASI, BUKAN REKOMENDASI BUY\n"
            f"Pair: {candidate['pair'].upper()}\n"
            f"Periode sampel: {from_time} ke {to_time}\n"
            f"Harga tercatat: Rp{candidate['reference_price_idr']}"
            f" ke Rp{candidate['latest_price_idr']}\n"
            f"Kenaikan antar-sampel: +{candidate['observed_rise_pct']}%\n"
            f"Volume 24 jam teramati: Rp{candidate['volume_24h_idr']}\n"
            f"{stage}\n{risk}\n"
            "G1, AI MARKET/RISK/STRATEGY, akun dan order: BELUM TERVERIFIKASI.\n"
            "Hanya WATCH. Tidak ada tombol PASS/REJECT atau BUY/SELL."
        )
        # Claim is durable BEFORE send; uncertainty must never replay it.
        try:
            msg = api.call("sendMessage", {
                "chat_id": ledger.owner_user_id,
                "text": message,
            })
            if (not isinstance(msg, dict)
                    or type(msg.get("message_id")) is not int
                    or msg["message_id"] <= 0
                    or not isinstance(msg.get("chat"), dict)
                    or msg["chat"].get("id") != ledger.owner_user_id
                    or msg["chat"].get("type") != "private"):
                return {
                    "status": "M05_RADAR_DELIVERY_UNCERTAIN_NO_RETRY",
                    "messages_sent": 0,
                }
        except Exception:
            # Optional radar must not stop the original owner receiver.
            return {
                "status": "M05_RADAR_DELIVERY_UNCERTAIN_NO_RETRY",
                "messages_sent": 0,
            }
        return {
            "status": "M05_RADAR_OWNER_PRIVATE_NOTICE_SENT",
            "messages_sent": 1, "execution_allowed": False,
            "owner_final_decision": None,
        }
    return {"status": "M05_RADAR_THROTTLED",
            "messages_sent": 0}
