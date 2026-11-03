"""M04-B Sumopod adapter: mock transport only, no paid calls or orders."""
from __future__ import annotations
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.sumopod_provider import ROLES, SumopodChatAdapter, SumopodProviderError, URL
from indodax_ai_bot.three_role_ai import ThreeRoleShadow
from test_m03g_bulk import FakeBulkMarket, fake_screen
from test_m04a_three_role_shadow import RoleCaller, signed_role_run

KEY = "sk-unit-test-key"
MODELS = {"MARKET": "market-example", "RISK": "risk-example", "STRATEGY": "strategy-example"}
DIGEST = "a" * 64

def evidence():
    return {"source": {"trace_id": "t", "pair": "btc_idr", "closed_1m_epoch": 1800000120},
            "evidence_digest_sha256": DIGEST, "prior_role_results": [], "role_order": list(ROLES)}

def result(role="MARKET", digest=DIGEST, finish_reason="stop", content=None):
    if content is None:
        output = {"role": role, "pair": "btc_idr",
                  "evidence_digest_sha256": digest, "reason": "unit test answer"}
        if role == "MARKET": output["bias"] = "MIXED"
        elif role == "RISK": output["decision"] = "NO_TRADE"
        else:
            output.update({"action": "WAIT", "projected_net_profit_idr": "0",
                           "projected_net_roi_percent": "0"})
        content = json.dumps(output)
    return {"choices": [{"finish_reason": finish_reason,
                         "message": {"content": content}}],
            "usage": {"prompt_tokens": 90, "completion_tokens": 45}}

class M04BSumopodTests(unittest.TestCase):
    def test_official_fixed_endpoint_and_three_models_with_usage(self):
        self.assertEqual(URL, "https://ai.sumopod.com/v1/chat/completions")
        seen=[]
        def fake(body,headers,timeout):
            seen.append((body,headers,timeout))
            role=next(r for r in ROLES if MODELS[r] == body["model"])
            return result(role)
        adapter=SumopodChatAdapter(api_key=KEY,models=MODELS,transport=fake,timeout_seconds=5)
        for role in ROLES:
            self.assertEqual(adapter(role,evidence())["role"],role)
        self.assertEqual([x[0]["model"] for x in seen],list(MODELS.values()))
        self.assertEqual(len(adapter.usage_events),3)
        for body,headers,timeout in seen:
            self.assertEqual(body["stream"],False)
            self.assertEqual(body["max_tokens"],650)
            self.assertEqual(json.loads(body["messages"][1]["content"]),evidence())
            self.assertEqual(headers["Authorization"],"Bearer "+KEY)
            self.assertEqual(timeout,5)

    def test_off_by_default_even_when_key_and_models_exist(self):
        adapter=SumopodChatAdapter(api_key=KEY,models=MODELS)
        with patch("indodax_ai_bot.sumopod_provider.urlopen") as send:
            with self.assertRaisesRegex(SumopodProviderError,"SUMOPOD_PAID_NETWORK_DISABLED"):
                adapter("MARKET",evidence())
            send.assert_not_called()
        self.assertNotIn(KEY,repr(adapter))
        self.assertEqual(adapter.usage_events,[])

    def test_env_configuration_never_enables_paid_network(self):
        env={"SUMOPOD_API_KEY":KEY}
        env.update({f"SUMOPOD_MODEL_{r}":m for r,m in MODELS.items()})
        adapter=SumopodChatAdapter.configured_from_env(env)
        with self.assertRaisesRegex(SumopodProviderError,"SUMOPOD_PAID_NETWORK_DISABLED"):
            adapter("RISK",evidence())
        with self.assertRaises(SumopodProviderError):
            SumopodChatAdapter.configured_from_env({"SUMOPOD_API_KEY":KEY})

    def test_missing_role_invalid_model_and_key_fail_closed(self):
        with self.assertRaisesRegex(SumopodProviderError,"THREE_ROLE_MODELS"):
            SumopodChatAdapter(api_key=KEY,models={"MARKET":"x"})
        with self.assertRaisesRegex(SumopodProviderError,"MODEL_ID"):
            SumopodChatAdapter(api_key=KEY,models={**MODELS,"RISK":"bad id"})
        with self.assertRaisesRegex(SumopodProviderError,"API_KEY"):
            SumopodChatAdapter(api_key="invalid key\n",models=MODELS)

    def test_invalid_pair_digest_and_unknown_role_skip_transport(self):
        send=Mock()
        adapter=SumopodChatAdapter(api_key=KEY,models=MODELS,transport=send)
        bads=[("FINAL",evidence()),("MARKET",{"source":{},"evidence_digest_sha256":DIGEST}),
              ("MARKET",{"source":{"pair":"btc_idr"},"evidence_digest_sha256":"x"})]
        for role,inp in bads:
            with self.assertRaises(SumopodProviderError):
                adapter(role,inp)
        send.assert_not_called()

    def test_wrong_digest_or_role_response_fail_closed(self):
        adapter=SumopodChatAdapter(api_key=KEY,models=MODELS,transport=lambda *_:result(digest="b"*64))
        with self.assertRaisesRegex(SumopodProviderError,"SOURCE_MISMATCH"):
            adapter("MARKET",evidence())
        self.assertEqual(adapter.usage_events,[])

    def test_partial_and_refused_completions_rejected(self):
        for status in ("length","content_filter",None):
            adapter=SumopodChatAdapter(api_key=KEY,models=MODELS,
                                       transport=lambda *_:result(finish_reason=status))
            with self.assertRaisesRegex(SumopodProviderError,"INCOMPLETE_OR_REFUSED"):
                adapter("MARKET",evidence())

    def test_invalid_json_duplicate_keys_and_nan_rejected(self):
        for body in ('{"role":"MARKET","role":"RISK"}',
                     'not-json {"role":"MARKET"}',
                     '{"role":"MARKET","reason": NaN}'):
            adapter=SumopodChatAdapter(api_key=KEY,models=MODELS,
                                       transport=lambda *_:result(content=body))
            with self.assertRaises(SumopodProviderError):
                adapter("MARKET",evidence())

    def test_secret_in_transport_error_is_not_exposed(self):
        def bad(*_): raise RuntimeError("do-not-leak:"+KEY)
        adapter=SumopodChatAdapter(api_key=KEY,models=MODELS,transport=bad)
        with self.assertRaises(SumopodProviderError) as err:
            adapter("MARKET",evidence())
        self.assertEqual(str(err.exception),"SUMOPOD_TRANSPORT_UNAVAILABLE")
        self.assertNotIn(KEY,str(err.exception))

    def test_roles_share_evidence_but_strategy_receives_prior_risk_veto_context(self):
        caller=RoleCaller()
        out=signed_role_run(caller)
        self.assertEqual(caller.calls,list(ROLES))
        self.assertEqual([len(x["prior_role_results"]) for x in caller.inputs],[0,1,2])
        self.assertEqual(caller.inputs[2]["prior_role_results"][1]["decision"],"ALLOW")
        self.assertEqual({x["evidence_digest_sha256"] for x in caller.inputs},
                         {out["source_digest_sha256"]})
        self.assertEqual(len({json.dumps(x["market_evidence"],sort_keys=True)
                              for x in caller.inputs}),1)
        self.assertFalse(out["execution_allowed"])

    def test_public_orchestrator_with_injected_adapter_never_calls_models(self):
        send=Mock(return_value=result())
        adapter=SumopodChatAdapter(api_key=KEY,models=MODELS,transport=send)
        with TemporaryDirectory() as td:
            app=DailyScalpingOrchestrator(
                Settings.from_env({}),DecisionJournal(Path(td)/"journal.sqlite3"),
                ai_role_caller=adapter)
            with patch("indodax_ai_bot.orchestrator.DailyPairScanner") as scanner:
                scanner.return_value.scan.return_value=fake_screen()
                out=app.run_bulk_deep_cycle("probe",market=FakeBulkMarket())
        self.assertIsNone(out.final_decision)
        self.assertEqual(out.ai_shadow["completed_roles"],[])
        self.assertFalse(out.execution_allowed)
        send.assert_not_called()
        self.assertEqual(adapter.usage_events,[])

if __name__ == "__main__":
    unittest.main()
