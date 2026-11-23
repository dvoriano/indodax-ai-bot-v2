"""Original M09 WS3 versus INDODAX official public 1m OHLCV, RESEARCH ONLY.

One operator-invoked optional HTTPS GET per original M04 WATCH pair. All live
orders, PAPER fills, Telegram, private endpoints, paid AI and original M03/M04
runtime remain untouched. A candle close is a *minute-end reference*, NEVER
a quote/executable price at the exact WATCH horizon second.

Read only: validates the same owner-only 0600 captured M04 evidence and the
original M09 source first. Does not alter/repair WS3 UNKNOWN.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from indodax_ai_bot.m05_radar_momentum_nonlive import DEFAULT_DB
from scripts.m09_ws3_watch_coverage_audit_readonly import (
    audit_saved_ws3_watch_coverage_readonly,
)

_STAGE = "M09_INDODAX_PUBLIC_1M_CANDLE_REFERENCE_READONLY"
_API = "https://indodax.com/tradingview/history_v2"
_PAIR = re.compile(r"[a-z0-9]+_idr\Z")
_HORIZONS = ("5m", "15m", "30m", "60m")
_MAX_RESPONSE_BYTES = 262144
_MAX_CANDLES = 160
_MAX_SPAN = 2 * 3600
_MIN_EPOCH = 1_500_000_000


def _blocked(reason: str) -> dict:
    return {
        "stage": _STAGE, "status": reason,
        "candidate_count": 0, "observations": [],
        "candle_references_observed": 0,
        "independent_g1_verified": False,
        "paper_fill_created": False, "paper_orders_created": 0,
        "provider_calls": 0, "telegram_messages_sent": 0,
        "order_sent": False, "execution_allowed": False,
    }


def _positive(raw) -> Decimal:
    if type(raw) not in (int, float, str, Decimal):
        raise ValueError("UNTRUSTED_NUMERIC_TYPE")
    if type(raw) is str and not 0 < len(raw) <= 64:
        raise ValueError("UNTRUSTED_NUMERIC_LENGTH")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise ValueError("UNTRUSTED_NUMBER") from None
    if not value.is_finite() or not 0 < value < Decimal("1e25"):
        raise ValueError("UNTRUSTED_NONPOSITIVE_NUMBER")
    return value


def _volume(raw) -> Decimal:
    if type(raw) not in (int, float, str, Decimal):
        raise ValueError("UNTRUSTED_VOLUME_TYPE")
    if type(raw) is str and not 0 < len(raw) <= 64:
        raise ValueError("UNTRUSTED_VOLUME_LENGTH")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise ValueError("UNTRUSTED_VOLUME") from None
    if not value.is_finite() or not 0 <= value < Decimal("1e30"):
        raise ValueError("UNTRUSTED_VOLUME")
    return value


def _validate_pair(pair: str) -> str:
    if type(pair) is not str or not _PAIR.fullmatch(pair):
        raise ValueError("PAIR_INVALID")
    # Official public endpoint expects BTCIDR-style symbols, not btc_idr.
    return pair.replace("_", "").upper()


def fetch_indodax_public_1m_candles(
        pair: str, *, from_epoch: int, to_epoch: int) -> object:
    """Explicit, fixed-host unauthenticated GET only. Never include API keys."""
    symbol = _validate_pair(pair)
    if (type(from_epoch) is not int or type(to_epoch) is not int
            or from_epoch < _MIN_EPOCH or to_epoch < from_epoch
            or to_epoch - from_epoch > _MAX_SPAN):
        raise ValueError("CANDLE_REQUEST_WINDOW_INVALID")
    url = _API + "?" + urlencode({
        "from": from_epoch, "symbol": symbol, "tf": "1", "to": to_epoch,
    })
    req = Request(url, headers={"User-Agent": "indodax-v2-m09-research/1.0"},
                  method="GET")
    # Explicit max body, fixed origin, short timeout, and no credentials.
    with urlopen(req, timeout=10) as response:
        if response.status != 200:
            raise OSError("PUBLIC_CANDLE_HTTP_STATUS")
        body = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(body) > _MAX_RESPONSE_BYTES:
            raise ValueError("PUBLIC_CANDLE_BODY_OVERSIZED")
    return json.loads(body.decode("utf-8"), parse_float=Decimal)


def _parse_candles(payload: object, *, from_epoch: int, to_epoch: int) -> dict:
    """Validated closed-bar source candidates, with no implicit fill."""
    if type(payload) is not list or len(payload) > _MAX_CANDLES:
        raise ValueError("CANDLE_RESPONSE_NOT_BOUNDED_ARRAY")
    candles = {}
    for entry in payload:
        if type(entry) is not dict or type(entry.get("Time")) is not int:
            raise ValueError("CANDLE_TIMESTAMP_INVALID")
        minute = entry["Time"]
        if (minute % 60 != 0 or not from_epoch <= minute <= to_epoch
                or minute in candles):
            raise ValueError("CANDLE_TIMESTAMP_DUPLICATE_OR_OUT_OF_RANGE")
        o = _positive(entry.get("Open"))
        h = _positive(entry.get("High"))
        l = _positive(entry.get("Low"))
        c = _positive(entry.get("Close"))
        vol = _volume(entry.get("Volume"))
        if not l <= min(o, c) <= max(o, c) <= h:
            raise ValueError("CANDLE_OHLC_INCONSISTENT")
        candles[minute] = (c, vol)
    return candles


def compare_m09_with_indodax_candles_readonly(
        *, capture_file: Path, source_db: Path, evaluation_epoch: int,
        fetch_public_ohlcv: bool = False, fetcher=None) -> dict:
    """Cross-check original M09 outcomes; NEVER replace their statuses.

    The caller must explicitly opt in to public GET. Tests inject a fixture
    fetcher; importing this module never causes a request.
    """
    if type(fetch_public_ohlcv) is not bool or not fetch_public_ohlcv:
        return _blocked("EXPLICIT_PUBLIC_CANDLE_FETCH_OPT_IN_REQUIRED")
    if type(evaluation_epoch) is not int or evaluation_epoch <= 0:
        return _blocked("EVALUATION_CLOCK_INVALID")

    original = audit_saved_ws3_watch_coverage_readonly(
        capture_file=capture_file, source_db=source_db,
        evaluation_epoch=evaluation_epoch)
    if original.get("status") != "LOCAL_CAPTURE_COVERAGE_RESEARCH_ONLY":
        return _blocked("ORIGINAL_WS3_" + str(original.get("status")))
    originals = original.get("observations")
    if (type(originals) is not list or len(originals) not in (1, 2)
            or len(originals) != original.get("candidate_count")):
        return _blocked("ORIGINAL_COHORT_INVALID")

    result = {
        "stage": _STAGE,
        "status": "PUBLIC_1M_CANDLE_REFERENCE_RESEARCH_ONLY",
        "candles_api_endpoint": "/tradingview/history_v2",
        "candle_resolution_seconds": 60,
        "candle_uses_close_of_target_minute_not_target_second": True,
        "candle_closes_must_be_known_by_evaluation": True,
        "candle_data_is_retrieved_retroactively": True,
        "historical_candle_immutability_verified": False,
        "candle_close_is_not_executable_last_price": True,
        "candle_reference_does_not_resolve_ws3_unknown": True,
        "original_ws3_results_preserved": True,
        "m04_candidates_reselected": False,
        "independent_g1_verified": False,
        "real_ai_role_responses_observed": False,
        "complete_trade_tape_verified": False,
        "net_roi_or_market_fills_verified": False,
        "candidate_count": len(originals),
        "captured_epoch": original["captured_epoch"],
        "evaluated_epoch": evaluation_epoch,
        "original_ws3_matured_horizons": original["matured_horizons"],
        "original_ws3_observed_horizons": original["observed_matured_horizons"],
        "original_ws3_unknown_horizons": original["unknown_matured_horizons"],
        "original_ws3_coverage_percent": original["observed_coverage_percent"],
        "candle_references_observed": 0,
        "candle_minutes_present": 0,
        "candle_zero_volume_references": 0,
        "public_rest_gets_attempted": 0,
        "public_rest_gets_validated": 0,
        "observations": [],
        "paper_fill_created": False,
        "paper_orders_created": 0,
        "provider_calls": 0,
        "telegram_messages_sent": 0,
        "order_sent": False,
        "execution_allowed": False,
    }
    request = fetcher if fetcher is not None else fetch_indodax_public_1m_candles
    for item in originals:
        try:
            pair = item["pair"]
            symbol = _validate_pair(pair)
            baseline = _positive(item["source_price_idr"])
            points = item["coverage"]
            targets = [points[h]["target_epoch"] for h in _HORIZONS]
            if any(type(t) is not int or t < _MIN_EPOCH for t in targets):
                return _blocked("ORIGINAL_HORIZON_INVALID")
        except (ValueError, TypeError, KeyError):
            return _blocked("ORIGINAL_PAIR_INVALID")

        from_epoch = min(targets) // 60 * 60
        # Fetch no bar whose *open* is in the future. Even if the API
        # responds with a current partial bar, it will not be reported
        # as an observed closed candle unless its close is <= evaluation.
        to_epoch = min(max(targets) // 60 * 60, evaluation_epoch // 60 * 60)
        fetch_status = "PUBLIC_CANDLES_NOT_REQUESTED_YET"
        candles = {}
        if to_epoch >= from_epoch:
            result["public_rest_gets_attempted"] += 1
            try:
                payload = request(pair, from_epoch=from_epoch, to_epoch=to_epoch)
                candles = _parse_candles(payload, from_epoch=from_epoch,
                                         to_epoch=to_epoch)
                result["public_rest_gets_validated"] += 1
                fetch_status = "PUBLIC_CANDLES_RETRIEVED_AND_VALIDATED"
            except (HTTPError, URLError, OSError, ValueError, TypeError,
                    TimeoutError, UnicodeError, json.JSONDecodeError):
                fetch_status = "PUBLIC_CANDLES_UNAVAILABLE_OR_INVALID"
        window_reports = {}
        for h in _HORIZONS:
            point = points[h]
            target = point["target_epoch"]
            minute = target // 60 * 60
            closed = minute + 60
            ws3_status = point["outcome_status"]
            report = {
                "target_epoch": target,
                "ws3_original_outcome_status": ws3_status,
                "ws3_original_coverage_class": point["coverage_class"],
                "ws3_original_gross_price_return_percent":
                    point["observed_gross_price_return_percent"],
                "candle_minute_open_epoch": minute,
                "candle_minute_close_epoch": closed,
                "candle_status": None,
                "candle_close_idr": None,
                "candle_volume_units": None,
                "candle_ohlc_returned_and_validated": False,
                "candle_zero_volume_reference_close_idr": None,
                "candle_zero_volume_reference_gross_price_change_from_original_ws3_baseline_percent": None,
                "candle_zero_volume_reference_is_not_trade_evidence": True,
                "candle_close_gross_price_change_from_original_ws3_baseline_percent": None,
                "candle_is_quote_at_exact_target_second": False,
                "fee_spread_slippage_or_fill_verified": False,
            }
            if evaluation_epoch < closed:
                report["candle_status"] = "CANDLE_MINUTE_NOT_YET_CLOSED"
            elif fetch_status != "PUBLIC_CANDLES_RETRIEVED_AND_VALIDATED":
                report["candle_status"] = fetch_status
            elif minute not in candles:
                report["candle_status"] = "CANDLE_MINUTE_NOT_RETURNED"
            else:
                close, volume = candles[minute]
                report["candle_ohlc_returned_and_validated"] = True
                report["candle_volume_units"] = str(volume)
                result["candle_minutes_present"] += 1
                if volume == 0:
                    # Zero-volume OHLCV can carry forward a historical price.
                    # Show this source value, but NEVER promote it to an
                    # observed trade, timed WS3 price, PAPER fill, or net ROI.
                    report["candle_status"] = "CANDLE_ZERO_VOLUME_UNVERIFIED"
                    report["candle_zero_volume_reference_close_idr"] = str(close)
                    report["candle_zero_volume_reference_gross_price_change_from_original_ws3_baseline_percent"] = (
                        str((close / baseline - 1) * Decimal(100)))
                    result["candle_zero_volume_references"] += 1
                else:
                    report["candle_status"] = "CLOSED_1M_CANDLE_REFERENCE_OBSERVED"
                    report["candle_close_idr"] = str(close)
                    report["candle_close_gross_price_change_from_original_ws3_baseline_percent"] = (
                        str((close / baseline - 1) * Decimal(100)))
                    result["candle_references_observed"] += 1
            window_reports[h] = report
        result["observations"].append({
            "pair": pair,
            "api_symbol": symbol,
            "api_symbol_verified_against_pairs_listing": False,
            "fetch_status": fetch_status,
            "original_m04_evidence_digest_sha256": item["m04_evidence_digest_sha256"],
            "original_ws3_baseline_idr": str(baseline),
            "horizons": window_reports,
            "market_decision_or_execution_authorized": False,
        })
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare original M09 local WS3 with official closed 1m Indodax candles")
    parser.add_argument("--audit-capture", required=True, metavar="PRIVATE_0600_JSON")
    parser.add_argument("--source-db", default=str(DEFAULT_DB))
    parser.add_argument("--fetch-public-ohlcv", action="store_true",
                        help="Explicitly allow at most two bounded unauthenticated public HTTPS GETs")
    args = parser.parse_args(argv)
    report = compare_m09_with_indodax_candles_readonly(
        capture_file=Path(args.audit_capture), source_db=Path(args.source_db),
        evaluation_epoch=int(time.time()),
        fetch_public_ohlcv=args.fetch_public_ohlcv)
    print(json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    return 0 if report["status"] == "PUBLIC_1M_CANDLE_REFERENCE_RESEARCH_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
