"""M11-04 original M07 one-cycle source-bound account and loss observation.

Real CLI M07 M03+M06 join is exercised with only synthetic patched
market/2GET responses. No API, credentials, paid AI, Telegram, M07 order,
new DB, synthetic M08 loss authentication, or LIVE authority.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.deep_analysis import DeepCandidate
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import CycleResult, DailyScalpingOrchestrator
from tests.test_m03_public_quote_friction_mainpath import public_observation
from tests.test_m07_main_idr_sourcebound_sizing_original import (
    NOW, RULES, account as m06_account,
)

FUNDING = {
    "requested_ticket_ceiling_idr": Decimal("100000"),
    "new_floki_sell_rebuy_hold_idr": Decimal("0"),
    "new_pepe_sell_rebuy_hold_idr": Decimal("0"),
    "commitments_not_locked_idr": Decimal("0"),
    "max_spread_bps": Decimal("150"),
}


def observed_m06():
    return {"source_currency": "IDR", **m06_account()}


def report_for(row, *, aligned=True):
    return CycleResult("one", None, "PUBLIC_UNVERIFIED", bulk={
        "mode": "M03G_BULK_PUBLIC_SHARED_SOURCE_ONLY",
        "shared_public_scan_context": True,
        "eligible_cohort": {
            "status": "ALIGNED_PUBLIC_UNATTESTED" if aligned else "UNALIGNED",
            "eligible_pair_ids": ["btc_idr"],
        },
        "provisional_market_evidence": [row],
        "execution_allowed": False,
    })


class OriginalM11M07SourceBoundTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix="m11-m07-viewonly-", dir="/tmp")
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.db = self.dir / "original-v2.sqlite3"
        self.bot = DailyScalpingOrchestrator(Settings(), DecisionJournal(self.db))
        obs = replace(public_observation(), pair_rule_fields=dict(RULES))
        with patch.object(self.bot.journal, "record_and_compare_public_wall",
                          return_value={"status": "UNATTESTED"}):
            self.row = self.bot._attach_public_wall_experience(
                DeepCandidate("btc_idr", 75, "PUBLIC_REVIEW", None), obs,
            ).summary()
        self.funding_file = self.dir / "funding.json"
        self.funding_file.write_text(json.dumps({k: str(v) for k, v in FUNDING.items()}))
        self.funding_file.chmod(0o600)
        self.ws = self.dir / "public_ws_btc.sqlite3"
        self.ws.write_bytes(b"synthetic-only")
        self.env = {
            "INDODAX_TRADING_ENABLED": "NO",
            "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
            "INDODAX_EXECUTION_MODE": "DRY_RUN",
        }

    def invoke(self, *, acc=None, bulk=None, extra=(), env=None, reference=True):
        if acc is None:
            acc = observed_m06()
        if bulk is None:
            bulk = report_for(self.row)
        args = [
            "--m07-main-readonly-cycle-once",
            "--m07-runtime-funding-file", str(self.funding_file),
            "--ws-db", str(self.ws),
            "--db", str(self.db),
            *extra,
        ]
        if reference:
            args.append("--m07-readonly-m11-source-observation")
        out, err = StringIO(), StringIO()
        flags = {**self.env, **(env or {})}
        with (patch.dict(os.environ, flags, clear=True),
              patch("indodax_ai_bot.orchestrator.time.time", return_value=NOW),
              patch("indodax_ai_bot.__main__._existing_v2_credentials",
                    return_value=self.funding_file),
              patch("indodax_ai_bot.__main__.read_view_only_credentials",
                    return_value=("dummy-viewonly", "dummy-secret")),
              patch.object(DailyScalpingOrchestrator, "run_bulk_deep_cycle",
                           return_value=bulk) as scan,
              patch.object(DailyScalpingOrchestrator,
                           "inspect_m06_idr_budget_readonly_once",
                           return_value=acc) as view,
              redirect_stdout(out), redirect_stderr(err)):
            rc = main(args)
        return rc, out.getvalue(), err.getvalue(), scan, view

    def assert_blocked_observation(self, o):
        self.assertEqual(o["status"], "REJECT_NONLIVE_UNVERIFIED_RISK_RELEASE")
        self.assertFalse(o["m06_full_account_funding_authenticated"])
        self.assertFalse(o["account_exposure_idr_verified"])
        self.assertFalse(o["all_peer_liabilities_verified"])
        self.assertFalse(o["order_slots_completely_reconciled"])
        self.assertFalse(o["risk_owner_identity_authenticated"])
        self.assertFalse(o["m11_owner_risk_limits_authenticated"])
        self.assertFalse(o["m11_operational_halt_release_verified"])
        self.assertFalse(o["m11_limit_scenario_evaluated"])
        self.assertFalse(o["m08_bot_owned_settlement_verified"])
        self.assertFalse(o["m08_realized_net_pnl_wib_verified"])
        self.assertIsNone(o["realized_daily_net_loss_idr_verified"])
        self.assertIsNone(o["realized_pnl_day_wib_verified"])
        self.assertFalse(o["g1_independent_issuer_verified"])
        self.assertIsNone(o["owner_final_decision"])
        self.assertEqual(o["private_gets_added_by_m11"], 0)
        self.assertFalse(o["m07_order_intent_created_by_m11"])
        self.assertFalse(o["order_sent"])
        self.assertFalse(o["execution_allowed"])
        self.assertFalse(o["m11_live_release_authorized"])

    def test_original_cli_real_caller_one_scanner_two_viewonly_gets_observed(self):
        rc, out, err, scanner, view = self.invoke()
        self.assertEqual((rc, err), (0, ""))
        scanner.assert_called_once()
        view.assert_called_once()
        data = json.loads(out)
        self.assertIsNone(data["final_decision"])
        self.assertFalse(data["execution_allowed"])
        report = data["m07_original_live_source_join"]
        self.assertEqual(report["m06_authenticated_viewonly_get_count"], 2)
        self.assertFalse(report["execution_allowed"])
        o = report["m11_sourcebound_risk_observation"]
        self.assertEqual(o["m06_viewonly_status"],
                         "M06_TWO_SIGNED_GETS_REPORTED_RECENT_NOT_FULLY_ATTESTED")
        self.assertTrue(o["m06_transport_observed"])
        self.assertEqual(o["m06_balance_and_open_orders_observation"], {
            "m06_free_idr_observed": "920147",
            "m06_locked_idr_observed": "0",
            "m06_all_pair_open_order_count_observed": 0,
            "m06_viewonly_observed_epoch": NOW - 5,
            "m06_snapshot_age_seconds": 5,
        })
        self.assertEqual(o["m08_original_journal_source_status"],
                         "M08_NO_AUTHENTICATED_BOT_OWNED_SETTLEMENT_ISSUER")
        self.assert_blocked_observation(o)

    def test_original_cli_no_flag_same_existing_source_join_output(self):
        rc, out, err, scanner, view = self.invoke(reference=False)
        self.assertEqual((rc, err), (0, ""))
        scanner.assert_called_once()
        view.assert_called_once()
        self.assertNotIn("m11_sourcebound_risk_observation",
                         json.loads(out)["m07_original_live_source_join"])

    def test_no_public_candidate_means_no_private_get_no_balance(self):
        rc, out, err, scanner, view = self.invoke(
            bulk=report_for(self.row, aligned=False))
        self.assertEqual((rc, err), (3, ""))
        scanner.assert_called_once()
        view.assert_not_called()
        o = json.loads(out)["m07_original_live_source_join"][
            "m11_sourcebound_risk_observation"]
        self.assertFalse(o["m06_transport_observed"])
        self.assertIsNone(o["m06_balance_and_open_orders_observation"])
        self.assert_blocked_observation(o)

    def test_all_pair_nonzero_orders_are_observed_but_never_release(self):
        rc, out, err, _, view = self.invoke(
            acc={**observed_m06(), "all_pair_open_orders_observed": 2})
        self.assertEqual((rc, err), (0, ""))
        view.assert_called_once()
        o = json.loads(out)["m07_original_live_source_join"][
            "m11_sourcebound_risk_observation"]
        self.assertEqual(o["m06_balance_and_open_orders_observation"][
            "m06_all_pair_open_order_count_observed"], 2)
        self.assert_blocked_observation(o)

    def test_stale_account_is_not_presented_as_recent(self):
        o = self.bot.observe_m11_m07_sourcebound_account_and_loss_readonly_once(
            account={**observed_m06(), "account_observed_epoch": NOW - 120},
            now_epoch=NOW)
        self.assertIsNone(o["m06_balance_and_open_orders_observation"])
        self.assertEqual(o["m06_viewonly_status"], "UNKNOWN_NO_VALID_RECENT_M06_2GET")
        self.assert_blocked_observation(o)

    def test_incomplete_gets_or_fake_full_authentication_discard_balance(self):
        for changes in (
            {"requests_completed": 1},
            {"requests_completed": True},
            {"view_only_permissions_verified": False},
            {"source_authenticated": True},
            {"execution_allowed": True},
            {"order_sent": True},
            {"source_currency": "USDT"},
            {"status": "UNTRUSTED"},
            {"account_observed_epoch": NOW + 1},
        ):
            with self.subTest(changes=changes):
                o = self.bot.observe_m11_m07_sourcebound_account_and_loss_readonly_once(
                    account={**observed_m06(), **changes}, now_epoch=NOW)
                self.assertIsNone(o["m06_balance_and_open_orders_observation"])
                self.assert_blocked_observation(o)

    def test_nan_negative_infinite_invalid_count_never_exposed(self):
        for changes in (
            {"exchange_idr_free_observed": "NaN"},
            {"exchange_idr_locked_observed": "-1"},
            {"exchange_idr_free_observed": "Infinity"},
            {"exchange_idr_locked_observed": "0" * 60},
            {"all_pair_open_orders_observed": -1},
            {"all_pair_open_orders_observed": False},
            {"all_pair_open_orders_observed": 1000},
            {"exchange_idr_free_observed": None},
        ):
            with self.subTest(changes=changes):
                o = self.bot.observe_m11_m07_sourcebound_account_and_loss_readonly_once(
                    account={**observed_m06(), **changes}, now_epoch=NOW)
                self.assertIsNone(o["m06_balance_and_open_orders_observation"])
                self.assert_blocked_observation(o)

    def test_missing_source_and_non_dict_rejected(self):
        for obj in (None, {}, [], "not-an-account"):
            with self.subTest(obj=obj):
                o = self.bot.observe_m11_m07_sourcebound_account_and_loss_readonly_once(
                    account=obj, now_epoch=NOW)
                self.assertIsNone(o["m06_balance_and_open_orders_observation"])
                self.assert_blocked_observation(o)

    def test_trust_flags_never_allow_funds_or_capital(self):
        o = self.bot.observe_m11_m07_sourcebound_account_and_loss_readonly_once(
            account={**observed_m06(),
                     "all_peer_liabilities_verified": True,
                     "spendable_idr_verified": True,
                     "local_special_ledger_uid_bound": True},
            now_epoch=NOW)
        self.assertTrue(o["m06_transport_observed"])
        self.assertFalse(o["m06_full_account_funding_authenticated"])
        self.assert_blocked_observation(o)

    def test_m08_future_rows_never_count_as_settled_realized_loss(self):
        original = self.bot.journal.review_m09_original_m08_position_and_idr_readonly()
        with patch.object(
            self.bot.journal, "review_m09_original_m08_position_and_idr_readonly",
            return_value={**original, "future_event_db_rows_untrusted": 9},
        ) as journal:
            o = self.bot.observe_m11_m07_sourcebound_account_and_loss_readonly_once(
                account=observed_m06(), now_epoch=NOW)
        journal.assert_called_once()
        self.assertEqual(o["m08_future_event_rows_untrusted"], 9)
        self.assertIsNone(o["realized_daily_net_loss_idr_verified"])
        self.assert_blocked_observation(o)

    def test_fake_m08_issuer_claim_denied_not_accepted(self):
        original = self.bot.journal.review_m09_original_m08_position_and_idr_readonly()
        for changes in (
            {"wallet_settled_idr_verified": True},
            {"realized_net_pnl_wib_verified": True},
            {"status": "EXCHANGE_SETTLED"},
            {"execution_allowed": True},
        ):
            with self.subTest(changes=changes):
                with patch.object(
                    self.bot.journal, "review_m09_original_m08_position_and_idr_readonly",
                    return_value={**original, **changes},
                ):
                    with self.assertRaisesRegex(
                        ValueError, "M11_M07_UNVERIFIED_M08_ISSUER"):
                        self.bot.observe_m11_m07_sourcebound_account_and_loss_readonly_once(
                            account=observed_m06(), now_epoch=NOW)

    def test_source_observation_does_not_modify_existing_journal_bytes(self):
        before = sha256(self.db.read_bytes()).hexdigest()
        for _ in range(3):
            o = self.bot.observe_m11_m07_sourcebound_account_and_loss_readonly_once(
                account=observed_m06(), now_epoch=NOW)
            self.assert_blocked_observation(o)
            self.assertEqual(sha256(self.db.read_bytes()).hexdigest(), before)

    def test_owner_entered_offline_policy_not_live_release(self):
        self.bot.journal.record_m11_offline_risk_limits_once(
            owner_user_id=1, policy_id="a"*24,
            capital_ceiling_idr=Decimal("100000"),
            max_open_orders=1, daily_loss_ceiling_idr=Decimal("5000"),
            recorded_epoch=NOW,
        )
        o = self.bot.observe_m11_m07_sourcebound_account_and_loss_readonly_once(
            account=observed_m06(), now_epoch=NOW)
        self.assertFalse(o["m11_owner_risk_limits_authenticated"])
        self.assert_blocked_observation(o)

    def test_wrong_cli_action_flag_denied_before_get(self):
        with patch.dict(os.environ, self.env, clear=True):
            with redirect_stderr(StringIO()) as err:
                rc = main(["--health", "--m07-readonly-m11-source-observation"])
        self.assertEqual(rc, 2)
        self.assertIn("M07_RUNTIME_M11_OBSERVATION_REQUIRES_ONE_CYCLE",
                      err.getvalue())

    def test_live_mode_denied_before_account_or_journal_access(self):
        for env in (
            {"INDODAX_TRADING_ENABLED": "YES"},
            {"INDODAX_ALLOW_LIVE_EXECUTION": "YES"},
            {"INDODAX_EXECUTION_MODE": "LIVE"},
        ):
            with self.subTest(env=env):
                with patch.dict(os.environ, {**self.env, **env}, clear=True):
                    with redirect_stderr(StringIO()) as err:
                        rc = main([
                            "--m07-main-readonly-cycle-once",
                            "--m07-readonly-m11-source-observation",
                        ])
                self.assertEqual(rc, 2)
                self.assertIn("M01_LIVE_NOT_IMPLEMENTED", err.getvalue())

    def test_no_network_telegram_paid_provider_or_signed_order(self):
        with (patch("indodax_ai_bot.__main__.TelegramBotApiClient") as telegram,
              patch("indodax_ai_bot.__main__.SumopodChatAdapter") as ai,
              patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket") as market,
              patch("indodax_ai_bot.orchestrator.OfficialV2OrderAdapter") as adapter):
            rc, out, err, scan, get = self.invoke()
        self.assertEqual((rc, err), (0, ""))
        scan.assert_called_once()
        get.assert_called_once()
        for mock in (telegram, ai, market, adapter):
            mock.assert_not_called()
        self.assert_blocked_observation(json.loads(out)[
            "m07_original_live_source_join"]["m11_sourcebound_risk_observation"])

    def test_order_intents_not_created_by_new_readonly_review(self):
        with self.bot.journal._connect() as con:
            before = con.execute(
                "SELECT COUNT(*) FROM m07_offline_order_intents").fetchone()[0]
        self.bot.observe_m11_m07_sourcebound_account_and_loss_readonly_once(
            account=observed_m06(), now_epoch=NOW)
        with self.bot.journal._connect() as con:
            after = con.execute(
                "SELECT COUNT(*) FROM m07_offline_order_intents").fetchone()[0]
        self.assertEqual(before, after)

    def test_invalid_epoch_fails_closed(self):
        for t in (None, True, 0, -1, "1800000080"):
            with self.subTest(t=t):
                with self.assertRaisesRegex(ValueError, "M11_M07_OBSERVATION_EPOCH_INVALID"):
                    self.bot.observe_m11_m07_sourcebound_account_and_loss_readonly_once(
                        account=observed_m06(), now_epoch=t)


if __name__ == "__main__":
    unittest.main()
