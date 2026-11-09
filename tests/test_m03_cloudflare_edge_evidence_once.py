"""One original-budgeted M03 public edge probe: offline, no data/secret leaks."""
from __future__ import annotations

from datetime import datetime, timezone
from email.message import Message
import json
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from indodax_ai_bot.m03_cloudflare_edge_evidence_once import (
    _headers_evidence, collect_one_public_rest_edge_evidence
)
from indodax_ai_bot.public_market import PublicDataError, IndodaxPublicMarket


class M03EdgeProofTests(unittest.TestCase):
    def setUp(self):
        self.off = {
            "INDODAX_TRADING_ENABLED": "NO",
            "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
            "INDODAX_EXECUTION_MODE": "DRY_RUN",
            "INDODAX_M03_REQUIRE_EGRESS_ADMISSION": "YES",
        }
        self.fixed_time = lambda: datetime(
            2026, 11, 9, 5, 0, 0, tzinfo=timezone.utc
        )

    def test_http403_cf_challenge_ray_only_and_no_body(self):
        headers = Message()
        headers.add_header("Server", "cloudflare")
        headers.add_header("Cf-Ray", "123abcDEF987-SIN")
        headers.add_header("cf-mitigated", "challenge")
        headers.add_header("Content-Type", "text/html; charset=UTF-8")
        headers.add_header("Set-Cookie", "SECRET_NEVER_EXPOSE")
        err = HTTPError(
            "https://indodax.com/api/server_time", 403,
            "SECRET BODY NEVER EXPOSE", headers, None
        )
        calls = []
        class FakeMarket:
            public_get_attempt_count = 1
            def __init__(self, timeout):
                self.timeout = timeout
            def _get(self, path):
                calls.append(path)
                raise PublicDataError("PUBLIC_HTTP_OR_JSON_UNAVAILABLE") from err

        report = collect_one_public_rest_edge_evidence(
            env=self.off, market_factory=FakeMarket,
            now_fn=self.fixed_time
        )
        self.assertEqual(calls, ["/api/server_time"])
        self.assertEqual(report["status"], "HTTP_RESPONSE_ERROR")
        self.assertEqual(report["http_status"], 403)
        self.assertTrue(report["cf_challenge"])
        self.assertTrue(report["cloudflare_server"])
        self.assertEqual(report["cf_ray"], "123abcDEF987-SIN")
        self.assertEqual(report["response_content_type"], "text/html")
        self.assertEqual(report["public_get_attempts"], 1)
        for private in (
            "SECRET_NEVER_EXPOSE", "SECRET BODY",
            "https://indodax.com", "Set-Cookie",
        ):
            self.assertNotIn(private, json.dumps(report))

    def test_original_public_adapter_http403_remains_inspectable_without_leaks(self):
        # This is the exact PR244→existing edge diagnostic join, previously
        # not exercised: fixed M03 scanner reason AND CF response provenance.
        from email.message import Message
        headers = Message()
        headers.add_header("Server", "cloudflare")
        headers.add_header("Cf-Ray", "1234abc-SIN")
        headers.add_header("Content-Type", "text/html")
        headers.add_header("Set-Cookie", "UNTRUSTED-SECRET")
        original = HTTPError(
            "https://indodax.com/api/server_time", 403,
            "DO_NOT_SHOW_HTTP_REASON", headers, None,
        )
        class Adapter:
            def __init__(self, timeout):
                self.market = IndodaxPublicMarket(fetch=lambda _path: None)
                self.public_get_attempt_count = 0
            def _get(self, path):
                self.public_get_attempt_count += 1
                return self.market._http_json(path)

        with patch("indodax_ai_bot.public_market.urlopen",
                   side_effect=original) as network:
            report = collect_one_public_rest_edge_evidence(
                env=self.off, market_factory=Adapter, now_fn=self.fixed_time,
            )
        self.assertEqual(network.call_count, 1)
        self.assertEqual(report["status"], "HTTP_RESPONSE_ERROR")
        self.assertEqual(report["http_status"], 403)
        self.assertEqual(report["cf_ray"], "1234abc-SIN")
        self.assertEqual(report["response_content_type"], "text/html")
        self.assertEqual(report["public_get_attempts"], 1)
        self.assertFalse(report["trading_enabled"])
        for secret in ("UNTRUSTED-SECRET", "DO_NOT_SHOW_HTTP_REASON",
                       "https://indodax.com"):
            self.assertNotIn(secret, json.dumps(report))

    def test_missing_readonly_gate_never_constructs_client(self):
        def fail_factory(**kwargs):
            raise AssertionError("client must not be constructed")
        for field in self.off:
            env = self.off.copy()
            env.pop(field)
            report = collect_one_public_rest_edge_evidence(
                env=env, market_factory=fail_factory,
                now_fn=self.fixed_time,
            )
            self.assertEqual(report["status"], "DENIED_REQUIRED_READONLY_ENV")
            self.assertEqual(report["public_get_attempts"], 0)

    def test_response_without_cf_challenge_not_claimed_automatically(self):
        headers = Message()
        headers.add_header("Server", "cloudflare")
        headers.add_header("CF-Ray", "malicious token@secret")
        headers.add_header("Content-Type", "application/json")
        proof = _headers_evidence(
            HTTPError("https://indodax.com", 403, "Forbidden", headers, None)
        )
        self.assertFalse(proof["cf_challenge"])
        self.assertIsNone(proof["cf_ray"])
        self.assertEqual(proof["response_content_type"], "application/json")
        self.assertNotIn("token@secret", json.dumps(proof))

    def test_json_ok_does_not_claim_trusted_or_trade(self):
        calls = []
        class FakeMarket:
            public_get_attempt_count = 1
            def __init__(self, timeout):
                self.timeout = timeout
            def _get(self, path):
                calls.append(path)
                return {"server_time": 1234567}

        report = collect_one_public_rest_edge_evidence(
            env=self.off, market_factory=FakeMarket,
            now_fn=self.fixed_time
        )
        self.assertEqual(calls, ["/api/server_time"])
        self.assertEqual(report["status"], "PUBLIC_JSON_OK_UNATTESTED")
        self.assertEqual(report["orders_sent"], 0)
        self.assertEqual(report["private_api_requests"], 0)
        self.assertFalse(report["trading_enabled"])


if __name__ == "__main__":
    unittest.main()
