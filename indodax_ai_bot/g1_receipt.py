"""M04-C: independent internal G1 receipt verification, fail closed.

A receipt MAC authenticates an INTERNAL issuer with access to a separate secret.
It does NOT authenticate Indodax itself and cannot prove trade tape coverage.
No issuer/key is wired to the production public scanner or runtime.
"""
from __future__ import annotations

from hashlib import sha256
import hmac
import json
import re
from typing import Any, Mapping

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ISSUER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{1,79}$")


def canonical_receipt_bytes(claims: Mapping[str, Any]) -> bytes:
    """Strictly bounded signed fields, reused by an eventual independent issuer."""
    keys = ("schema_version", "issuer_id", "trace_id", "pair",
            "closed_1m_epoch", "source_digest_sha256", "expires_epoch")
    if set(claims) != set(keys):
        raise ValueError("INVALID_G1_RECEIPT_FIELDS")
    if (type(claims["schema_version"]) is not int or claims["schema_version"] != 1
            or type(claims["closed_1m_epoch"]) is not int or claims["closed_1m_epoch"] <= 0
            or type(claims["expires_epoch"]) is not int or claims["expires_epoch"] <= 0):
        raise ValueError("INVALID_G1_RECEIPT_EPOCH")
    if not isinstance(claims["issuer_id"], str) or not _ISSUER.fullmatch(claims["issuer_id"]):
        raise ValueError("INVALID_G1_ISSUER_ID")
    if (not isinstance(claims["trace_id"], str)
            or not 1 <= len(claims["trace_id"]) <= 128):
        raise ValueError("INVALID_G1_RECEIPT_TRACE")
    if (not isinstance(claims["pair"], str)
            or not re.fullmatch(r"[a-z0-9]+_idr", claims["pair"])):
        raise ValueError("INVALID_G1_RECEIPT_PAIR")
    if (not isinstance(claims["source_digest_sha256"], str)
            or not _HEX64.fullmatch(claims["source_digest_sha256"])):
        raise ValueError("INVALID_G1_RECEIPT_DIGEST")
    return json.dumps({k: claims[k] for k in keys},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


class G1ReceiptVerifier:
    """Bound to a caller-supplied trusted INTERNAL issuer secret, not market data.

    There is deliberately no default key, automatic issuer, or env-key lookup.
    A local hash alone and booleans in public JSON cannot pass verification.
    """

    def __init__(self, *, issuer_id: str, verification_secret: bytes):
        if not isinstance(verification_secret, bytes) or len(verification_secret) < 32:
            raise ValueError("INVALID_G1_VERIFIER_SECRET")
        if not isinstance(issuer_id, str) or not _ISSUER.fullmatch(issuer_id):
            raise ValueError("INVALID_G1_VERIFIER_ISSUER")
        self._secret = verification_secret
        self._issuer_id = issuer_id

    def verify(self, receipt: Any, *, trace_id: str, pair: str,
               closed_epoch: int, source_digest: str, now: int) -> bool:
        if not isinstance(receipt, dict) or set(receipt) != {"claims", "mac_sha256"}:
            return False
        claims = receipt.get("claims")
        mac = receipt.get("mac_sha256")
        if not isinstance(claims, dict) or not isinstance(mac, str) or not _HEX64.fullmatch(mac):
            return False
        try:
            payload = canonical_receipt_bytes(claims)
        except (TypeError, ValueError, KeyError):
            return False
        if (claims["issuer_id"] != self._issuer_id
                or claims["trace_id"] != trace_id
                or claims["pair"] != pair
                or claims["closed_1m_epoch"] != closed_epoch
                or claims["source_digest_sha256"] != source_digest
                or type(now) is not int or now <= 0
                or now > claims["expires_epoch"]
                or claims["expires_epoch"] > now + 60):
            return False
        expected = hmac.new(self._secret, payload, sha256).hexdigest()
        return hmac.compare_digest(expected, mac)
