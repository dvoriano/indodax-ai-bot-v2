"""M04-V: bounded no-retry/duplicate Sumopod calls (synthetic transport ONLY).

A local cap is NOT a persistent daily USD budget or authorization for paid
API calls. The real production CLI still forces network OFF.
"""
from __future__ import annotations

from unittest import TestCase
from unittest.mock import Mock, patch
import unittest

from indodax_ai_bot.sumopod_provider import (
    SumopodChatAdapter, SumopodProviderError,
)

KEY = "test-m04v-key-not-valid"
MODELS = {"MARKET": "test-market", "RISK": "test-risk", "STRATEGY": "test-strategy"}


def evidence(pair="btc_idr", digest="a" * 64):
    return {"source": {"pair": pair, "closed_1m_epoch": 1800000120},
            "evidence_digest_sha256": digest, "prior_role_results": []}


def reply(payload, _headers, _timeout):
    import json
    ev = json.loads(payload["messages"][1]["content"])
    role = next(k for k, v in MODELS.items() if v == payload["model"])
    data = {"role": role, "pair": ev["source"]["pair"],
            "evidence_digest_sha256": ev["evidence_digest_sha256"],
            "reason": "test transport only"}
    if role == "MARKET": data["bias"] = "MIXED"
    elif role == "RISK": data["decision"] = "NO_TRADE"
    else: data.update({"action": "WAIT", "projected_net_profit_idr": "0",
                       "projected_net_roi_percent": "0"})
    return {"choices": [{"finish_reason": "stop",
                         "message": {"content": json.dumps(data)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 30}}


class M04VCycleCallBudgetTests(TestCase):
    def test_two_candidate_six_roles_then_seventh_denied(self):
        send = Mock(side_effect=reply)
        a = SumopodChatAdapter(api_key=KEY, models=MODELS, transport=send)
        for pair, digest in (("btc_idr", "a"*64), ("eth_idr", "b"*64)):
            for role in MODELS:
                self.assertEqual(a(role, evidence(pair, digest))["role"], role)
        self.assertEqual(send.call_count, 6)
        self.assertEqual(len(a.usage_events), 6)
        with self.assertRaisesRegex(SumopodProviderError, "SUMOPOD_CYCLE_CALL_CAP_REACHED"):
            a("MARKET", evidence("sol_idr", "c"*64))
        self.assertEqual(send.call_count, 6)

    def test_duplicate_same_role_pair_and_candle_never_retries(self):
        send = Mock(side_effect=reply)
        a = SumopodChatAdapter(api_key=KEY, models=MODELS, transport=send)
        self.assertEqual(a("MARKET", evidence())["role"], "MARKET")
        with self.assertRaisesRegex(SumopodProviderError, "SUMOPOD_DUPLICATE_ROLE_SOURCE"):
            a("MARKET", evidence())
        self.assertEqual(send.call_count, 1)

    def test_failed_network_attempt_also_reserved_without_retry(self):
        send = Mock(side_effect=RuntimeError("no-secret-shall-leak"))
        a = SumopodChatAdapter(api_key=KEY, models=MODELS, transport=send)
        with self.assertRaisesRegex(SumopodProviderError, "SUMOPOD_TRANSPORT_UNAVAILABLE"):
            a("MARKET", evidence())
        with self.assertRaisesRegex(SumopodProviderError, "SUMOPOD_DUPLICATE_ROLE_SOURCE"):
            a("MARKET", evidence())
        self.assertEqual(send.call_count, 1)
        self.assertEqual(a.usage_events, [])

    def test_oversized_evidence_rejected_before_transport(self):
        send = Mock(side_effect=reply)
        a = SumopodChatAdapter(api_key=KEY, models=MODELS, transport=send,
                               max_input_bytes_per_call=2048)
        body = evidence()
        body["dummy"] = "x" * 4000
        with self.assertRaisesRegex(SumopodProviderError, "SUMOPOD_SOURCE_INPUT_TOO_LARGE"):
            a("RISK", body)
        send.assert_not_called()
        # Invalid input must not consume a call slot.
        self.assertEqual(a("RISK", evidence())["role"], "RISK")
        self.assertEqual(send.call_count, 1)

    def test_invalid_caps_rejected_and_real_network_off_by_default(self):
        for bad in (0, True, 7, "6"):
            with self.assertRaisesRegex(SumopodProviderError, "INVALID_SUMOPOD_CALL_CAP"):
                SumopodChatAdapter(api_key=KEY, models=MODELS,
                                   max_calls_per_instance=bad)
        with self.assertRaisesRegex(SumopodProviderError, "INVALID_SUMOPOD_INPUT_BYTES_CAP"):
            SumopodChatAdapter(api_key=KEY, models=MODELS,
                               max_input_bytes_per_call=999)
        a = SumopodChatAdapter(api_key=KEY, models=MODELS)
        with patch("indodax_ai_bot.sumopod_provider._post") as post:
            with self.assertRaisesRegex(SumopodProviderError, "SUMOPOD_PAID_NETWORK_DISABLED"):
                a("MARKET", evidence())
            post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
