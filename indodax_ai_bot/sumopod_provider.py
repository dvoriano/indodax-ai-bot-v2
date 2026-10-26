"""M04-B: Sumopod Chat Completions adapter, explicit opt-in, never a BUY gate.

OFF by default. Runtime M04-A intentionally does not instantiate this class:
G1 issuer authentication, complete trade coverage and owner authorization are
NOT implemented. Tests inject a fake transport; no paid request is made.

Official docs: https://docs.sumopod.com/guides/ai/openai-compatible-api
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from os import environ
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROLES = ("MARKET", "RISK", "STRATEGY")
URL = "https://ai.sumopod.com/v1/chat/completions"
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:-]{0,127}$")
PAIR_RE = re.compile(r"^[a-z0-9]+_idr$")
DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")

# Stubbed transport in unit tests receives payload, headers, timeout.
Transport = Callable[[dict[str, Any], dict[str, str], float], Any]

ROLE_PROMPTS = {
    "MARKET": (
        "You are MARKET in a conservative IDR spot scalping pipeline. "
        "Analyze ONLY the attached source-bound market evidence and BTC context. "
        "Do not manufacture prices, trades, complete trade coverage, news or fills. "
        "Return ONLY a JSON object with role='MARKET', pair, "
        "evidence_digest_sha256, bias one of BULLISH/BEARISH/MIXED, and reason."
    ),
    "RISK": (
        "You are RISK with non-overridable veto authority. Inspect the same "
        "source-bound market evidence and the prior MARKET response if present. "
        "Missing trade coverage, source issuer, liquidity, capital or spread "
        "evidence warrants NO_TRADE. Never assert that a BUY is safe or executed. "
        "Return ONLY JSON with role='RISK', pair, evidence_digest_sha256, "
        "decision one of ALLOW/NO_TRADE/EXIT_REVIEW, and reason."
    ),
    "STRATEGY": (
        "You are STRATEGY, called ONLY after a valid RISK ALLOW. Use the SAME "
        "evidence and prior MARKET/RISK decisions. If entry/size/fee/slippage "
        "information is absent choose WAIT, do not invent quantitative profits. "
        "Return ONLY JSON with role='STRATEGY', pair, evidence_digest_sha256, "
        "action one of BUY/WAIT/EXIT_REVIEW, reason, projected_net_profit_idr "
        "and projected_net_roi_percent as finite nonnegative decimal strings. "
        "These estimates are diagnostic and never an order authorization."
    ),
}


class SumopodProviderError(RuntimeError):
    """Nonsecret stable error codes only. Do not leak headers or response bodies."""


def _model_id(value: Any) -> str:
    if not isinstance(value, str) or not MODEL_RE.fullmatch(value):
        raise SumopodProviderError("INVALID_SUMOPOD_MODEL_ID")
    return value


def _strict_json(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text or len(text) > 16_384:
        raise SumopodProviderError("INVALID_SUMOPOD_MESSAGE")

    def unique(items: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in items:
            if k in out:
                raise SumopodProviderError("DUPLICATE_JSON_KEY")
            out[k] = v
        return out

    def deny_constant(_: str) -> None:
        raise SumopodProviderError("INVALID_JSON_CONSTANT")

    try:
        output = json.loads(text, object_pairs_hook=unique, parse_constant=deny_constant)
    except (ValueError, TypeError) as exc:
        raise SumopodProviderError("INVALID_SUMOPOD_JSON") from None
    if not isinstance(output, dict):
        raise SumopodProviderError("SUMOPOD_RESPONSE_NOT_OBJECT")
    return output


def _post(payload: dict[str, Any], headers: dict[str, str], timeout: float) -> Any:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = Request(URL, body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            result = response.read(131_073)
        if len(result) > 131_072:
            raise SumopodProviderError("SUMOPOD_RESPONSE_TOO_LARGE")
        return json.loads(result)
    except SumopodProviderError:
        raise
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, TypeError):
        # Never include upstream exception bodies, headers, keys, or messages.
        raise SumopodProviderError("SUMOPOD_HTTP_OR_JSON_FAILED") from None


class SumopodChatAdapter:
    """Three models, three existing roles, one provider adapter. Network OFF by default.

    Actual paid network is impossible unless caller explicitly sets
    allow_paid_requests=True. This adapter is NOT wired into the runtime today.
    """

    def __init__(
        self, *, api_key: str, models: Mapping[str, str],
        transport: Transport | None = None, allow_paid_requests: bool = False,
        timeout_seconds: float = 8.0, max_tokens: int = 650,
    ) -> None:
        if (not isinstance(api_key, str) or not api_key.strip()
                or len(api_key) > 512 or any(ch.isspace() for ch in api_key)):
            raise SumopodProviderError("INVALID_SUMOPOD_API_KEY")
        if set(models) != set(ROLES):
            raise SumopodProviderError("SUMOPOD_REQUIRES_THREE_ROLE_MODELS")
        self._key = api_key  # never repr/log/serialize this object
        self._models = {role: _model_id(models[role]) for role in ROLES}
        if (isinstance(timeout_seconds, bool) or
                not isinstance(timeout_seconds, (int, float))
                or not 0 < timeout_seconds <= 20):
            raise SumopodProviderError("INVALID_SUMOPOD_TIMEOUT")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 64 <= max_tokens <= 1024:
            raise SumopodProviderError("INVALID_SUMOPOD_TOKEN_LIMIT")
        self._timeout = float(timeout_seconds)
        self._max_tokens = max_tokens
        self._transport = transport
        self._allow_paid_requests = allow_paid_requests is True
        self.usage_events: list[dict[str, Any]] = []

    def __repr__(self) -> str:
        return "<SumopodChatAdapter key=REDACTED network_default_off>"

    @classmethod
    def configured_from_env(
        cls, source: Mapping[str, str] | None = None, *,
        transport: Transport | None = None,
    ) -> "SumopodChatAdapter":
        env = environ if source is None else source
        return cls(
            api_key=env.get("SUMOPOD_API_KEY", ""),
            models={
                "MARKET": env.get("SUMOPOD_MODEL_MARKET", ""),
                "RISK": env.get("SUMOPOD_MODEL_RISK", ""),
                "STRATEGY": env.get("SUMOPOD_MODEL_STRATEGY", ""),
            },
            transport=transport,
            # Never turn paid network on using an env flag. This stage is
            # test-only; the release gate will require a separate owner GO.
            allow_paid_requests=False,
        )

    def __call__(self, role: str, evidence: dict[str, Any]) -> dict[str, Any]:
        if role not in ROLES:
            raise SumopodProviderError("INVALID_AI_ROLE")
        if not isinstance(evidence, dict) or not isinstance(evidence.get("source"), dict):
            raise SumopodProviderError("INVALID_AI_EVIDENCE")
        source = evidence["source"]
        pair, digest = source.get("pair"), evidence.get("evidence_digest_sha256")
        if (not isinstance(pair, str) or not PAIR_RE.fullmatch(pair)
                or not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest)):
            raise SumopodProviderError("INVALID_SOURCE_BOUND_ROLE_INPUT")
        # Model sees exactly the source received by the ThreeRoleShadow and
        # the previous role output, never unbound market facts.
        body = {
            "model": self._models[role],
            "messages": [
                {"role": "system", "content": ROLE_PROMPTS[role]},
                {"role": "user", "content": json.dumps(evidence, sort_keys=True, separators=(",", ":"))},
            ],
            "max_tokens": self._max_tokens,
            "stream": False,
        }
        headers = {
            "Authorization": "Bearer " + self._key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._transport is None and not self._allow_paid_requests:
            raise SumopodProviderError("SUMOPOD_PAID_NETWORK_DISABLED")
        try:
            result = (self._transport or _post)(body, headers, self._timeout)
        except SumopodProviderError:
            raise
        except Exception:
            # Injected test transports may throw; no details leak.
            raise SumopodProviderError("SUMOPOD_TRANSPORT_UNAVAILABLE") from None
        if not isinstance(result, dict):
            raise SumopodProviderError("INVALID_SUMOPOD_ENVELOPE")
        choices = result.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise SumopodProviderError("INVALID_SUMOPOD_CHOICES")
        choice = choices[0]
        if choice.get("finish_reason") not in ("stop",):
            raise SumopodProviderError("SUMOPOD_INCOMPLETE_OR_REFUSED")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise SumopodProviderError("INVALID_SUMOPOD_MESSAGE")
        parsed = _strict_json(message.get("content"))
        if (parsed.get("role") != role or parsed.get("pair") != pair
                or parsed.get("evidence_digest_sha256") != digest):
            raise SumopodProviderError("SUMOPOD_ROLE_OR_SOURCE_MISMATCH")
        usage = result.get("usage")
        if isinstance(usage, dict):
            prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
            if (type(prompt) is int and type(completion) is int
                    and prompt >= 0 and completion >= 0):
                self.usage_events.append({
                    "role": role, "model": self._models[role],
                    "prompt_tokens": prompt, "completion_tokens": completion,
                })
        return parsed
