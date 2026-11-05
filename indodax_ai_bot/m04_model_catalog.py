"""M04 owner-opt-in provider MODEL CATALOG metadata GET, NOT AI inference.

An OpenAI-compatible service MAY implement GET /v1/models. Catalog listing
alone neither proves an individual model is callable nor establishes prices.
This module cannot call /chat/completions, infer, spend on tokens or trade.
No network occurs unless explicit metadata-only GO is passed by the owner.
"""
from __future__ import annotations

import json
import ssl
from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPRedirectHandler, HTTPSHandler, Request, build_opener,
)

from .sumopod_provider import (
    ROLES, SumopodProviderError, _model_id,
    inspect_provider_configuration_offline,
)

MODELS_URL = "https://ai.sumopod.com/v1/models"
MAX_RESPONSE_BYTES = 262144
MAX_CATALOG_MODELS = 2048

CatalogTransport = Callable[[str, str, Mapping[str, str], float], Any]


class M04CatalogDenied(RuntimeError):
    """Stable, redacted status. Never propagate upstream error bodies or keys."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _metadata_get(_method: str, url: str,
                  headers: Mapping[str, str], timeout: float) -> Any:
    if _method != "GET" or url != MODELS_URL:
        raise M04CatalogDenied("M04_CATALOG_UNEXPECTED_NETWORK_DESTINATION")
    request = Request(url, headers=dict(headers), method="GET")
    opener = build_opener(
        _NoRedirect(),
        HTTPSHandler(context=ssl.create_default_context()),
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise M04CatalogDenied("M04_CATALOG_RESPONSE_TOO_LARGE")
        return body
    except M04CatalogDenied:
        raise
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        raise M04CatalogDenied("M04_CATALOG_PROVIDER_GET_UNAVAILABLE") from None


def _decode_catalog(data: Any) -> set[str]:
    if isinstance(data, bytes):
        if not 0 < len(data) <= MAX_RESPONSE_BYTES:
            raise M04CatalogDenied("M04_CATALOG_RESPONSE_SIZE_INVALID")
        try:
            data = data.decode("utf-8")
        except UnicodeError:
            raise M04CatalogDenied("M04_CATALOG_RESPONSE_INVALID") from None
    if isinstance(data, str):
        if not 0 < len(data.encode("utf-8")) <= MAX_RESPONSE_BYTES:
            raise M04CatalogDenied("M04_CATALOG_RESPONSE_SIZE_INVALID")

        def unique(items: list[tuple[str, Any]]) -> dict:
            out: dict[str, Any] = {}
            for key, value in items:
                if key in out:
                    raise M04CatalogDenied("M04_CATALOG_DUPLICATE_JSON_KEY")
                out[key] = value
            return out

        def reject_constant(_value: str):
            raise M04CatalogDenied("M04_CATALOG_INVALID_NUMBER")

        try:
            data = json.loads(
                data, object_pairs_hook=unique,
                parse_constant=reject_constant,
            )
        except (ValueError, TypeError, UnicodeError):
            raise M04CatalogDenied("M04_CATALOG_RESPONSE_INVALID") from None

    if (type(data) is not dict
            or data.get("object") != "list"
            or type(data.get("data")) is not list
            or len(data["data"]) > MAX_CATALOG_MODELS):
        raise M04CatalogDenied("M04_CATALOG_RESPONSE_SCHEMA_INVALID")
    seen = set()
    for row in data["data"]:
        if type(row) is not dict or type(row.get("id")) is not str:
            raise M04CatalogDenied("M04_CATALOG_MODEL_ROW_INVALID")
        try:
            model = _model_id(row["id"])
        except SumopodProviderError:
            raise M04CatalogDenied("M04_CATALOG_MODEL_ROW_INVALID") from None
        if model in seen:
            raise M04CatalogDenied("M04_CATALOG_DUPLICATE_MODEL_ID")
        seen.add(model)
    return seen


def inspect_sumopod_model_catalog_readonly_once(
    *, source: Mapping[str, str], allow_metadata_network: bool = False,
    transport: CatalogTransport | None = None,
) -> dict[str, Any]:
    """Single bounded GET, redacted response, owner-specified role models.

    Caller must separately opt in to this metadata network request.
    Successful GET is NOT billable chat inference, an actual model response,
    a verified provider price, G1 attestation, or trading approval.
    """
    if allow_metadata_network is not True:
        raise M04CatalogDenied("M04_CATALOG_EXPLICIT_NETWORK_GO_REQUIRED")
    status = inspect_provider_configuration_offline(source)
    if status["status"] != "M04_PROVIDER_LOCAL_CONFIG_PRESENT_UNVERIFIED":
        raise M04CatalogDenied("M04_CATALOG_LOCAL_CONFIG_NOT_READY")
    key = source["SUMOPOD_API_KEY"]
    models = status["configured_models_by_role"]
    headers = {
        "Authorization": "Bearer " + key,
        "Accept": "application/json",
    }
    fetch = transport if transport is not None else _metadata_get
    try:
        payload = fetch("GET", MODELS_URL, headers, 6.0)
        found = _decode_catalog(payload)
    except M04CatalogDenied:
        raise
    except (OSError, ValueError, TypeError, RuntimeError):
        raise M04CatalogDenied("M04_CATALOG_PROVIDER_GET_UNAVAILABLE") from None
    present = {role: models[role] in found for role in ROLES}
    return {
        "stage": "M04_SUMOPOD_MODEL_CATALOG_READONLY",
        "status": (
            "M04_MODELS_LISTED_METADATA_ONLY"
            if all(present.values())
            else "M04_SELECTED_MODEL_IDS_NOT_ALL_LISTED"
        ),
        "model_ids_by_role": {role: models[role] for role in ROLES},
        "selected_model_ids_present_in_catalog": present,
        "only_one_fixed_provider_metadata_get": True,
        "metadata_get_completed": True,
        "provider_response_redacted": True,
        "actual_provider_inference_verified": False,
        "model_context_window_verified": False,
        "actual_provider_pricing_verified": False,
        "g1_independent_issuer_verified": False,
        "three_real_role_responses_observed": False,
        "paid_ai_go_authorized": False,
        "paid_chat_completion_calls": 0,
        "order_sent": False,
        "execution_allowed": False,
        "final_decision": "REJECT",
    }
