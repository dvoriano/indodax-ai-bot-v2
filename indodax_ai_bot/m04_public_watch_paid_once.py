"""Owner-gated one-shot PUBLIC WATCH Sumopod runner, never a trading G1.

Explicit, separately owner-chosen daily/per-request USD micro-unit limits
and provider pricing acknowledgement required before any billable request.
Original M03 public handoff is read-only. Only FIRST current candidate and
up to three sequential MARKET/RISK/STRATEGY requests. No Telegram, orders,
new timer, scanner, retries, synthetic trade signals, or account access.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from hashlib import sha256
import json
import time
from typing import Any

from .ai_budget import AiBudgetError, PersistentAiBudget
from .m04_public_watch_provider_preview_nonlive import (
    prepare_public_watch_provider_request_preview_offline,
    validate_public_watch_provider_response_shape_offline,
)
from .sumopod_provider import (
    MODEL_RE, SumopodProviderError, _post, _strict_json,
)
from .three_role_ai import ROLES

# Never read this from an environment variable or a background service.
OWNER_PAID_ONCE_CONFIRMATION = "OWNER_PUBLIC_WATCH_PAID_TEST_ONCE"
MAX_REQUEST_BYTES = 32768
MAX_OUTPUT_TOKENS = 650
HTTP_TIMEOUT_SECONDS = 8.0
Transport = Callable[[dict[str, Any], dict[str, str], float], Any]


def _status(reason: str) -> dict[str, Any]:
    return {
        "stage": "M04_PUBLIC_WATCH_OWNER_PAID_ONE_SHOT",
        "status": reason,
        "scope": "ONE_FRESH_PUBLIC_WATCH_ONLY_NOT_G1",
        "role_order": list(ROLES),
        "pair": None,
        "evidence_digest_sha256": None,
        "roles_completed": [],
        "responses": [],
        "provider_http_attempts": 0,
        "genuine_provider_responses_verified": 0,
        "injected_test_transport": False,
        "owner_paid_test_approved": False,
        "provider_pricing_acknowledged_by_owner": False,
        "pricing_independently_verified_by_code": False,
        "source_g1_verified": False,
        "risk_status": "NO_TRADE",
        "strategy_status": "WAIT",
        "owner_final_decision": None,
        "qualified_trade_proposal_created": False,
        "telegram_messages_sent": 0,
        "order_sent": False,
        "execution_allowed": False,
    }


def run_public_watch_paid_once(
    *, handoff: dict, now_fn: Callable[[], float] = time.time,
    owner_models: Mapping[str, str] | None = None,
    api_key: str | None = None,
    budget: PersistentAiBudget | None = None,
    owner_confirmation: str | None = None,
    owner_pricing_reviewed: bool = False,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """One-shot explicit owner API invocation; never used by original M05.

    The injected transport is test-only and can never establish provider
    origin. A real TLS POST uses the existing _post; no automatic retry.
    Every role/source is reserved durably BEFORE a possibly billable call.
    No defaults for owner models, expense caps, price, or authorization.
    """
    denied = _status("M04_PUBLIC_WATCH_PAID_GO_OR_COST_LIMITS_REQUIRED")
    if (owner_confirmation != OWNER_PAID_ONCE_CONFIRMATION
            or owner_pricing_reviewed is not True
            or not isinstance(budget, PersistentAiBudget)
            or not isinstance(owner_models, Mapping)
            or set(owner_models) != set(ROLES)
            or any(type(owner_models[r]) is not str
                   or not MODEL_RE.fullmatch(owner_models[r]) for r in ROLES)
            or type(api_key) is not str or not api_key
            or len(api_key) > 512 or any(ch.isspace() for ch in api_key)):
        return denied
    try:
        now = int(now_fn())
    except (ValueError, TypeError, OverflowError, OSError):
        return _status("M04_PUBLIC_WATCH_CLOCK_UNAVAILABLE")
    if now <= 0:
        return _status("M04_PUBLIC_WATCH_CLOCK_UNAVAILABLE")

    if not isinstance(handoff, dict) or not isinstance(handoff.get("candidates"), list):
        return _status("M04_PUBLIC_WATCH_SOURCE_INVALID")
    if not 1 <= len(handoff["candidates"]) <= 2:
        return _status("M04_PUBLIC_WATCH_NO_CURRENT_CANDIDATE")

    # Validate the ENTIRE original cohort first, before slicing to one.
    original = prepare_public_watch_provider_request_preview_offline(
        handoff=handoff, now_epoch=now, models_by_role=owner_models,
    )
    if (original.get("status") != "M04_PUBLIC_WATCH_PROVIDER_CONTRACT_OFFLINE"
            or original.get("candidate_count") != len(handoff["candidates"])
            or original.get("request_preview_count") != len(handoff["candidates"]) * 3
            or original.get("provider_http_calls") != 0
            or original.get("real_provider_dispatch_allowed") is not False):
        return _status("M04_PUBLIC_WATCH_SOURCE_OR_PROVIDER_PREVIEW_REJECTED")

    chosen = original["request_previews"][:3]
    if (len(chosen) != 3
            or [p.get("role") for p in chosen] != list(ROLES)
            or len({p.get("pair") for p in chosen}) != 1
            or len({p.get("evidence_digest_sha256") for p in chosen}) != 1
            or any(p.get("send_allowed") is not False
                   or p.get("owner_paid_ai_go_authorized") is not False
                   or p.get("response_observed") is not False
                   for p in chosen)):
        return _status("M04_PUBLIC_WATCH_SOURCE_ROLE_BINDING_REJECTED")

    pair = chosen[0]["pair"]
    digest = chosen[0]["evidence_digest_sha256"]
    first_snapshot = handoff["candidates"][0]["public_evidence"]
    epoch = first_snapshot["observed_epoch"]
    result = _status("M04_PUBLIC_WATCH_PAID_READY_NO_CALL_YET")
    result["pair"] = pair
    result["evidence_digest_sha256"] = digest
    result["owner_paid_test_approved"] = True
    result["provider_pricing_acknowledged_by_owner"] = True
    result["injected_test_transport"] = transport is not None

    validated_prior: list[dict[str, Any]] = []
    for index, role in enumerate(ROLES):
        try:
            current = int(now_fn())
        except (ValueError, TypeError, OverflowError, OSError):
            result["status"] = "M04_PUBLIC_WATCH_PAID_CLOCK_UNAVAILABLE"
            return result
        if (current < epoch or current - epoch > 90
                or current - now > 90):
            result["status"] = "M04_PUBLIC_WATCH_SOURCE_EXPIRED_BEFORE_ROLE"
            return result

        p = chosen[index]
        request = deepcopy(p["request_body_preview"])
        try:
            context = json.loads(request["messages"][1]["content"])
            if (context.get("pair") != pair
                    or context.get("role") != role
                    or context.get("evidence_digest_sha256") != digest
                    or context.get("actual_prior_role_responses") is not None
                    or context.get("independent_g1_issuer_verified") is not False
                    or context.get("qualified_trade_proposal_authorized") is not False
                    or context.get("execution_allowed") is not False
                    or context.get("required_prior_roles") != list(ROLES[:index])
                    or request.get("model") != owner_models[role]
                    or request.get("stream") is not False):
                raise ValueError("ROLE_SOURCE_OR_MODEL_MISMATCH")
            context["actual_prior_role_responses"] = deepcopy(validated_prior)
            context["owner_paid_ai_go_authorized"] = True
            canonical = json.dumps(
                context, sort_keys=True, separators=(",", ":"),
                ensure_ascii=True, allow_nan=False,
            )
            if len(canonical.encode("utf-8")) > MAX_REQUEST_BYTES:
                raise ValueError("ROLE_INPUT_TOO_LARGE")
            request["messages"][1]["content"] = canonical
            request["max_tokens"] = MAX_OUTPUT_TOKENS
            header = {
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        except (ValueError, TypeError, KeyError, OverflowError):
            result["status"] = "M04_PUBLIC_WATCH_ROLE_INPUT_REJECTED"
            return result

        # Durable dedup+cost reservation must occur before network, even
        # if upstream fails or times out. No retry/refund on uncertain charge.
        try:
            budget.reserve(role, pair, digest)
        except AiBudgetError:
            result["status"] = "M04_PUBLIC_WATCH_BUDGET_OR_DUPLICATE_BLOCKED"
            return result

        result["provider_http_attempts"] += 1
        try:
            envelope = (transport or _post)(
                request, header, HTTP_TIMEOUT_SECONDS,
            )
        except Exception:
            result["status"] = "M04_PUBLIC_WATCH_PROVIDER_FAILED_NO_RETRY"
            return result
        try:
            if (not isinstance(envelope, dict)
                    or not isinstance(envelope.get("choices"), list)
                    or len(envelope["choices"]) != 1
                    or not isinstance(envelope["choices"][0], dict)
                    or envelope["choices"][0].get("finish_reason") != "stop"
                    or not isinstance(envelope["choices"][0].get("message"), dict)
                    or envelope["choices"][0]["message"].get("role") != "assistant"
                    or envelope.get("model") != owner_models[role]):
                raise ValueError("INVALID_PROVIDER_RESPONSE")
            usage = envelope.get("usage")
            if (not isinstance(usage, dict)
                    or type(usage.get("prompt_tokens")) is not int
                    or type(usage.get("completion_tokens")) is not int
                    or not 0 <= usage["prompt_tokens"] <= 1_000_000
                    or not 0 <= usage["completion_tokens"] <= MAX_OUTPUT_TOKENS):
                raise ValueError("INVALID_PROVIDER_USAGE")
            raw = envelope["choices"][0]["message"].get("content")
            verdict = validate_public_watch_provider_response_shape_offline(
                raw_response_json=raw, request_preview=p,
            )
            if verdict.get("response_shape_valid") is not True:
                raise ValueError("INVALID_MODEL_ROLE_OR_PUBLIC_WATCH_STANCE")
            parsed = _strict_json(raw)
            if (parsed["role"] != role or parsed["pair"] != pair
                    or parsed["evidence_digest_sha256"] != digest):
                raise ValueError("MODEL_EVIDENCE_MISMATCH")
        except (ValueError, TypeError, KeyError, SumopodProviderError):
            result["status"] = "M04_PUBLIC_WATCH_PROVIDER_RESPONSE_REJECTED"
            return result

        validated_prior.append(parsed)
        result["roles_completed"].append(role)
        result["responses"].append({
            "role": role,
            "model_id_requested_by_owner": owner_models[role],
            "stance": parsed["stance"],
            "reason": parsed["reason"],
            "source_digest_sha256": digest,
            "prompt_tokens_reported": usage["prompt_tokens"],
            "completion_tokens_reported": usage["completion_tokens"],
            # Vendor-reported token counts do not prove actual charged cost.
            "actual_charge_verified": False,
        })
        if transport is None:
            result["genuine_provider_responses_verified"] += 1

    result["status"] = (
        "M04_PUBLIC_WATCH_THREE_ROLE_PROVIDER_RESPONSES_RECEIVED"
        if transport is None
        else "M04_PUBLIC_WATCH_TEST_TRANSPORT_THREE_ROLES_ONLY"
    )
    return result
