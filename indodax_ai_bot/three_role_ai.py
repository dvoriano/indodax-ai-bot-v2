"""M04-A: one source-bound MARKET→RISK→STRATEGY contract, SHADOW ONLY.

This module does not make provider/network requests and cannot authorize an
Indodax order. The role caller may be injected only by later M04 provider work.
Public M03-I evidence remains untrusted: an injected caller MUST NOT run.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from time import time
from typing import Any

from .contracts import FinalDecision
from .g1_receipt import G1ReceiptVerifier, validate_g1_coverage_manifest

ROLES = ("MARKET", "RISK", "STRATEGY")


def _market_fact_catalog(source: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Build a source-only MARKET schema from one signed candidate snapshot.

    The refs and values are derived from the frozen source, never from model
    prose, other REST requests or estimated complete trade-tape coverage.
    """
    pair = source["pair"]
    market = source.get("market_evidence")
    market = market if isinstance(market, dict) else {}
    timing = source.get("public_timeframe_evidence")
    pair_timing = timing.get(pair) if isinstance(timing, dict) else None
    diag = (pair_timing.get("timeframe_diagnostic")
            if isinstance(pair_timing, dict) else None)
    diag = (diag if isinstance(diag, dict)
            and diag.get("status") == "ALIGNED_PUBLIC_TIMESTAMPS_UNATTESTED"
            else {})

    def row(value: Any, ref: str, *, partial: bool = False) -> dict[str, Any]:
        if value is None or isinstance(value, bool):
            return {"status": "UNAVAILABLE", "source_ref": None, "value": None}
        if not isinstance(value, (str, int, list)):
            return {"status": "UNAVAILABLE", "source_ref": None, "value": None}
        if isinstance(value, str) and (not value or len(value) > 160):
            return {"status": "UNAVAILABLE", "source_ref": None, "value": None}
        if isinstance(value, list) and (
                len(value) > 20 or any(
                    not isinstance(item, str) or len(item) > 160
                    for item in value
                )):
            return {"status": "UNAVAILABLE", "source_ref": None, "value": None}
        return {
            "status": "PARTIAL_PUBLIC_SAMPLE" if partial else "PUBLIC_DIAGNOSTIC",
            "source_ref": ref,
            "value": deepcopy(value),
        }

    def numeric_row(value: Any, ref: str, *, partial: bool = False) -> dict[str, Any]:
        try:
            if isinstance(value, bool) or value is None:
                raise ValueError("NOT_A_FACT")
            num = Decimal(str(value))
            if not num.is_finite():
                raise ValueError("NONFINITE_FACT")
        except (InvalidOperation, TypeError, ValueError):
            return row(None, ref)
        return row(value, ref, partial=partial)

    prefix = "source.public_timeframe_evidence." + pair + ".timeframe_diagnostic."
    return {
        "btc_regime": row(source.get("btc_regime"), "source.btc_regime"),
        "pair_ema8": numeric_row(market.get("ema8"), "source.market_evidence.ema8"),
        "pair_ema21": numeric_row(market.get("ema21"), "source.market_evidence.ema21"),
        "closed_1m": row(diag.get("closed_1m_end_epoch"),
                         prefix + "closed_1m_end_epoch"),
        "closed_5m": row(diag.get("closed_5m_end_epoch"),
                         prefix + "closed_5m_end_epoch"),
        "closed_15m": row(diag.get("closed_15m_end_epoch"),
                          prefix + "closed_15m_end_epoch"),
        "volume_ratio": numeric_row(market.get("volume_ratio"),
                            "source.market_evidence.volume_ratio"),
        "spread_bps": numeric_row(market.get("spread_bps"),
                          "source.market_evidence.spread_bps"),
        "book_imbalance": numeric_row(market.get("book_imbalance"),
                              "source.market_evidence.book_imbalance"),
        "buy_notional_sample_idr": numeric_row(
            market.get("visible_buy_idr"),
            "source.market_evidence.visible_buy_idr", partial=True),
        "sell_notional_sample_idr": numeric_row(
            market.get("visible_sell_idr"),
            "source.market_evidence.visible_sell_idr", partial=True),
        "setup_stage": row(
            (market.get("scalp_setup_stage") or {}).get("stage")
            if isinstance(market.get("scalp_setup_stage"), dict) else None,
            "source.market_evidence.scalp_setup_stage.stage"),
        "anomalies": row(market.get("anomaly_flags"),
                         "source.market_evidence.anomaly_flags"),
        # PUBLIC ticker/depth observations do not establish listing status.
        "listing_status": row(None, "source.market_evidence.listing_status"),
    }


# RISK evidence below is source-bound shadow input, NEVER proof of
# exchange listing, private balances, fees, or an executable trade.
_RISK_CLAIM_FIELDS = ("listing", "spendable_balance", "fee_slippage",
                      "owned_position", "market_liquidity")


def _risk_source_report(source: dict[str, Any]) -> dict[str, Any]:
    """Deterministic RISK veto reasons from original frozen source bytes.

    Claimed private/account readiness alone is not independently verified.
    Production has no trusted G1 issuer or M06/M07 account receipts and
    therefore cannot claim a real RISK clearance from this report.
    """
    market = source.get("market_evidence")
    market = market if isinstance(market, dict) else {}
    issues: list[str] = []
    observed = source.get("screening_observed_epoch")
    end = source.get("closed_1m_epoch")
    if (type(observed) is not int or type(end) is not int
            or not 0 <= observed - end <= 120):
        issues.append("SOURCE_STALE_OR_MISALIGNED")
    spread = market.get("spread_bps")
    try:
        if isinstance(spread, bool) or spread is None:
            raise ValueError("SPREAD_UNKNOWN")
        value = Decimal(str(spread))
        if not value.is_finite() or not 0 <= value <= Decimal("80"):
            raise ValueError("SPREAD_NOT_SUITABLE")
    except (ValueError, TypeError, InvalidOperation):
        issues.append("SPREAD_MISSING_OR_TOO_WIDE")
    imbalance = market.get("book_imbalance")
    try:
        if isinstance(imbalance, bool) or imbalance is None:
            raise ValueError("BOOK_UNKNOWN")
        book = Decimal(str(imbalance))
        if not book.is_finite() or not -1 <= book <= 1:
            raise ValueError("BOOK_INVALID")
    except (ValueError, TypeError, InvalidOperation):
        issues.append("BOOK_LIQUIDITY_NOT_QUALIFIED")
    flags = market.get("anomaly_flags")
    if not isinstance(flags, list) or flags or market.get("public_maintenance_flag") is not False:
        issues.append("ANOMALY_OR_MAINTENANCE_REVIEW")
    # This namespace does NOT exist in the real public scanner output.
    # Only an authenticated independent downstream source can later populate
    # it; current tests use signed synthetic claims to exercise RISK->STRATEGY.
    claims = source.get("risk_source_claims")
    for field in _RISK_CLAIM_FIELDS:
        x = claims.get(field) if isinstance(claims, dict) else None
        if (not isinstance(x, dict)
                or x.get("pair") != source.get("pair")
                or type(x.get("closed_1m_epoch")) is not int
                or x.get("closed_1m_epoch") != end
                or x.get("status") != "SOURCE_BOUND_CLAIM_ONLY"):
            issues.append("MISSING_" + field.upper() + "_EVIDENCE")
    return {
        "stage": "M04_RISK_SOURCE_BOUND_SHADOW_VETO",
        "pair": source.get("pair"),
        "closed_1m_epoch": end,
        "blockers": issues,
        "allow_shadow_analysis_only": not issues,
        "independent_exchange_account_proof_verified": False,
        "execution_allowed": False,
    }


def _strategy_source_plan(source: dict[str, Any]) -> dict[str, Any]:
    """Source-bound diagnostic math, NEVER an exchange quote or executable plan.

    Current M03 public snapshots contain no independently authenticated
    entry price, free balance, fee/precision, or SELL ownership. Therefore no
    real candidate may acquire a numerical price/sizing from this helper.
    """
    pair, epoch = source["pair"], source["closed_1m_epoch"]
    baseline = {
        "stage": "M04_STRATEGY_SOURCE_BOUND_DIAGNOSTIC",
        "status": "UNAVAILABLE_NO_VERIFIED_ENTRY_AND_COSTS",
        "pair": pair, "closed_1m_epoch": epoch,
        "quote_origin": "UNAVAILABLE",
        "entry_price_idr": None, "quantity": None,
        "allocated_cost_idr": None, "estimated_net_exit_idr": None,
        "estimated_net_profit_idr": None, "estimated_net_roi_percent": None,
        "stop_loss_price_idr": None, "max_hold_minutes": None,
        "cancel_if": None,
        "net_exit_targets": [
            {"net_roi_percent": percent, "required_exit_price_idr": None}
            for percent in ("2", "8", "11")
        ],
        "fee_basis": "INDODAX_ALL_IN_EXECUTION_FEES_ONLY",
        "authenticated_balance_verified": False,
        "authenticated_quote_and_fees_verified": False,
        "bot_owned_position_verified": False,
        "execution_allowed": False,
    }
    claim = source.get("strategy_quote_claim")
    if (not isinstance(claim, dict)
            or claim.get("status") != "SYNTHETIC_SOURCE_BOUND_SCENARIO_ONLY"
            or claim.get("pair") != pair
            or type(claim.get("closed_1m_epoch")) is not int
            or claim["closed_1m_epoch"] != epoch):
        return baseline

    def numeric(key: str, *, allow_zero: bool = False) -> Decimal:
        value = claim.get(key)
        if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
            raise ValueError("INVALID_STRATEGY_NUMBER")
        try:
            number = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError("INVALID_STRATEGY_NUMBER") from exc
        if (not number.is_finite()
                or (number < 0 if allow_zero else number <= 0)
                or (number != 0 and
                    (number.adjusted() > 15 or number.adjusted() < -18))):
            raise ValueError("INVALID_STRATEGY_NUMBER")
        return number

    try:
        entry = numeric("entry_price_idr")
        qty = numeric("quantity")
        sell = numeric("scenario_sell_price_idr")
        stop = numeric("stop_loss_price_idr")
        buy_fee = numeric("buy_all_in_fee_rate", allow_zero=True)
        sell_fee = numeric("sell_all_in_fee_rate", allow_zero=True)
        buy_slip = numeric("buy_slippage_bps", allow_zero=True)
        sell_slip = numeric("sell_slippage_bps", allow_zero=True)
        hold = claim.get("max_hold_minutes")
        cancel = claim.get("cancel_if")
        if (not 0 < stop < entry
                or any(rate > Decimal("0.05")
                       for rate in (buy_fee, sell_fee))
                or any(bps > Decimal("500") for bps in (buy_slip, sell_slip))
                or type(hold) is not int or not 1 <= hold <= 90
                or not isinstance(cancel, str) or not 1 <= len(cancel) <= 120
                or entry * qty > Decimal("100000000000000")):
            return baseline
        gross_buy = entry * qty
        allocated = gross_buy * (
            Decimal("1") + buy_fee + buy_slip / Decimal("10000"))
        gross_sell = sell * qty
        factor = Decimal("1") - sell_fee - sell_slip / Decimal("10000")
        if factor <= 0:
            return baseline
        net_exit = gross_sell * factor
        net_profit = net_exit - allocated
        roi = Decimal("100") * net_profit / allocated
        targets = [
            {
                "net_roi_percent": level,
                "required_exit_price_idr": str(
                    allocated * (Decimal("1") + Decimal(level) / Decimal("100"))
                    / (qty * factor)
                ),
            }
            for level in ("2", "8", "11")
        ]
    except (TypeError, ValueError, InvalidOperation, OverflowError,
            ZeroDivisionError, ArithmeticError):
        return baseline
    return {
        **baseline, "status": "SYNTHETIC_SCENARIO_CALCULATED_UNATTESTED",
        "quote_origin": "SYNTHETIC_SOURCE_BOUND_SCENARIO_ONLY",
        "entry_price_idr": str(entry), "quantity": str(qty),
        "allocated_cost_idr": str(allocated),
        "estimated_net_exit_idr": str(net_exit),
        "estimated_net_profit_idr": str(net_profit),
        "estimated_net_roi_percent": str(roi),
        "stop_loss_price_idr": str(stop), "max_hold_minutes": hold,
        "cancel_if": cancel, "net_exit_targets": targets,
    }


@dataclass(frozen=True, slots=True)
class _FrozenCandidateSnapshot:
    """An immutable canonical byte commitment, NOT exchange attestation."""

    source_bytes: bytes
    digest: str
    pair: str
    closed_1m_epoch: int

    @classmethod
    def capture(cls, source: dict[str, Any]) -> "_FrozenCandidateSnapshot":
        # Canonical bytes are captured ONCE. No later mutable bulk/role input
        # can modify the signed source digest for an in-flight candidate.
        raw = json.dumps(
            source, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False,
        ).encode("utf-8")
        return cls(
            raw, sha256(raw).hexdigest(),
            source["pair"], source["closed_1m_epoch"],
        )

    def new_role_input(self, prior_role_results: list[dict[str, Any]]) -> dict[str, Any]:
        """Rehydrate an isolated copy for each role from the SAME frozen bytes."""
        source = json.loads(self.source_bytes)
        if (source.get("pair") != self.pair
                or source.get("closed_1m_epoch") != self.closed_1m_epoch
                or sha256(self.source_bytes).hexdigest() != self.digest):
            raise ValueError("SOURCE_SNAPSHOT_INTEGRITY_FAILED")
        return {
            "source": source,
            "market_evidence": deepcopy(source["market_evidence"]),
            "market_fact_catalog": _market_fact_catalog(source),
            "risk_source_report": _risk_source_report(source),
            "strategy_source_plan": _strategy_source_plan(source),
            "evidence_digest_sha256": self.digest,
            "snapshot_contract": {
                "schema_version": 1,
                "pair": self.pair,
                "closed_1m_epoch": self.closed_1m_epoch,
                "evidence_digest_sha256": self.digest,
            },
            "role_order": list(ROLES),
            "live_execution_enabled": False,
            "prior_role_results": deepcopy(prior_role_results),
        }


def _reject(reason: str, *, digest: str | None = None, roles: list[str] | None = None,
            candidate_pair: str | None = None) -> dict[str, Any]:
    return {
        "stage": "M04A_THREE_ROLE_SHADOW",
        "status": "REJECT",
        "reason": reason,
        "source_digest_sha256": digest,
        "candidate_pair": candidate_pair,
        "completed_roles": roles if roles is not None else [],
        "expected_roles": list(ROLES),
        "role_results": [],
        "g1_verified": False,
        "provider_connected": False,
        "risk_veto_bypass_allowed": False,
        # G1/M04 can report unusable source or missing AI, but only M05
        # owner Final Decision can issue PASS/REJECT for a trade proposal.
        "final_decision": None,
        "stage_disposition": "ADVISORY_UNAVAILABLE",
        "g2_candidate_authorized": False,
        "execution_allowed": False,
        "live_trading_authorized": False,
    }


def _finite_decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("INVALID_" + name)
    try:
        d = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("INVALID_" + name) from exc
    if not d.is_finite() or d < 0:
        raise ValueError("INVALID_" + name)
    return d



def _candidate_decision_loop(*, reports: list[dict], source_epoch: int,
                             now_epoch: int) -> dict:
    """Compare validated SHADOW reports only; never issue Final PASS or BUY.

    Only source-bound synthetic plans can be compared at present. A model
    BUY with unknown account/quote/costs is not a qualified scenario.
    Comparisons use a conservative dominance rule, not a forecast.
    """
    out = {
        "stage": "M04_CANDIDATE_DECISION_LOOP_SHADOW",
        "status": "RESCAN_FRESH_EVIDENCE",
        "selected_pair": None,
        "selected_evidence_digest_sha256": None,
        "qualified_scenario_count": 0,
        "candidates": [],
        "time_to_profit": "UNKNOWN",
        "final_decision": None,
        "execution_allowed": False,
        "paper_fill_authorized": False,
    }
    if (type(source_epoch) is not int or type(now_epoch) is not int
            or source_epoch <= 0
            or not 0 <= now_epoch - source_epoch <= 60):
        return dict(out, status="RESCAN_STALE_EVIDENCE")
    if (not isinstance(reports, list) or len(reports) > 2
            or any(not isinstance(r, dict) or not isinstance(r.get("pair"), str)
                   for r in reports)
            or len({r["pair"] for r in reports}) != len(reports)):
        return dict(out, status="RESCAN_INVALID_COHORT")
    qualified = []
    for r in reports:
        pair, digest = r["pair"], r.get("evidence_digest_sha256")
        reason = r.get("reason")
        item = {"pair": pair, "evidence_digest_sha256": digest,
                "reason": reason, "disposition": "ADVISORY_UNAVAILABLE"}
        out["candidates"].append(item)
        roles = r.get("role_results")
        if isinstance(reason, str) and reason.startswith("RISK_VETO_"):
            item["disposition"] = "RISK_REJECT"
            continue
        if reason == "STRATEGY_BUY_NET_TARGET_UNMET":
            item["disposition"] = "NET_REJECT"
            continue
        if (r.get("completed_roles") != list(ROLES)
                or not isinstance(roles, list) or len(roles) != 3
                or any(not isinstance(x, dict) for x in roles)
                or [x.get("role") for x in roles] != list(ROLES)):
            continue
        market, risk, strategy = roles
        if risk.get("decision") != "ALLOW":
            item["disposition"] = "RISK_REJECT"
            continue
        if strategy.get("action") != "BUY":
            item["disposition"] = "STRATEGY_WAIT"
            continue
        if market.get("bias") == "BEARISH":
            item["disposition"] = "MARKET_CONFLICT_WAIT"
            continue
        plan = strategy.get("strategy_source_plan")
        if (reason != "STRATEGY_BUY_PREFLIGHT_UNVERIFIED"
                or not isinstance(plan, dict)
                or plan.get("status") != "SYNTHETIC_SCENARIO_CALCULATED_UNATTESTED"
                or plan.get("pair") != pair
                or not isinstance(digest, str) or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)
                or not isinstance(risk.get("risk_source_report"), dict)
                or risk["risk_source_report"].get("blockers") != []):
            item["disposition"] = "WAIT_UNVERIFIED_QUOTE_OR_RISK"
            continue
        try:
            net = _finite_decimal(strategy.get("projected_net_profit_idr"), "NET")
            roi = _finite_decimal(strategy.get("projected_net_roi_percent"), "ROI")
            cost = _finite_decimal(plan.get("allocated_cost_idr"), "COST")
            entry = _finite_decimal(plan.get("entry_price_idr"), "ENTRY")
            stop = _finite_decimal(plan.get("stop_loss_price_idr"), "STOP")
            spread_row = market["evidence_status"]["spread_bps"]
            if spread_row["status"] != "PUBLIC_DIAGNOSTIC":
                raise ValueError("SPREAD_UNKNOWN")
            spread = _finite_decimal(spread_row["value"], "SPREAD")
            if (net < 10000 or roi < 2 or cost <= 0 or not 0 < stop < entry
                    or spread > 80
                    or net > _finite_decimal(plan.get("estimated_net_profit_idr"), "PLAN_NET")
                    or roi > _finite_decimal(plan.get("estimated_net_roi_percent"), "PLAN_ROI")):
                raise ValueError("SCENARIO_NOT_COMPARABLE")
            stop_distance = (entry - stop) / entry * 100
        except (KeyError, TypeError, ValueError, ArithmeticError):
            item["disposition"] = "WAIT_INCOMPLETE_SCENARIO"
            continue
        item["disposition"] = "QUALIFIED_SYNTHETIC_SCENARIO"
        item["scenario_metrics"] = {
            "projected_net_profit_idr": str(net),
            "projected_net_roi_percent": str(roi),
            "allocated_cost_idr": str(cost),
            "spread_bps": str(spread),
            "stop_distance_percent_not_max_loss": str(stop_distance),
        }
        qualified.append((item, cost, (net, roi, -spread, -stop_distance)))
    out["qualified_scenario_count"] = len(qualified)
    if not qualified:
        return out
    winner = qualified[0]
    if len(qualified) == 2:
        a, b = qualified
        # Same capital basis and strict dominance across all four dimensions.
        # If tradeoffs conflict or tie, do not invent weights or confidence.
        def dominates(x, y):
            return (x[1] == y[1]
                    and all(u >= v for u, v in zip(x[2], y[2]))
                    and any(u > v for u, v in zip(x[2], y[2])))
        if dominates(a, b):
            winner = a
        elif dominates(b, a):
            winner = b
        else:
            return dict(out, status="RESCAN_INCOMPARABLE_SCENARIOS")
    return dict(out, status="SELECTED_SYNTHETIC_REVIEW_ONLY",
                selected_pair=winner[0]["pair"],
                selected_evidence_digest_sha256=winner[0]["evidence_digest_sha256"])


class ThreeRoleShadow:
    """One non-live AI boundary connected to the single bulk orchestrator.

    Caller is intentionally absent in production. An explicit caller is only
    used in regression tests to validate three-role output contracts.
    """

    def __init__(self, *, caller: Callable[[str, dict[str, Any]], Mapping[str, Any]] | None = None,
                 clock: Callable[[], float] = time,
                 source_verifier: G1ReceiptVerifier | None = None) -> None:
        self._caller = caller
        self._clock = clock
        # No verifier is wired from production public market or env flags.
        self._source_verifier = source_verifier

    def _run_two_candidates(self, trace_id: str, bulk: dict[str, Any],
                            preview: list[str]) -> dict[str, Any]:
        """Preflight both candidates before any caller; isolate valid receipts.

        Shared cohort facts remain in BOTH signed digests. A shared fact
        mutation can invalidate both; one bad individual receipt alone
        cannot invalidate the other pair's independently verified digest.
        No source/coverage/paid-provider check is bypassed.
        """
        if (len(preview) != 2 or any(not isinstance(p, str) for p in preview)
                or len(set(preview)) != 2):
            return _reject("INVALID_SOURCE_COHORT_SHAPE")
        if any(bulk.get(k) is not True for k in (
                "g1_verified", "source_trust_verified", "trade_history_complete")):
            return _reject("G1_PUBLIC_EVIDENCE_UNTRUSTED")
        receipts = bulk.get("g1_attestations")
        if not isinstance(receipts, dict) or not set(receipts).issubset(preview):
            return _reject("G1_ISSUER_RECEIPT_UNVERIFIED")
        now = int(self._clock())
        # Fail closed on malformed shared cohort; never rebuild signed facts.
        isolated = []
        for pair in preview:
            item = deepcopy(bulk)
            item["eligible_cohort"]["provisional_watchlist_preview"] = [pair]
            item["g1_attestation"] = receipts.get(pair)
            item.pop("g1_attestations", None)
            isolated.append(item)
        preflight = ThreeRoleShadow(
            caller=None, clock=lambda: now, source_verifier=self._source_verifier,
        )
        checked = [preflight.run(trace_id, item) for item in isolated]
        verified = {
            pair: result["source_digest_sha256"]
            for pair, result in zip(preview, checked)
            if result["reason"] == "M04_PROVIDER_NOT_CONFIGURED"
        }
        if not verified:
            failure = checked[0]
            return _reject(failure["reason"],
                           digest=failure.get("source_digest_sha256"),
                           candidate_pair=preview[0])
        if self._caller is None:
            return _reject("M04_PROVIDER_NOT_CONFIGURED")
        reports = []
        for pair, item, admitted in zip(preview, isolated, checked):
            result = admitted
            if pair in verified:
                # Recheck freshness and receipt at each actual candidate call,
                # especially after a preceding slow provider response.
                result = ThreeRoleShadow(
                    caller=self._caller, clock=self._clock,
                    source_verifier=self._source_verifier,
                ).run(trace_id, item)
            reports.append({
                "pair": pair,
                "evidence_digest_sha256": result.get("source_digest_sha256"),
                "reason": result["reason"],
                "completed_roles": result["completed_roles"],
                "role_results": result["role_results"],
            })
        result = _reject("M04_TWO_CANDIDATE_SHADOW_ONLY")
        result["candidate_reports"] = reports
        result["candidate_count"] = 2
        result["source_digests_by_pair"] = verified
        result["completed_role_calls"] = sum(
            len(x["completed_roles"]) for x in reports
        )
        result["candidate_decision_loop"] = _candidate_decision_loop(
            reports=reports,
            source_epoch=bulk.get("screening_observed_epoch"),
            now_epoch=int(self._clock()),
        )
        # Even synthetic signed receipts and role responses authorize no order.
        return result

    def run(self, trace_id: str, bulk: dict[str, Any] | None) -> dict[str, Any]:
        result = self._run(trace_id, bulk)
        # Attach the same decision diagnostics to a single validated review.
        # Early admission failures keep their original reason and cannot
        # obtain a selection or cause a model call through this wrapper.
        if ("candidate_decision_loop" not in result
                and result.get("completed_roles") == list(ROLES)
                and isinstance(bulk, dict)):
            result["candidate_decision_loop"] = _candidate_decision_loop(
                reports=[{
                    "pair": result["candidate_pair"],
                    "evidence_digest_sha256": result["source_digest_sha256"],
                    "reason": result["reason"],
                    "completed_roles": result["completed_roles"],
                    "role_results": result["role_results"],
                }],
                source_epoch=bulk.get("screening_observed_epoch"),
                now_epoch=int(self._clock()),
            )
        return result

    def _run(self, trace_id: str, bulk: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(trace_id, str) or not trace_id.strip():
            return _reject("INVALID_TRACE_ID")
        if (isinstance(bulk, dict)
                and isinstance(bulk.get("eligible_cohort"), dict)
                and isinstance(bulk["eligible_cohort"].get("provisional_watchlist_preview"), list)
                and len(bulk["eligible_cohort"]["provisional_watchlist_preview"]) == 2):
            return self._run_two_candidates(
                trace_id, bulk,
                bulk["eligible_cohort"]["provisional_watchlist_preview"],
            )
        if not isinstance(bulk, dict) or not isinstance(bulk.get("eligible_cohort"), dict):
            return _reject("MISSING_SOURCE_COHORT")
        cohort = bulk["eligible_cohort"]
        if cohort.get("status") != "ALIGNED_PUBLIC_UNATTESTED":
            return _reject("UNALIGNED_OR_INCOMPLETE_COHORT")
        pairs = cohort.get("ranked_same_window_diagnostics")
        preview = cohort.get("provisional_watchlist_preview")
        epoch = cohort.get("closed_1m_epoch")
        observed = bulk.get("screening_observed_epoch")
        if (not isinstance(pairs, list) or not isinstance(preview, list)
                or len(preview) > 2 or len(set(map(str, preview))) != len(preview)
                or isinstance(epoch, bool) or not isinstance(epoch, int) or epoch <= 0
                or isinstance(observed, bool) or not isinstance(observed, int) or observed < epoch
                or observed - epoch > 120):
            return _reject("INVALID_SOURCE_COHORT_SHAPE")
        indexed: dict[str, int] = {}
        for item in pairs:
            if not isinstance(item, dict):
                return _reject("INVALID_SOURCE_CANDIDATE")
            p, score = item.get("pair"), item.get("score")
            if (not isinstance(p, str) or not p.endswith("_idr")
                    or isinstance(score, bool) or not isinstance(score, int)
                    or not 0 <= score <= 100 or p in indexed):
                return _reject("INVALID_SOURCE_CANDIDATE")
            indexed[p] = score
        if not all(isinstance(p, str) and p in indexed for p in preview):
            return _reject("INVALID_PREVIEW_PAIR")
        reviews = cohort.get("provisional_quality_reviews")
        if not isinstance(reviews, list):
            return _reject("MISSING_PUBLIC_QUALITY_REVIEWS")
        quality: dict[str, bool] = {}
        for review in reviews:
            if (not isinstance(review, dict)
                    or not isinstance(review.get("pair"), str)
                    or review["pair"] in quality
                    or not isinstance(review.get("review_flags"), list)
                    or not isinstance(review.get("clear_of_provisional_cautions"), bool)):
                return _reject("INVALID_QUALITY_REVIEW")
            quality[review["pair"]] = (
                review["clear_of_provisional_cautions"]
                and review["review_flags"] == []
            )
        if any(not quality.get(p, False) for p in preview):
            return _reject("PREVIEW_HAS_UNRESOLVED_QUALITY_CAUTION")
        if not preview:
            return _reject("NO_PROVISIONAL_CANDIDATE")
        now = int(self._clock())
        if now < observed or now - observed > 60:
            return _reject("EXPIRED_G1_SOURCE")
        source = {
            "trace_id": trace_id,
            "pair": preview[0],
            "closed_1m_epoch": epoch,
            "screening_observed_epoch": observed,
            "btc_regime": cohort.get("btc_regime"),
            "pair_score": indexed[preview[0]],
            "provisional_quality_reviews": cohort.get("provisional_quality_reviews", []),
            "source_trust_verified": cohort.get("source_trust_verified") is True,
            "g1_verified": cohort.get("g1_verified") is True,
            "trade_history_complete": cohort.get("trade_history_complete") is True,
            "root_g1_verified": bulk.get("g1_verified") is True,
            "root_trade_history_complete": bulk.get("trade_history_complete") is True,
            "root_source_trust_verified": bulk.get("source_trust_verified") is True,
            # Claims are digest-bound so tampering after issuer signing
            # invalidates the receipt, but they are NOT M06/M07 proof.
            "risk_source_claims": deepcopy(
                (bulk.get("risk_source_claims_by_pair") or {}).get(preview[0])
                if isinstance(bulk.get("risk_source_claims_by_pair"), dict)
                else None
            ),
            "strategy_quote_claim": deepcopy(
                (bulk.get("strategy_quote_claims_by_pair") or {}).get(preview[0])
                if isinstance(bulk.get("strategy_quote_claims_by_pair"), dict)
                else None
            ),
            # A signed digest must include the independent issuer's actual
            # upstream coverage manifest, not just self-asserted trust flags.
            # Public M03 REST/WS intentionally never creates this manifest.
            "g1_coverage_manifest": deepcopy(
                (bulk.get("g1_coverage_manifests_by_pair") or {}).get(preview[0])
                if isinstance(bulk.get("g1_coverage_manifests_by_pair"), dict)
                else None
            ),
        }
        # M03 final max-2 preview is quality/stage ranked, so its candidate
        # may sit below scalar-score top10. Prefer the SAME-cycle selected
        # evidence, not a historical or partial unrelated top10 row.
        selected_evidence = bulk.get("provisional_market_evidence")
        selected_matches = (
            [item for item in selected_evidence if isinstance(item, dict)
             and item.get("pair") == source["pair"]]
            if isinstance(selected_evidence, list) else []
        )
        if len(selected_matches) > 1:
            return _reject("G1_DUPLICATE_BOUND_MARKET_EVIDENCE",
                           candidate_pair=source["pair"])
        if len(selected_matches) == 1:
            pair_evidence = selected_matches[0]
        else:
            # Legacy M04 signed fixtures have an owner-injected preview and
            # only a scalar top10; production candidate exists in the
            # selected evidence already, even if ranked below scalar top10.
            raw_market_evidence = bulk.get("ranked_diagnostics_top10")
            pair_evidence = next(
                (item for item in raw_market_evidence
                 if isinstance(item, dict) and item.get("pair") == source["pair"]),
                None,
            ) if isinstance(raw_market_evidence, list) else None
        if (not isinstance(pair_evidence, dict)
                or pair_evidence.get("closed_1m_epoch") != epoch
                or pair_evidence.get("trade_authorized") is not False):
            return _reject("G1_MISSING_BOUND_MARKET_EVIDENCE",
                           candidate_pair=source["pair"])
        # Carry only factual PUBLIC timing evidence for THIS pair plus BTC;
        # unknown/incomplete source fields remain explicitly absent. This is
        # descriptive metadata, not a signed or complete exchange feed.
        coverage = bulk.get("g1_source_admission")
        if isinstance(coverage, dict) and isinstance(coverage.get("pair_coverage"), list):
            coverage_rows = coverage["pair_coverage"]
            selected_timing = {}
            for bound_pair in dict.fromkeys((source["pair"], "btc_idr")):
                rows = [row for row in coverage_rows
                        if isinstance(row, dict) and row.get("pair") == bound_pair]
                if len(rows) > 1:
                    return _reject("G1_DUPLICATE_PUBLIC_SOURCE_TIMING",
                                   candidate_pair=source["pair"])
                if rows:
                    row = rows[0]
                    if (bound_pair == source["pair"] and
                            row.get("closed_1m_epoch") != epoch):
                        return _reject("G1_SOURCE_TIMING_EPOCH_MISMATCH",
                                       candidate_pair=source["pair"])
                    selected_timing[bound_pair] = {
                        "closed_1m_epoch": row.get("closed_1m_epoch"),
                        "timeframe_diagnostic": deepcopy(row.get("timeframe_diagnostic")),
                        "can_prove_complete_trade_tape":
                            row.get("can_prove_complete_trade_tape") is True,
                    }
            source["public_timeframe_evidence"] = selected_timing

        # Capture the existing candidate candle/microstructure evidence and
        # PUBLIC 1m/5m/15m timing in ONE canonical byte snapshot before issuer
        # verification and before any MARKET/RISK/STRATEGY caller.
        try:
            source["market_evidence"] = json.loads(
                json.dumps(pair_evidence, allow_nan=False)
            )
            frozen = _FrozenCandidateSnapshot.capture(source)
        except (TypeError, ValueError, KeyError, OverflowError):
            return _reject("G1_INVALID_SOURCE_SNAPSHOT",
                           candidate_pair=source["pair"])
        digest = frozen.digest
        # A local SHA digest never authenticates Indodax or a trusted issuer.
        if not all(source[k] for k in (
            "source_trust_verified", "g1_verified", "trade_history_complete",
            "root_g1_verified", "root_trade_history_complete", "root_source_trust_verified",
        )):
            return _reject("G1_PUBLIC_EVIDENCE_UNTRUSTED", digest=digest,
                           candidate_pair=source["pair"])
        # These boolean labels arrive in mutable JSON, so they are NOT
        # sufficient to authorize a role call. Require an independently
        # authenticated INTERNAL issuer receipt covering the evidence hash.
        if (self._source_verifier is None
                or not self._source_verifier.verify(
                    bulk.get("g1_attestation"),
                    trace_id=trace_id, pair=source["pair"],
                    closed_epoch=epoch, source_digest=digest, now=now,
                )):
            return _reject("G1_ISSUER_RECEIPT_UNVERIFIED", digest=digest,
                           candidate_pair=source["pair"])
        # A valid MAC authenticates a configured INTERNAL issuer, but does
        # not by itself demonstrate upstream trade-window completeness.
        # Require exactly closed candles and independently claimed 15m/1h
        # replay coverage sealed into the SAME frozen digest. The provenance
        # issuer still needs real, separate source acceptance in production.
        manifest = source["g1_coverage_manifest"]
        if manifest is None:
            return _reject("G1_WINDOW_COVERAGE_UNKNOWN", digest=digest,
                           candidate_pair=source["pair"])
        claims = bulk["g1_attestation"]["claims"]
        if not validate_g1_coverage_manifest(
            manifest, issuer_id=claims["issuer_id"],
            pair=source["pair"], closed_epoch=epoch,
        ):
            return _reject("G1_WINDOW_COVERAGE_INVALID", digest=digest,
                           candidate_pair=source["pair"])
        if self._caller is None:
            return _reject("M04_PROVIDER_NOT_CONFIGURED", digest=digest,
                           candidate_pair=source["pair"])

        roles: list[str] = []
        validated: list[dict[str, Any]] = []
        for role in ROLES:
            # Owner policy: RISK is an advisory analysis, not the owner Final.
            # An effective NO_TRADE/EXIT_REVIEW must reach STRATEGY so owner
            # can inspect a complete three-role explanation. It remains a
            # binding mechanical no-order condition, never an executor pass.
            try:
                # Each model receives an isolated view reconstructed from
                # the SAME once-captured candidate+BTC public source bytes.
                # A caller cannot mutate the later RISK/STRATEGY snapshot.
                role_input = frozen.new_role_input(validated)
                raw = self._caller(role, role_input)
                if not isinstance(raw, Mapping):
                    raise ValueError("MALFORMED_RESPONSE")
                if raw.get("role") != role or raw.get("pair") != source["pair"] or raw.get("evidence_digest_sha256") != digest:
                    raise ValueError("ROLE_PAIR_OR_EVIDENCE_MISMATCH")
                reason = raw.get("reason")
                if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
                    raise ValueError("INVALID_ROLE_REASON")
                if role == "MARKET":
                    bias = raw.get("bias")
                    if bias not in ("BULLISH", "BEARISH", "MIXED"):
                        raise ValueError("INVALID_MARKET_BIAS")
                    if set(raw) != {
                            "role", "pair", "evidence_digest_sha256",
                            "reason", "bias", "evidence_status"}:
                        raise ValueError("INVALID_MARKET_OUTPUT_FIELDS")
                    # An LLM cannot invent prices, candle windows, buyer
                    # pressure or listing status by merely naming them.
                    # Every output field must exactly mirror the signed
                    # snapshot's public/partial/unavailable fact catalog.
                    claimed = raw.get("evidence_status")
                    # Never compare against the caller's mutable input: an
                    # injected model may change both its input catalog and
                    # answer before returning. Reconstruct the authority
                    # from the immutable bytes captured before any role call.
                    catalog = _market_fact_catalog(
                        json.loads(frozen.source_bytes)
                    )
                    # Canonical JSON comparison also rejects booleans
                    # posing as integers and non-finite numeric claims.
                    try:
                        exact = (
                            json.dumps(claimed, sort_keys=True,
                                       separators=(",", ":"), allow_nan=False)
                            == json.dumps(catalog, sort_keys=True,
                                          separators=(",", ":"), allow_nan=False)
                        )
                    except (TypeError, ValueError):
                        exact = False
                    if (not isinstance(claimed, dict)
                            or set(claimed) != set(catalog)
                            or not exact):
                        raise ValueError("MARKET_EVIDENCE_NOT_SOURCE_BOUND")
                    parsed = {
                        "role": role, "bias": bias, "reason": reason,
                        "evidence_status": deepcopy(catalog),
                    }
                elif role == "RISK":
                    decision = raw.get("decision")
                    if decision not in ("ALLOW", "NO_TRADE", "EXIT_REVIEW"):
                        raise ValueError("INVALID_RISK_VETO")
                    # Never trust mutable caller inputs or its own claimed
                    # checks. Recompute from the same frozen source as MARKET.
                    verified_report = _risk_source_report(
                        json.loads(frozen.source_bytes)
                    )
                    claimed = raw.get("risk_source_report")
                    try:
                        exact = (
                            json.dumps(claimed, sort_keys=True, allow_nan=False)
                            == json.dumps(verified_report, sort_keys=True,
                                          allow_nan=False)
                        )
                    except (TypeError, ValueError):
                        exact = False
                    if not exact:
                        raise ValueError("RISK_SOURCE_REPORT_NOT_BOUND")
                    # A model cannot use ALLOW to override a missing source,
                    # listing, balance, fee, or position-ownership claim.
                    effective = ("NO_TRADE" if decision == "ALLOW"
                                 and verified_report["blockers"] else decision)
                    parsed = {
                        "role": role, "decision": effective, "reason": reason,
                        "risk_source_report": deepcopy(verified_report),
                        "model_decision": decision,
                    }
                else:
                    action = raw.get("action")
                    if action not in ("BUY", "WAIT", "EXIT_REVIEW"):
                        raise ValueError("INVALID_STRATEGY_ACTION")
                    if set(raw) != {
                            "role", "pair", "evidence_digest_sha256",
                            "reason", "action", "projected_net_profit_idr",
                            "projected_net_roi_percent", "strategy_source_plan"}:
                        raise ValueError("INVALID_STRATEGY_OUTPUT_FIELDS")
                    # Rebuild expected plan AFTER model return from frozen
                    # bytes; mutating callback cannot forge quote or targets.
                    expected_plan = _strategy_source_plan(
                        json.loads(frozen.source_bytes)
                    )
                    try:
                        exact = (
                            json.dumps(raw.get("strategy_source_plan"),
                                       sort_keys=True, allow_nan=False)
                            == json.dumps(expected_plan, sort_keys=True,
                                          allow_nan=False)
                        )
                    except (TypeError, ValueError):
                        exact = False
                    if not exact:
                        raise ValueError("STRATEGY_PLAN_NOT_SOURCE_BOUND")
                    model_net = _finite_decimal(
                        raw.get("projected_net_profit_idr"), "NET_PROFIT")
                    model_roi = _finite_decimal(
                        raw.get("projected_net_roi_percent"), "NET_ROI")
                    if (action == "BUY" and expected_plan["status"] ==
                            "SYNTHETIC_SCENARIO_CALCULATED_UNATTESTED"):
                        # Even a synthetic scenario limits optimistic model
                        # projections. Production has no such verified quote.
                        if (model_net > Decimal(
                                expected_plan["estimated_net_profit_idr"])
                                or model_roi > Decimal(
                                    expected_plan["estimated_net_roi_percent"])):
                            raise ValueError("STRATEGY_NET_OVERSTATED")
                    parsed = {
                        "role": role, "action": action,
                        "effective_action": (
                            "WAIT" if action == "BUY" else action
                        ),
                        "reason": reason,
                        "projected_net_profit_idr": str(model_net),
                        "projected_net_roi_percent": str(model_roi),
                        "strategy_source_plan": deepcopy(expected_plan),
                    }
            except Exception:
                return _reject("M04_ROLE_INVALID_OR_UNAVAILABLE", digest=digest,
                               roles=roles, candidate_pair=source["pair"])
            validated.append(parsed)
            roles.append(role)
            if role == "STRATEGY":
                risk = validated[1]
                if risk["decision"] != "ALLOW":
                    # Preserve the effective RISK finding after STRATEGY is
                    # recorded. A proposed BUY is diagnostic only; the
                    # published effective_action is always WAIT.
                    return {
                        **_reject("RISK_VETO_" + risk["decision"], digest=digest,
                                  roles=roles, candidate_pair=source["pair"]),
                        "role_results": validated,
                    }
                if parsed["action"] == "BUY":
                    # M04-U: BOTH net thresholds are mandatory. Synthetic
                    # signed receipts, private account and M07 preflight are
                    # still NOT proof of real order eligibility.
                    if (Decimal(parsed["projected_net_profit_idr"]) < Decimal("10000")
                            or Decimal(parsed["projected_net_roi_percent"]) < Decimal("2")):
                        return {
                            **_reject("STRATEGY_BUY_NET_TARGET_UNMET", digest=digest,
                                      roles=roles, candidate_pair=source["pair"]),
                            "role_results": validated,
                        }
                    return {
                        **_reject("STRATEGY_BUY_PREFLIGHT_UNVERIFIED", digest=digest,
                                  roles=roles, candidate_pair=source["pair"]),
                        "role_results": validated,
                    }
        return {
            **_reject("M04_SHADOW_ONLY_FINAL_AND_PREFLIGHT_ABSENT",
                      digest=digest, roles=roles, candidate_pair=source["pair"]),
            "role_results": validated,
        }
