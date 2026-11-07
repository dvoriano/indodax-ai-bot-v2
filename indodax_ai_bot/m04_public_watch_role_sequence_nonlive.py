"""Original M04 PUBLIC WATCH three-role sequencing rehearsal, FIXTURES ONLY.

No model caller, network transport, provider credentials, private account,
Telegram sender, database mutation, budget change, order, or new scanner.
This validates role order and prior-response lineage on supplied TEST TEXT;
it is never a real provider reply, G1 approval, or trading recommendation.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import time
from typing import Any

from .m04_public_watch_provider_preview_nonlive import (
    prepare_public_watch_provider_request_preview_offline,
    validate_public_watch_provider_response_shape_offline,
)
from .m04_radar_three_role_handoff_readonly import (
    inspect_original_radar_three_role_handoff_readonly,
)
from .m05_radar_momentum_nonlive import DEFAULT_DB
from .sumopod_provider import SumopodProviderError, _strict_json
from .three_role_ai import ROLES


def _base(status: str) -> dict[str, Any]:
    return {
        "stage": "M04_PUBLIC_WATCH_ROLE_SEQUENCE_FIXTURE_REHEARSAL",
        "status": status,
        "simulated_fixture_only": True,
        "candidate_count": 0,
        "candidate_reports": [],
        "simulated_response_shapes_validated": 0,
        "actual_model_responses_observed": 0,
        "provider_http_calls": 0,
        "owner_paid_ai_go_authorized": False,
        "provider_dispatch_allowed": False,
        "provider_availability_verified": False,
        "independent_g1_issuer_verified": False,
        "account_and_all_in_fees_verified": False,
        "telegram_messages_sent": 0,
        "owner_final_decision": None,
        "order_sent": False,
        "execution_allowed": False,
    }


def simulate_public_watch_role_sequence_offline(
    *, handoff: dict, now_epoch: int,
    fixture_response_json_by_pair: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    """Strict sequential MARKET→RISK→STRATEGY using only supplied fixture text.

    At each step the next role receives a newly isolated copy of source
    request context with its *earlier* already-validated TEST responses.
    No user-supplied callable or transport is accepted. Even a validated
    synthetic role result cannot be a qualified trade or real AI output.
    """
    preview = prepare_public_watch_provider_request_preview_offline(
        handoff=handoff, now_epoch=now_epoch,
    )
    if preview.get("status") not in (
        "M04_PUBLIC_WATCH_PROVIDER_CONTRACT_OFFLINE",
        "M04_PUBLIC_WATCH_NO_CURRENT_CANDIDATE",
    ):
        return _base("M04_PUBLIC_WATCH_SEQUENCE_SOURCE_UNVERIFIED")
    if (preview.get("provider_http_calls") != 0
            or preview.get("model_responses_observed") != 0
            or preview.get("owner_paid_ai_go_authorized") is not False
            or preview.get("real_provider_dispatch_allowed") is not False
            or preview.get("execution_allowed") is not False
            or preview.get("order_sent") is not False):
        return _base("M04_PUBLIC_WATCH_SEQUENCE_AUTHORITY_CONFLICT")
    count = preview["candidate_count"]
    if count == 0:
        if fixture_response_json_by_pair not in (None, {}):
            return _base("M04_PUBLIC_WATCH_SEQUENCE_FIXTURE_COHORT_MISMATCH")
        return _base("M04_PUBLIC_WATCH_SEQUENCE_NO_CURRENT_CANDIDATE")
    if fixture_response_json_by_pair is None:
        return _base("M04_PUBLIC_WATCH_SEQUENCE_TEST_RESPONSES_NOT_PROVIDED")
    if (not isinstance(fixture_response_json_by_pair, Mapping)
            or any(type(pair) is not str for pair in fixture_response_json_by_pair)
            or len(fixture_response_json_by_pair) != count):
        return _base("M04_PUBLIC_WATCH_SEQUENCE_FIXTURE_COHORT_MISMATCH")

    previews = preview.get("request_previews")
    if (not isinstance(previews, list)
            or len(previews) != count * len(ROLES)):
        return _base("M04_PUBLIC_WATCH_SEQUENCE_PREVIEW_SHAPE_MISMATCH")
    names = [previews[index]["pair"]
             for index in range(0, len(previews), len(ROLES))]
    if set(names) != set(fixture_response_json_by_pair):
        return _base("M04_PUBLIC_WATCH_SEQUENCE_FIXTURE_COHORT_MISMATCH")
    for pair in names:
        roles = fixture_response_json_by_pair[pair]
        if (not isinstance(roles, Mapping)
                or set(roles) != set(ROLES)
                or any(type(roles[role]) is not str
                       or not 1 <= len(roles[role]) <= 16384
                       for role in ROLES)):
            return _base("M04_PUBLIC_WATCH_SEQUENCE_FIXTURE_ROLE_MISMATCH")

    all_reports: list[dict[str, Any]] = []
    # Fail the whole batch on an invalid role. No partial reports that could
    # accidentally be interpreted as a complete chain or owner proposal.
    for group_idx, pair in enumerate(names):
        group = previews[group_idx * 3:(group_idx + 1) * 3]
        bound: list[dict[str, Any]] = []
        role_hashes: list[dict[str, str]] = []
        source_digest = group[0]["evidence_digest_sha256"]
        for role_index, role in enumerate(ROLES):
            entry = group[role_index]
            if (entry.get("role") != role or entry.get("pair") != pair
                    or entry.get("evidence_digest_sha256") != source_digest
                    or entry.get("depends_on_roles") != list(ROLES[:role_index])
                    or entry.get("send_allowed") is not False
                    or entry.get("response_observed") is not False
                    or entry.get("owner_paid_ai_go_authorized") is not False):
                return _base("M04_PUBLIC_WATCH_SEQUENCE_PREVIEW_ROLE_MISMATCH")
            request = deepcopy(entry["request_body_preview"])
            if (not isinstance(request, dict)
                    or request.get("stream") is not False
                    or not isinstance(request.get("messages"), list)
                    or len(request["messages"]) != 2
                    or request["messages"][1].get("role") != "user"):
                return _base("M04_PUBLIC_WATCH_SEQUENCE_REQUEST_UNVERIFIED")
            try:
                source = json.loads(request["messages"][1]["content"])
            except (ValueError, TypeError):
                return _base("M04_PUBLIC_WATCH_SEQUENCE_REQUEST_UNVERIFIED")
            if (not isinstance(source, dict)
                    or source.get("contract") !=
                    "M04_PUBLIC_WATCH_RESEARCH_UNTRUSTED_DATA_ONLY"
                    or source.get("role") != role
                    or source.get("pair") != pair
                    or source.get("evidence_digest_sha256") != source_digest
                    or source.get("required_prior_roles") != list(ROLES[:role_index])
                    or source.get("actual_prior_role_responses") is not None
                    or source.get("independent_g1_issuer_verified") is not False
                    or source.get("owner_paid_ai_go_authorized") is not False
                    or source.get("qualified_trade_proposal_authorized") is not False
                    or source.get("execution_allowed") is not False):
                return _base("M04_PUBLIC_WATCH_SEQUENCE_REQUEST_UNVERIFIED")

            # Local rehearsal only: roles are bound in strict order, and
            # subsequent role context carries exactly earlier test responses
            # from the same candidate source, never another pair's output.
            source["actual_prior_role_responses"] = deepcopy(bound)
            if (len(source["actual_prior_role_responses"]) != role_index
                    or any(item["role"] != ROLES[i]
                           or item["pair"] != pair
                           or item["evidence_digest_sha256"] != source_digest
                           for i, item in enumerate(source[
                               "actual_prior_role_responses"
                           ]))):
                return _base("M04_PUBLIC_WATCH_SEQUENCE_LINEAGE_MISMATCH")
            raw = fixture_response_json_by_pair[pair][role]
            validation = validate_public_watch_provider_response_shape_offline(
                raw_response_json=raw, request_preview=entry,
            )
            if validation["response_shape_valid"] is not True:
                return _base("M04_PUBLIC_WATCH_SEQUENCE_FIXTURE_RESPONSE_REJECTED")
            try:
                parsed = _strict_json(raw)
                canonical = json.dumps(
                    parsed, sort_keys=True, separators=(",", ":"),
                    allow_nan=False, ensure_ascii=True,
                ).encode("ascii")
            except (ValueError, TypeError, UnicodeError,
                    SumopodProviderError):
                return _base("M04_PUBLIC_WATCH_SEQUENCE_FIXTURE_RESPONSE_REJECTED")
            bound.append(parsed)
            role_hashes.append({
                "role": role,
                "stance": parsed["stance"],
                "test_fixture_response_sha256": sha256(canonical).hexdigest(),
            })
        all_reports.append({
            "pair": pair,
            "evidence_digest_sha256": source_digest,
            "validated_fixture_role_count": 3,
            "source_is_public_unattested": True,
            "role_fixture_reports": role_hashes,
            "complete_role_order_verified_in_test": True,
            "all_prior_role_contexts_same_pair_and_digest": True,
            "g1_verified": False,
            "model_origin_verified": False,
            "trade_proposal_created": False,
            "execution_allowed": False,
        })
    result = _base("M04_PUBLIC_WATCH_SEQUENCE_FIXTURE_VALIDATED_ONLY")
    result["candidate_count"] = len(all_reports)
    result["candidate_reports"] = all_reports
    result["simulated_response_shapes_validated"] = len(all_reports) * 3
    return result


def inspect_original_public_watch_role_sequence_offline(
    *, db_path: Path = DEFAULT_DB, now_epoch: int | None = None,
) -> dict[str, Any]:
    """Read original M03 snapshot ONCE, never invent test/model responses."""
    now = int(time.time()) if now_epoch is None else now_epoch
    if type(now) is not int or now <= 0:
        return _base("M04_PUBLIC_WATCH_SEQUENCE_INVALID_CLOCK")
    handoff = inspect_original_radar_three_role_handoff_readonly(
        db_path=db_path, now_epoch=now,
    )
    return simulate_public_watch_role_sequence_offline(
        handoff=handoff, now_epoch=now,
    )


def main() -> int:
    result = inspect_original_public_watch_role_sequence_offline()
    print(json.dumps({
        "status": result["status"],
        "candidate_count": result["candidate_count"],
        "simulated_response_shapes_validated":
            result["simulated_response_shapes_validated"],
        "actual_model_responses_observed":
            result["actual_model_responses_observed"],
        "provider_http_calls": result["provider_http_calls"],
        "owner_paid_ai_go_authorized":
            result["owner_paid_ai_go_authorized"],
        "order_sent": result["order_sent"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
