"""M04-V: durable per-Jakarta-day USD reserve across processes/restarts.

Uses synthetic in-memory transport, never connects to a paid model/exchange.
Owner must separately verify model prices and authorize payment; this suite
validates guard semantics, not billing accuracy or release safety.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
import unittest
from zoneinfo import ZoneInfo

from indodax_ai_bot.ai_budget import AiBudgetError, PersistentAiBudget
from indodax_ai_bot.sumopod_provider import SumopodChatAdapter, SumopodProviderError


ZONE = ZoneInfo("Asia/Jakarta")
MODELS = {"MARKET": "unit-market", "RISK": "unit-risk", "STRATEGY": "unit-strategy"}
KEY = "unit-not-real-sumopod-key"


def evidence(pair="btc_idr", digest="a" * 64):
    return {"source": {"pair": pair, "closed_1m_epoch": 1800000120},
            "evidence_digest_sha256": digest}


def fake_success(payload, _headers, _timeout):
    source = json.loads(payload["messages"][1]["content"])
    role = next(k for k, v in MODELS.items() if v == payload["model"])
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
        "role": role, "pair": source["source"]["pair"],
        "evidence_digest_sha256": source["evidence_digest_sha256"],
        "reason": "synthetic only",
        **({"bias": "MIXED"} if role == "MARKET" else
           {"decision": "NO_TRADE"} if role == "RISK" else
           {"action": "WAIT", "projected_net_profit_idr": "0",
            "projected_net_roi_percent": "0"}),
    })}}]}


class M04VPersistentBudgetTests(unittest.TestCase):
    def test_rejects_invalid_config_without_creating_ledger(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "b.sqlite3"
            with self.assertRaisesRegex(AiBudgetError, "INVALID_AI_BUDGET_LIMIT"):
                PersistentAiBudget(p, daily_limit_micro_usd=1,
                                   per_request_reservation_micro_usd=2)
            self.assertFalse(p.exists())

    def test_one_reservation_blocks_replay_across_new_ledger_instances(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "b.sqlite3"
            kw = dict(daily_limit_micro_usd=2000,
                      per_request_reservation_micro_usd=1000,
                      clock=lambda: datetime(2026, 10, 27, 23, tzinfo=ZONE))
            a = PersistentAiBudget(p, **kw)
            self.assertEqual(a.reserve("MARKET", "btc_idr", "a"*64), 1000)
            b = PersistentAiBudget(p, **kw)
            with self.assertRaisesRegex(AiBudgetError, "AI_BUDGET_SOURCE_ALREADY_RESERVED"):
                b.reserve("MARKET", "btc_idr", "a"*64)
            self.assertEqual(b.reserve("RISK", "btc_idr", "a"*64), 2000)
            with self.assertRaisesRegex(AiBudgetError, "AI_DAILY_COST_CAP_REACHED"):
                a.reserve("STRATEGY", "btc_idr", "a"*64)

    def test_local_day_reset_only_after_jakarta_midnight(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "b.sqlite3"
            state = [datetime(2026, 10, 27, 23, 59, tzinfo=ZONE)]
            a = PersistentAiBudget(
                p, daily_limit_micro_usd=1000,
                per_request_reservation_micro_usd=1000,
                clock=lambda: state[0],
            )
            a.reserve("MARKET", "btc_idr", "a"*64)
            state[0] = datetime(2026, 10, 28, 0, 0, tzinfo=ZONE)
            self.assertEqual(a.reserve("MARKET", "btc_idr", "a"*64), 1000)

    def test_paid_constructor_without_durable_budget_blocks_all_transport(self):
        sender = Mock(side_effect=fake_success)
        a = SumopodChatAdapter(
            api_key=KEY, models=MODELS, allow_paid_requests=True, transport=sender,
        )
        with self.assertRaisesRegex(SumopodProviderError, "SUMOPOD_PAID_BUDGET_NOT_CONFIGURED"):
            a("MARKET", evidence())
        sender.assert_not_called()

    def test_budget_is_reserved_before_payment_attempt_and_blocks_second(self):
        with TemporaryDirectory() as td:
            budget = PersistentAiBudget(
                Path(td) / "b.sqlite3", daily_limit_micro_usd=1000,
                per_request_reservation_micro_usd=1000,
            )
            sender = Mock(side_effect=fake_success)
            one = SumopodChatAdapter(
                api_key=KEY, models=MODELS, allow_paid_requests=True,
                transport=sender, persistent_budget=budget,
            )
            self.assertEqual(one("MARKET", evidence())["role"], "MARKET")
            two = SumopodChatAdapter(
                api_key=KEY, models=MODELS, allow_paid_requests=True,
                transport=sender, persistent_budget=budget,
            )
            with self.assertRaisesRegex(SumopodProviderError, "AI_DAILY_COST_CAP_REACHED"):
                two("RISK", evidence())
            self.assertEqual(sender.call_count, 1)

    def test_failed_transport_still_consumes_whole_reservation(self):
        with TemporaryDirectory() as td:
            budget = PersistentAiBudget(
                Path(td) / "b.sqlite3", daily_limit_micro_usd=1000,
                per_request_reservation_micro_usd=1000,
            )
            sender = Mock(side_effect=RuntimeError("not-a-key-for-display"))
            one = SumopodChatAdapter(
                api_key=KEY, models=MODELS, allow_paid_requests=True,
                transport=sender, persistent_budget=budget,
            )
            with self.assertRaisesRegex(SumopodProviderError, "SUMOPOD_TRANSPORT_UNAVAILABLE"):
                one("MARKET", evidence())
            with self.assertRaisesRegex(SumopodProviderError, "AI_BUDGET_SOURCE_ALREADY_RESERVED"):
                two = SumopodChatAdapter(
                    api_key=KEY, models=MODELS, allow_paid_requests=True,
                    transport=sender, persistent_budget=budget,
                )
                two("MARKET", evidence())
            sender.assert_called_once()

    def test_concurrent_workers_cannot_both_spend_same_last_slot(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "b.sqlite3"
            def worker(role):
                b = PersistentAiBudget(
                    p, daily_limit_micro_usd=1000,
                    per_request_reservation_micro_usd=1000,
                )
                try:
                    b.reserve(role, "btc_idr", "a"*64)
                    return "OK"
                except AiBudgetError as exc:
                    return str(exc)
            with ThreadPoolExecutor(max_workers=2) as pool:
                got = list(pool.map(worker, ["MARKET", "RISK"]))
            self.assertCountEqual(got, ["OK", "AI_DAILY_COST_CAP_REACHED"])


if __name__ == "__main__":
    unittest.main()
