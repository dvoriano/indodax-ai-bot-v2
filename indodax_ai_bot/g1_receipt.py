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


def validate_g1_coverage_manifest(
    manifest: Any, *, issuer_id: str, pair: str, closed_epoch: int,
) -> bool:
    """Validate a SOURCE-BOUND *claim* of complete G1 source coverage.

    This is a necessary, NEVER sufficient gate. The manifest alone is not
    independently verified. The caller MUST authenticate the trusted INTERNAL
    issuer's MAC over the immutable source digest containing this manifest,
    then separately establish that the issuer actually audited upstream tape.
    No proof can be manufactured from WS3/public REST snapshots or flags.
    Production has no trusted issuer configured.
    """
    if (type(manifest) is not dict or set(manifest) != {
            "schema_version", "issuer_id", "pair", "closed_1m_epoch",
            "upstream_origin_receipt_sha256", "closed_candles",
            "tape_windows", "btc_regime_receipt_sha256",
        } or type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] != 1
            or manifest.get("issuer_id") != issuer_id
            or manifest.get("pair") != pair
            or type(closed_epoch) is not int or closed_epoch <= 0
            or closed_epoch % 60 != 0
            or type(manifest.get("closed_1m_epoch")) is not int
            or manifest["closed_1m_epoch"] != closed_epoch
            or not isinstance(issuer_id, str) or not _ISSUER.fullmatch(issuer_id)
            or not isinstance(pair, str)
            or not re.fullmatch(r"[a-z0-9]+_idr", pair)):
        return False

    def digest(value: Any) -> bool:
        return type(value) is str and bool(_HEX64.fullmatch(value)) and value != "0" * 64

    if (not digest(manifest["upstream_origin_receipt_sha256"])
            or not digest(manifest["btc_regime_receipt_sha256"])):
        return False

    candles = manifest["closed_candles"]
    if type(candles) is not dict or set(candles) != {"1m", "5m", "15m"}:
        return False
    for label, span in (("1m", 60), ("5m", 300), ("15m", 900)):
        row = candles[label]
        # The last *finished* 5m/15m candle may close before the 1m.
        expected = closed_epoch - closed_epoch % span
        if (type(row) is not dict or set(row) != {
                "start_epoch", "end_epoch", "source_receipt_sha256",
            } or type(row["start_epoch"]) is not int
                or type(row["end_epoch"]) is not int
                or row["start_epoch"] != expected - span
                or row["end_epoch"] != expected
                or not digest(row["source_receipt_sha256"])):
            return False

    tape = manifest["tape_windows"]
    if type(tape) is not dict or set(tape) != {"15m", "1h"}:
        return False
    for label, seconds in (("15m", 900), ("1h", 3600)):
        row = tape[label]
        if (type(row) is not dict or set(row) != {
                "start_epoch", "end_epoch", "first_offset", "last_offset",
                "publication_count", "gap_count", "unrecovered_gap_count",
                "replay_reconciled", "coverage_receipt_sha256",
            } or any(type(row.get(k)) is not int for k in (
                "start_epoch", "end_epoch", "first_offset", "last_offset",
                "publication_count", "gap_count", "unrecovered_gap_count",
            )) or row["start_epoch"] != closed_epoch - seconds
                or row["end_epoch"] != closed_epoch
                or row["first_offset"] <= 0
                or row["last_offset"] < row["first_offset"]
                or row["publication_count"] <= 0
                # Offset counts are publication units, not trade counts.
                # Only an upstream issuer can establish semantics/coverage.
                or row["last_offset"] - row["first_offset"] + 1 != row["publication_count"]
                or row["gap_count"] != 0
                or row["unrecovered_gap_count"] != 0
                or row["replay_reconciled"] is not True
                or not digest(row["coverage_receipt_sha256"])):
            return False
    fifteen, hour = tape["15m"], tape["1h"]
    return (
        hour["first_offset"] <= fifteen["first_offset"]
        <= fifteen["last_offset"] <= hour["last_offset"]
        and fifteen["publication_count"] <= hour["publication_count"]
    )
