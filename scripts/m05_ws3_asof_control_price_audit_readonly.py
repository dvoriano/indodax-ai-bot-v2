"""Original M05 WS3 ACK vs same-minute not-yet-alert control study (READ ONLY).

This is an observational price-only cohort audit, not a backtest, trade signal,
M04 MICRO PREALERT reconstruction, independent events, or net PnL.
No network calls, Telegram sends, service changes, AI calls, or orders.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
from zoneinfo import ZoneInfo

from indodax_ai_bot.m05_radar_momentum_nonlive import (
    PAIR_ID, _owned_private_regular,
)
from scripts.m05_signal_edge_audit_readonly import (
    HORIZONS, FRESHNESS_SECONDS, _positive_price,
    post_ack_price_proxy,
)

WIB = ZoneInfo("Asia/Jakarta")
VOLUME_MIN_RATIO = Decimal("0.5")
VOLUME_MAX_RATIO = Decimal("2")
MAX_ROWS = 20000
MAX_EVENTS = 100
MAX_CONTROLS = 6
PRIOR_CLAIM_SECONDS = 900


def _time(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, WIB).strftime("%Y-%m-%d %H:%M:%S WIB")


def _verified_path(path: Path, *, research: bool) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute() or not _owned_private_regular(p):
        raise ValueError("CONTROL_PRIVATE_SOURCE_UNTRUSTED")
    if research:
        try:
            parent = p.parent.lstat()
        except OSError:
            raise ValueError("CONTROL_RESEARCH_PARENT_UNTRUSTED") from None
        if (not stat.S_ISDIR(parent.st_mode)
                or parent.st_uid != os.geteuid()
                or stat.S_IMODE(parent.st_mode) != 0o700):
            raise ValueError("CONTROL_RESEARCH_PARENT_UNTRUSTED")
    return p


def _quotes_for_pair(rows: list[tuple], pair: str) -> list[tuple[int, Decimal]]:
    """Only accepted observational quote epochs, never cherry-pick conflicts."""
    prices = defaultdict(set)
    for p, minute, observed, price, volume, status, recorded in rows:
        if p == pair and status in (
            "ELIGIBLE_SOURCE_OBSERVATION", "BELOW_150M_VOLUME_FLOOR"
        ):
            prices[observed].add(price)
    return sorted((epoch, next(iter(vals)))
                  for epoch, vals in prices.items() if len(vals) == 1)


def _source_for_alert(rows: list[tuple], *, pair: str,
                      observed: int, claim: int) -> tuple | None:
    matches = [
        row for row in rows
        if row[0] == pair and row[2] == observed
        and row[5] == "ELIGIBLE_SOURCE_OBSERVATION"
        and row[6] <= claim and row[1] <= claim // 60 * 60
    ]
    # Detect conflicting source prices rather than selecting a favorable row.
    return max(matches, key=lambda x: x[6]) if matches and len(
        {row[3] for row in matches}) == 1 else None


def select_asof_controls(rows: list[tuple], *,
                         source: tuple, claim: int,
                         claims: dict[str, list[int]]) -> list[tuple]:
    """Deterministic cohort using ONLY info existing at alert claim time.

    Peer must be another eligible pair from the same original source minute,
    observed within 90s of alert source, 0.5-2x rolling 24h turnover,
    and have no earlier/current claimed owner alert within 15m.
    Future alerts are NEVER used to select or exclude controls.
    """
    anchor_pair, minute, exchange, _, volume, _, _ = source
    possible = {}
    for row in rows:
        pair, m, observed, price, other_volume, status, recorded = row
        if (pair == anchor_pair or m != minute
                or status != "ELIGIBLE_SOURCE_OBSERVATION"
                or recorded > claim or abs(observed - exchange) > 90):
            continue
        ratio = other_volume / volume
        if not VOLUME_MIN_RATIO <= ratio <= VOLUME_MAX_RATIO:
            continue
        if any(0 <= claim - prior <= PRIOR_CLAIM_SECONDS
               for prior in claims[pair]):
            continue
        previous = possible.get(pair)
        current_key = (abs(ratio - 1), pair)
        if previous is None or current_key < previous[0]:
            possible[pair] = (current_key, row)
    return [pairrow for _, pairrow in sorted(
        possible.values(), key=lambda x: x[0]
    )[:MAX_CONTROLS]]


def compare_asof_ws3_controls_readonly(*, start_epoch: int, end_epoch: int,
                                      pairs: tuple[str, ...],
                                      owner_db: Path, research_db: Path,
                                      now_epoch: int | None = None) -> dict:
    """Use exact natural Bot API ACKs and pre-ACK frozen peer selection.

    The later 5m/15m outcomes must never influence candidate selection.
    The comparison is descriptive, not independent or strategy causal proof.
    """
    now = int(time.time()) if now_epoch is None else now_epoch
    if (type(now) is not int or type(start_epoch) is not int
            or type(end_epoch) is not int or start_epoch <= 0
            or end_epoch < start_epoch or start_epoch % 60
            or end_epoch % 60 or end_epoch - start_epoch > 120 * 60
            or end_epoch > now or type(pairs) is not tuple
            or not 1 <= len(pairs) <= 10 or len(pairs) != len(set(pairs))
            or any(type(p) is not str or not PAIR_ID.fullmatch(p)
                   for p in pairs)):
        raise ValueError("INVALID_CONTROL_AUDIT_SCOPE")
    owner = _verified_path(owner_db, research=False)
    research = _verified_path(research_db, research=True)
    with sqlite3.connect(owner.as_uri() + "?mode=ro",
                         uri=True, timeout=5) as db:
        db.execute("PRAGMA query_only=ON")
        placeholders = ",".join("?" for _ in pairs)
        events = db.execute(
            "SELECT pair,claimed_epoch,source_observed_epoch,"
            "ack_received_epoch FROM m05_radar_delivery_events "
            "WHERE source_kind='WS3' AND delivery_state='ACKED' "
            f"AND pair IN ({placeholders}) "
            "AND claimed_epoch BETWEEN ? AND ? "
            "ORDER BY claimed_epoch,pair LIMIT ?",
            (*pairs, start_epoch, end_epoch + 59, MAX_EVENTS + 1),
        ).fetchall()
        if len(events) > MAX_EVENTS:
            raise ValueError("CONTROL_TOO_MANY_ALERT_EVENTS")
        all_claims = db.execute(
            "SELECT pair,claimed_epoch FROM m05_radar_delivery_events "
            "WHERE claimed_epoch BETWEEN ? AND ? "
            "ORDER BY claimed_epoch,pair LIMIT 20001",
            (start_epoch - PRIOR_CLAIM_SECONDS,
             end_epoch + 59 + PRIOR_CLAIM_SECONDS),
        ).fetchall()
        if len(all_claims) > MAX_ROWS:
            raise ValueError("CONTROL_CLAIM_SOURCE_TOO_LARGE")

    claims_by_pair = defaultdict(list)
    for pair, claimed in all_claims:
        if not isinstance(pair, str) or type(claimed) is not int:
            raise ValueError("CONTROL_INVALID_CLAIM_ROW")
        claims_by_pair[pair].append(claimed)

    with sqlite3.connect(research.as_uri() + "?mode=ro",
                         uri=True, timeout=5) as db:
        db.execute("PRAGMA query_only=ON")
        # The same finite archive of original naturally observed WS3 frames.
        # Include 15m post-ACK + 90s and bucket alignment margin.
        raw = db.execute(
            "SELECT e.pair,e.minute_epoch,e.source_exchange_epoch,"
            "e.last_idr,e.rolling_24h_idr,e.source_status,"
            "c.recorded_local_epoch "
            "FROM ws3_source_evidence e "
            "JOIN ws3_source_cycles c USING(minute_epoch) "
            "WHERE e.minute_epoch BETWEEN ? AND ? "
            "ORDER BY e.minute_epoch,e.pair LIMIT ?",
            (start_epoch - 300,
             min(now, end_epoch + 1200), MAX_ROWS + 1),
        ).fetchall()
    if len(raw) > MAX_ROWS:
        raise ValueError("CONTROL_RESEARCH_RESULT_TOO_LARGE")
    rows = []
    for pair, minute, observed, p, v, status, recorded in raw:
        if status not in (
            "ELIGIBLE_SOURCE_OBSERVATION", "BELOW_150M_VOLUME_FLOOR"
        ):
            continue
        if (type(pair) is not str or not PAIR_ID.fullmatch(pair)
                or type(minute) is not int or minute % 60
                or type(observed) is not int or type(recorded) is not int
                or not minute - 90 <= observed <= minute + 59
                or not observed <= recorded <= now or recorded < minute):
            raise ValueError("CONTROL_INVALID_RESEARCH_SOURCE")
        price = _positive_price(p)
        volume = _positive_price(v)
        if (status == "ELIGIBLE_SOURCE_OBSERVATION"
                and volume < Decimal("150000000")):
            raise ValueError("CONTROL_INVALID_ELIGIBLE_VOLUME")
        rows.append((pair, minute, observed, price, volume, status, recorded))
    rows_by_minute = defaultdict(list)
    for row in rows:
        rows_by_minute[row[1]].append(row)

    all_outcomes = {}
    def outcomes(pair: str) -> list[tuple[int, Decimal]]:
        if pair not in all_outcomes:
            all_outcomes[pair] = _quotes_for_pair(rows, pair)
        return all_outcomes[pair]

    report = []
    pairs_with_controls = 0
    selected = 0
    paired = Counter()
    alert_measured = Counter()
    control_measured = Counter()

    for pair, claim, observed, ack in events:
        if (type(pair) is not str or type(claim) is not int
                or type(observed) is not int or type(ack) is not int
                or not observed <= claim <= ack <= now):
            raise ValueError("CONTROL_INVALID_DELIVERY_EVENT")
        all_source = [row for row in rows
                      if row[0] == pair and row[2] == observed]
        source = _source_for_alert(all_source, pair=pair,
                                   observed=observed, claim=claim)
        if source is None:
            report.append({
                "pair": pair, "ack_wib": _time(ack),
                "status": "ALERT_SOURCE_NOT_VERIFIED",
                "controls": [], "alert_price_proxies": {},
            })
            continue
        peers = select_asof_controls(
            rows_by_minute[source[1]],
            source=source, claim=claim, claims=claims_by_pair,
        )
        pairs_with_controls += bool(peers)
        selected += len(peers)
        notice_outcomes = {}
        peer_outcomes = {p[0]: {} for p in peers}
        paired_event = {}
        for horizon in HORIZONS:
            key = str(horizon // 60) + "m"
            alert_proxy = post_ack_price_proxy(
                outcomes(pair), ack_epoch=ack,
                horizon_seconds=horizon, now_epoch=now,
            )
            notice_outcomes[key] = alert_proxy
            if alert_proxy["status"] == "OBSERVED_PRICE_ONLY_NOT_EXECUTABLE":
                alert_measured[key] += 1
            peer_gross = []
            for candidate in peers:
                cp = candidate[0]
                out = post_ack_price_proxy(
                    outcomes(cp), ack_epoch=ack,
                    horizon_seconds=horizon, now_epoch=now,
                )
                peer_outcomes[cp][key] = out
                if out["status"] == "OBSERVED_PRICE_ONLY_NOT_EXECUTABLE":
                    control_measured[key] += 1
                    peer_gross.append(Decimal(out["gross_move_bps"]))
            if (alert_proxy["status"] == "OBSERVED_PRICE_ONLY_NOT_EXECUTABLE"
                    and peer_gross):
                paired[key] += 1
                alert_value = Decimal(alert_proxy["gross_move_bps"])
                control_mean = sum(peer_gross) / len(peer_gross)
                paired_event[key] = {
                    "status": "PAIRED_OBSERVED_PRICE_ONLY",
                    "control_quotes_available": len(peer_gross),
                    "alert_gross_bps": str(alert_value),
                    "control_mean_gross_bps": str(
                        control_mean.quantize(Decimal("0.01"))
                    ),
                    "difference_bps": str(
                        (alert_value - control_mean).quantize(Decimal("0.01"))
                    ),
                }
            else:
                paired_event[key] = {
                    "status": "NO_PAIRED_FRESH_OUTCOME",
                    "control_quotes_available": len(peer_gross),
                }
        controls = []
        for row in peers:
            cp = row[0]
            future_cross = any(
                claim < later <= claim + PRIOR_CLAIM_SECONDS
                for later in claims_by_pair[cp]
            )
            controls.append({
                "pair": cp,
                "turnover_ratio_vs_alert": str(
                    (row[4] / source[4]).quantize(Decimal("0.0001"))
                ),
                "became_alert_within_next_15m": future_cross,
                "price_proxies": peer_outcomes[cp],
            })
        report.append({
            "pair": pair, "ack_wib": _time(ack),
            "status": "MATCHED_WS3_ACK_ARCHIVE",
            "source_observed_wib": _time(observed),
            "source_to_ack_seconds": ack - observed,
            "controls": controls,
            "alert_price_proxies": notice_outcomes,
            "paired_outcomes": paired_event,
        })
    return {
        "status": "M05_WS3_ASOF_CONTROL_PRICE_AUDIT_READONLY",
        "window_start_wib": _time(start_epoch),
        "window_end_wib": _time(end_epoch + 59),
        "events": len(events),
        "matched_alerts": sum(
            e["status"] == "MATCHED_WS3_ACK_ARCHIVE" for e in report
        ),
        "alerts_with_controls": pairs_with_controls,
        "control_selections": selected,
        "five_min_alert_price_outcomes": alert_measured["5m"],
        "five_min_control_price_outcomes": control_measured["5m"],
        "five_min_paired_events": paired["5m"],
        "fifteen_min_alert_price_outcomes": alert_measured["15m"],
        "fifteen_min_control_price_outcomes": control_measured["15m"],
        "fifteen_min_paired_events": paired["15m"],
        "control_matching_uses_asof_claim_data_only": True,
        "future_alerts_are_not_excluded_from_control_selection": True,
        "control_crossover_later_is_descriptive_only": True,
        "same_control_pairs_may_repeat_not_independent": True,
        "rolling_24h_idr_not_1m_executed_turnover": True,
        "not_causal_or_out_of_sample": True,
        "not_realized_profit_or_executable_orders": True,
        "source_universe_partial_retention_bounded": True,
        "exchange_requests": 0,
        "telegram_messages_sent": 0,
        "paid_provider_calls": 0,
        "orders_sent": 0,
        "execution_allowed": False,
        "cohort": report,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--pairs", required=True)
    parser.add_argument("--owner-db", type=Path, required=True)
    parser.add_argument("--research-db", type=Path, required=True)
    args = parser.parse_args()
    try:
        date = datetime.strptime(args.date, "%Y-%m-%d").date()
        start = datetime.combine(
            date, datetime.strptime(args.start, "%H:%M").time(), WIB)
        end = datetime.combine(
            date, datetime.strptime(args.end, "%H:%M").time(), WIB)
        report = compare_asof_ws3_controls_readonly(
            start_epoch=int(start.timestamp()), end_epoch=int(end.timestamp()),
            pairs=tuple(p.strip().lower() for p in args.pairs.split(",")),
            owner_db=args.owner_db, research_db=args.research_db,
        )
    except (ValueError, sqlite3.Error, OSError, OverflowError) as exc:
        print(json.dumps({
            "status": "M05_CONTROL_AUDIT_BLOCKED",
            "reason": str(exc) if isinstance(exc, ValueError) else
            "SOURCE_IO_OR_SCHEMA",
            "execution_allowed": False, "orders_sent": 0,
        }, sort_keys=True))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
