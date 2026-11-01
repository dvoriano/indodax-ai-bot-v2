"""M03/M04 observed public candidate -> existing M05 owner advisory.

No invented last price, position size, capital, G1 issuer or completed AI.
A candidate may reach the HUMAN Telegram review even when G1 and paid
providers remain unavailable. The decision is consent-only, never an order.
"""
from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any

from .owner_control import OwnerDecisionLedger

_LABEL = re.compile(r"^[A-Za-z0-9_:.-]{1,100}$")
_EXCLUDED = frozenset(("floki_idr", "pepe_idr", "usdt_idr", "usdc_idr"))


def _label(value: Any, fallback: str) -> str:
    if isinstance(value, str) and _LABEL.fullmatch(value):
        return value
    return fallback


def queue_scanner_owner_advisory(
    *, bulk: dict | None, ai_shadow: dict | None,
    owner: OwnerDecisionLedger, now_epoch: int,
) -> dict[str, Any]:
    """Use the SAME immutable public bulk result, no second data fetch.

    Only coherent M03 same-window preview candidates with a real *closed*
    minute and within a 90-second public observation lifetime are queued.
    Results without that evidence remain a diagnostic, not a fake proposal.
    """
    if type(now_epoch) is not int or now_epoch <= 0:
        raise ValueError("M05_ADVISORY_CLOCK_INVALID")
    if not isinstance(owner, OwnerDecisionLedger):
        raise TypeError("M05_ADVISORY_REQUIRES_ORIGINAL_OWNER_LEDGER")
    if not isinstance(bulk, dict):
        return {"status": "NO_PUBLIC_BULK", "queued": 0}
    cohort = bulk.get("eligible_cohort")
    if not isinstance(cohort, dict):
        return {"status": "NO_ALIGNED_COHORT", "queued": 0}
    if cohort.get("status") != "ALIGNED_PUBLIC_UNATTESTED":
        return {"status": "NO_ALIGNED_COHORT", "queued": 0}
    closed_epoch = cohort.get("closed_1m_epoch")
    if (type(closed_epoch) is not int or closed_epoch <= 0
            or not closed_epoch <= now_epoch < closed_epoch + 90):
        return {"status": "CLOSED_MINUTE_STALE_OR_MISSING", "queued": 0}
    preview = cohort.get("provisional_watchlist_preview")
    rows = bulk.get("provisional_market_evidence")
    if (not isinstance(preview, list) or not 1 <= len(preview) <= 2
            or len(preview) != len(set(str(p) for p in preview))
            or not isinstance(rows, list)):
        return {"status": "NO_VALID_PREVIEW", "queued": 0}
    selected = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        p = row.get("pair")
        if isinstance(p, str) and p in preview:
            if p in selected:
                return {"status": "DUPLICATE_PUBLIC_CANDIDATE", "queued": 0}
            selected[p] = row
    if any(p not in selected for p in preview):
        return {"status": "INCOMPLETE_PUBLIC_CANDIDATE", "queued": 0}
    queued = 0
    reasons = []
    for pair in preview:
        row = selected[pair]
        if (not isinstance(pair, str) or pair in _EXCLUDED
                or row.get("closed_1m_epoch") != closed_epoch
                or row.get("score") is None
                or row.get("trade_authorized") is True):
            reasons.append("CANDIDATE_INCOMPLETE_OR_EXCLUDED")
            continue
        stage = row.get("scalp_setup_stage")
        setup = _label(
            stage.get("stage") if isinstance(stage, dict) else None,
            "SETUP_UNAVAILABLE",
        )
        ai_reason = _label(
            ai_shadow.get("reason") if isinstance(ai_shadow, dict) else None,
            "M04_NOT_CONNECTED",
        )
        completed = (ai_shadow.get("completed_roles")
                     if isinstance(ai_shadow, dict) else None)
        market_label = (
            "MARKET_RISK_STRATEGY_ADVISORY"
            if completed == ["MARKET", "RISK", "STRATEGY"]
            else "M04_" + ai_reason[:90]
        )
        risk_label = (
            "CAUTION_NO_TRADE" if "RISK_VETO" in ai_reason
            else "UNVERIFIED_ACCOUNT_AND_G1"
        )
        # Local source identity only, NEVER a signed issuer receipt.
        try:
            snapshot = json.dumps({
                "pair": pair, "closed_1m_epoch": closed_epoch,
                "candidate": row, "cohort": {
                    "status": cohort.get("status"),
                    "btc_regime": cohort.get("btc_regime"),
                    "preview": preview,
                },
                "g1_source_admission": bulk.get("g1_source_admission"),
                "ai_advisory": ai_shadow,
            }, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError):
            reasons.append("SOURCE_NOT_SERIALIZABLE")
            continue
        digest = sha256(snapshot.encode("utf-8")).hexdigest()
        proposal = owner.issue_market_advisory_proposal(
            pair=pair, digest=digest,
            closed_1m_epoch=closed_epoch,
            now_epoch=now_epoch,
            source_expires_epoch=closed_epoch + 90,
            setup=setup, market_status=market_label,
            risk_status=risk_label,
        )
        if proposal.get("accepted") is True:
            queued += 1
        else:
            reasons.append(proposal.get("reason", "PROPOSAL_NOT_QUEUED"))
    return {
        "status": "M05_OWNER_ADVISORY_QUEUED" if queued else "M05_OWNER_ADVISORY_NOT_QUEUED",
        "queued": queued, "attempted": len(preview),
        "diagnostics": reasons[:2],
        "owner_final_decision": None,
        "execution_allowed": False,
        "public_source_not_authenticated": True,
        "paid_ai_not_inferred": True,
    }
