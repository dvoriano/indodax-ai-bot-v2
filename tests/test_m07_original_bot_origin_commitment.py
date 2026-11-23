"""P1 M07 original V2 durable LOCAL origin, never authenticated submit.

Original CLI → DailyScalpingOrchestrator → OfficialV2OrderAdapter shape →
SAME original DecisionJournal. Offline private /tmp, NO/NO/DRY_RUN.
Local commitment is not bot-owned exchange fill provenance.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionConflictError, DecisionJournal
from indodax_ai_bot.m07_order_adapter import (
    M07Hardlocked, M07OrderDraft, OfficialV2OrderAdapter,
)
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from tests.test_m08_original_post_sell_rediscovery import (
    account_order, account_fills,
)

NOW = 1793695000
ACCOUNT = "a" * 64
OTHER = "b" * 64
CID = "v2localorigin001"


def draft(*, client=CID, symbol="INJIDR", price="25000", qty="4",
          side="BUY", kind="LIMIT"):
    return M07OrderDraft(symbol, side, kind, client,
                         price=Decimal(price), quantity=Decimal(qty))


def scenario(*, action="PREPARE", client=CID, symbol="INJIDR",
             account=ACCOUNT, uid=12345, price="25000", qty="4",
             epoch=NOW):
    return {
        "action": action, "account_uid": uid,
        "account_fingerprint": account, "symbol": symbol,
        "client_order_id": client, "price_idr": price,
        "quantity_base": qty, "observed_epoch": epoch,
    }


class OriginalM07OriginCommitmentTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix="m07-origin-p1-", dir="/tmp")
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.db = self.folder / "m11_offline_fixture.sqlite3"
        self.journal = DecisionJournal(self.db)
        self.db.chmod(0o600)
        self.file = self.folder / "origin.json"
        self.safe = {
            "INDODAX_TRADING_ENABLED": "NO",
            "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
            "INDODAX_EXECUTION_MODE": "DRY_RUN",
        }

    def cli(self, payload=None, *, path=None, db=None, env=None,
            argv=None, rewrite=True):
        if payload is None:
            payload = scenario()
        if path is None:
            path = self.file
        if path == self.file and rewrite:
            self.file.write_text(json.dumps(payload))
            self.file.chmod(0o600)
        if argv is None:
            argv = [
                "--m07-origin-commitment-offline-once",
                "--m07-origin-commitment-file", str(path),
                "--db", str(self.db if db is None else db),
            ]
        out, err = StringIO(), StringIO()
        with patch.dict(os.environ, {**self.safe, **(env or {})}, clear=True):
            with redirect_stdout(out), redirect_stderr(err):
                rc = main(argv)
        return rc, out.getvalue(), err.getvalue()

    def call(self, **kwargs):
        raw = self.cli(**kwargs)
        self.assertEqual((raw[0], raw[2]), (0, ""))
        result = json.loads(raw[1])
        self.assertIsNone(result["final_decision"])
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["live_trading_authorized"])
        return result["m07_local_origin_commitment"]

    def denied(self, **kwargs):
        rc, out, err = self.cli(**kwargs)
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("M07_ORIGIN_OFFLINE_SCOPE_OR_SOURCE_UNVERIFIED", err)

    def local_rows(self):
        with sqlite3.connect(self.db) as con:
            return con.execute(
                "SELECT client_order_id,account_uid,account_fingerprint,"
                "symbol,side,order_type,price_idr,quantity_base,"
                "request_sha256,created_epoch,last_epoch,state,provenance,"
                "execution_allowed FROM m07_prepared_origin_no_submit "
                "ORDER BY client_order_id"
            ).fetchall()

    def assert_untrusted(self, r):
        self.assertFalse(r["bot_exchange_submit_receipt_verified"])
        self.assertFalse(r["order_sent"])
        self.assertFalse(r["execution_allowed"])
        self.assertFalse(r["live_trading_authorized"])
        self.assertNotIn("owner_final_decision", r) if r["stage"].endswith(
            "COMMITMENT") else self.assertIsNone(r["owner_final_decision"])

    def test_original_cli_prepare_persists_immutable_draft_only(self):
        r = self.call()
        self.assertEqual(r["status"], "OFFLINE_PREPARED_NO_SUBMIT")
        self.assertFalse(r["replay"])
        self.assertTrue(r["local_draft_immutable"])
        self.assertTrue(r["exchange_order_state_unknown_not_retryable"])
        self.assertFalse(r["account_uid_authenticated"])
        self.assertFalse(r["m08_position_admitted"])
        self.assert_untrusted(r)
        rows = self.local_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0:8],
                         (CID,12345,ACCOUNT,"INJIDR","BUY","LIMIT",
                          "25000","4"))
        self.assertEqual(rows[0][11:],
                         ("OFFLINE_PREPARED_NO_SUBMIT",
                          "LOCAL_DRAFT_NO_EXCHANGE_SUBMIT",0))
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM m07_offline_order_intents"
            ).fetchone()[0], 0)
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM m08_position_settled_idr_events"
            ).fetchone()[0], 0)

    def test_identical_replay_is_inert_and_request_hash_stable(self):
        a = self.call()
        before = self.local_rows()
        b = self.call()
        self.assertTrue(b["replay"])
        self.assertEqual(a["request_draft_sha256"], b["request_draft_sha256"])
        self.assertEqual(self.local_rows(), before)

    def test_conflicting_same_client_id_draft_price_qty_uid_account_denied(self):
        self.call()
        for changed in (
            {"price": "26000"}, {"qty": "5"}, {"uid": 22},
            {"account": OTHER}, {"symbol": "BTCIDR"},
        ):
            with self.subTest(changed=changed):
                self.denied(payload=scenario(**changed))
        self.assertEqual(len(self.local_rows()), 1)

    def test_same_pair_new_client_id_blocked_before_any_order(self):
        self.call()
        self.denied(payload=scenario(client="another_inj_buy"))
        self.assertEqual(len(self.local_rows()), 1)

    def test_two_separate_pair_holds_max_then_third_blocked(self):
        self.call()
        self.call(payload=scenario(symbol="BTCIDR",client="another_btc_buy"))
        self.denied(payload=scenario(symbol="XRPIDR", client="third_xrp_buy"))
        self.assertEqual(len(self.local_rows()), 2)

    def test_uncertain_hold_requires_prepared_and_never_retries(self):
        self.denied(payload=scenario(action="UNCERTAIN"))
        self.call()
        r = self.call(payload=scenario(action="UNCERTAIN",epoch=NOW+1))
        self.assertEqual(r["status"], "OFFLINE_UNCERTAIN_HOLD_NO_RETRY")
        self.assertFalse(r["retry_authorized"])
        self.assertFalse(r["exchange_submit_attempt_verified"])
        self.assertFalse(r["release_authorized"])
        self.assert_untrusted(r)
        again = self.call(payload=scenario(action="UNCERTAIN",epoch=NOW+1))
        self.assertTrue(again["replay"])
        self.denied(payload=scenario(action="UNCERTAIN",epoch=NOW))
        self.assertEqual(self.local_rows()[0][11],
                         "OFFLINE_UNCERTAIN_HOLD_NO_RETRY")

    def test_uncertain_other_pair_blocks_new_origin_and_old_intent(self):
        self.call()
        self.call(payload=scenario(action="UNCERTAIN",epoch=NOW+1))
        self.denied(payload=scenario(symbol="BTCIDR",client="another_buy"))
        with self.assertRaisesRegex(
            DecisionConflictError, "M07_ORIGIN_UNCERTAIN_BLOCKS_BUY"
        ):
            self.journal.reserve_m07_offline_order_intent(
                intent_id="offlinebtc001", draft=M07OrderDraft(
                    "BTCIDR","BUY","MARKET","offlinebtc001",
                    quote_order_qty=10000),
                recorded_epoch=NOW+1,
            )

    def test_original_legacy_offline_intent_pair_collision_blocks_new_origin(self):
        self.journal.reserve_m07_offline_order_intent(
            intent_id="old_btc", draft=M07OrderDraft(
                "BTCIDR","BUY","MARKET","old_btc",
                quote_order_qty=10000,
            ), recorded_epoch=NOW)
        self.denied(payload=scenario(symbol="BTCIDR",client="new_btc"))
        self.denied(payload=scenario(symbol="INJIDR",client="old_btc"))

    def test_existing_synthetic_unknown_blocks_origin_admission(self):
        self.journal.reserve_m07_offline_order_intent(
            intent_id="old_btc", draft=M07OrderDraft(
                "BTCIDR","BUY","MARKET","old_btc",
                quote_order_qty=10000,
            ), recorded_epoch=NOW)
        self.journal.mark_m07_offline_unknown_hold(
            intent_id="old_btc", observed_epoch=NOW+1)
        self.denied()
        self.assertEqual(self.local_rows(), [])

    def test_original_m07_intent_cannot_collide_with_new_origin(self):
        self.call()
        with self.assertRaisesRegex(
            DecisionConflictError, "M07_ORIGIN_CLIENT_ID_IN_USE"
        ):
            self.journal.reserve_m07_offline_order_intent(
                intent_id=CID,draft=M07OrderDraft(
                    "BTCIDR","BUY","MARKET",CID,
                    quote_order_qty=10000),
                recorded_epoch=NOW,
            )

    def test_original_m07_guard_reports_origin_not_authenticated(self):
        self.call()
        g = self.journal.m07_offline_intent_summary()
        self.assertEqual(g["local_origin_prepared_no_submit_count"], 1)
        self.assertEqual(g["local_origin_uncertain_no_retry_count"], 0)
        self.assertTrue(g["local_origin_does_not_prove_order_issuer"])
        self.assertFalse(g["exchange_order_state_authenticated"])
        self.assertFalse(g["execution_allowed"])
        self.call(payload=scenario(action="UNCERTAIN",epoch=NOW+1))
        g = self.journal.m07_offline_intent_summary()
        self.assertEqual(g["local_origin_uncertain_no_retry_count"], 1)
        self.assertTrue(g["additional_buy_reservations_blocked"])

    def test_review_before_prepare_returns_block_and_no_new_db_row(self):
        r = self.call(payload=scenario(action="REVIEW"))
        self.assertEqual(r["status"],
                         "M07_ORIGIN_BLOCKED_NO_AUTHENTICATED_SUBMIT_ISSUER")
        self.assertIsNone(r["origin_local_state"])
        self.assertFalse(r["original_local_draft_present"])
        self.assertFalse(r["account_only_buy_order_correlated_not_owned"])
        self.assertFalse(r["m08_position_manager_admission"])
        self.assert_untrusted(r)
        self.assertEqual(self.local_rows(), [])

    def test_viewonly_account_order_and_buy_fills_not_bot_origin(self):
        self.call()
        order=account_order(side="BUY",symbol="INJIDR",
                            client_id=CID,qty="4")
        self.journal.record_m07_authenticated_readonly_order_status(
            account_fingerprint=ACCOUNT, order=order,
            observed_ms=NOW*1000-1000,
        )
        self.journal.record_m07_exchange_readonly_fills(
            account_fingerprint=ACCOUNT, fills=account_fills(order),
            observed_ms=NOW*1000-500,
        )
        before=sha256(self.db.read_bytes()).hexdigest()
        r = self.call(payload=scenario(action="REVIEW", epoch=NOW+2))
        self.assertTrue(r["original_local_draft_present"])
        self.assertTrue(r["account_only_buy_order_correlated_not_owned"])
        self.assertTrue(r["account_only_buy_fill_correlated_not_owned"])
        self.assertFalse(r["account_uid_authenticated"])
        self.assertFalse(r["verified_bot_owned_lots"])
        self.assertIsNone(r["verified_bot_owned_base_qty"])
        self.assertIsNone(r["m07_sell_quantity_authorized"])
        self.assertFalse(r["m08_06_settlement_issuer_allowed"])
        self.assertFalse(r["m08_position_manager_admission"])
        self.assertFalse(r["retry_authorized"])
        self.assertFalse(r["release_authorized"])
        self.assert_untrusted(r)
        self.assertEqual(sha256(self.db.read_bytes()).hexdigest(),before)
        self.assertEqual(self.local_rows()[0][11],
                         "OFFLINE_PREPARED_NO_SUBMIT")

    def test_forged_foreign_account_status_does_not_correlate(self):
        self.call()
        order=account_order(side="BUY",symbol="INJIDR",
                            client_id=CID,qty="4")
        self.journal.record_m07_authenticated_readonly_order_status(
            account_fingerprint=OTHER,order=order,
            observed_ms=NOW*1000-1000,
        )
        r=self.call(payload=scenario(action="REVIEW"))
        self.assertFalse(r["account_only_buy_order_correlated_not_owned"])
        self.assertFalse(r["account_only_buy_fill_correlated_not_owned"])
        self.assert_untrusted(r)

    def test_partial_order_status_never_promoted(self):
        self.call()
        order=account_order(side="BUY",status="PARTIALLY_FILLED",
                            symbol="INJIDR",client_id=CID,qty="4")
        self.journal.record_m07_authenticated_readonly_order_status(
            account_fingerprint=ACCOUNT,order=order,
            observed_ms=NOW*1000-1000,
        )
        r=self.call(payload=scenario(action="REVIEW"))
        self.assertFalse(r["account_only_buy_order_correlated_not_owned"])
        self.assert_untrusted(r)

    def test_review_wrong_uid_fingerprint_price_qty_or_epoch_fails_closed(self):
        self.call()
        for changes in (
            {"uid":100}, {"account":OTHER}, {"price":"20000"},
            {"qty":"5"}, {"epoch":NOW-1},
        ):
            with self.subTest(changes=changes):
                self.denied(payload=scenario(action="REVIEW",**changes))

    def test_restart_reopen_retains_uncertain_without_issuer(self):
        self.call()
        self.call(payload=scenario(action="UNCERTAIN",epoch=NOW+1))
        reopened=DecisionJournal(self.db)
        self.assertEqual(reopened.m07_offline_intent_summary()[
            "local_origin_uncertain_no_retry_count"],1)
        r=self.call(payload=scenario(action="REVIEW",epoch=NOW+2))
        self.assertEqual(r["origin_local_state"],
                         "OFFLINE_UNCERTAIN_HOLD_NO_RETRY")
        self.assert_untrusted(r)

    def test_concurrent_same_client_commitment_exactly_one_creation(self):
        d=draft()
        def one(_):
            return self.journal.prepare_m07_order_origin_commitment_offline(
                account_uid=12345,account_fingerprint=ACCOUNT,
                draft=d,observed_epoch=NOW)
        with ThreadPoolExecutor(max_workers=7) as pool:
            rows=list(pool.map(one,range(7)))
        self.assertEqual(sum(r["replay"] is False for r in rows),1)
        self.assertEqual(len(self.local_rows()),1)

    def test_orchestrator_injected_external_authority_cannot_use_origin(self):
        class FakeAI:
            pass
        bot=DailyScalpingOrchestrator(
            Settings(),self.journal,ai_role_caller=FakeAI())
        with self.assertRaisesRegex(ValueError,"M07_ORIGIN_HARDLOCK_OR_AUTHORITY_INVALID"):
            bot.review_m07_original_bot_origin_local_once(
                action="PREPARE",account_uid=12345,
                account_fingerprint=ACCOUNT,draft=draft(),
                observed_epoch=NOW)
        self.assertEqual(self.local_rows(), [])

    def test_wrong_action_or_synthetic_sell_invalid_source(self):
        self.denied(payload=scenario(action="SEND"))
        self.denied(payload=scenario(symbol="FLOKIIDR"))
        self.denied(payload=scenario(price="NaN"))
        self.denied(payload=scenario(price="-10"))
        self.denied(payload=scenario(uid=True))
        self.denied(payload=scenario(account="bad"))
        self.assertEqual(self.local_rows(), [])

    def test_action_without_fixture_or_orphan_file_denied(self):
        rc,out,err=self.cli(argv=[
            "--health","--m07-origin-commitment-file",str(self.file),
        ])
        self.assertEqual((rc,out),(2,""))
        self.assertIn("M07_ORIGIN_FILE_REQUIRES_OFFLINE_ACTION",err)
        rc,out,err=self.cli(argv=[
            "--m07-origin-commitment-offline-once",
            "--db",str(self.db),
        ])
        self.assertEqual((rc,out),(2,""))

    def test_missing_or_misnamed_db_denied_before_creation(self):
        for p in (
            self.folder/"missing.sqlite3",
            self.folder/"random.sqlite3",
            Path("/tmp/m11_offline_fixture.sqlite3"),
        ):
            with self.subTest(p=str(p)):
                self.denied(db=p)
        self.assertFalse((self.folder/"missing.sqlite3").exists())

    def test_outside_scenario_file_and_symlink_denied(self):
        other=TemporaryDirectory(prefix="m07-other-",dir="/tmp")
        self.addCleanup(other.cleanup)
        p=Path(other.name)/"origin.json"
        p.write_text(json.dumps(scenario()))
        p.chmod(0o600)
        self.denied(path=p)
        self.file.write_text(json.dumps(scenario()))
        self.file.chmod(0o600)
        link=self.folder/"originlink.json"
        link.symlink_to(self.file)
        self.denied(path=link)

    def test_insecure_file_db_parent_and_wal_sidecar_denied(self):
        self.file.write_text(json.dumps(scenario()))
        self.file.chmod(0o644)
        self.denied(path=self.file, rewrite=False)
        self.file.chmod(0o600)
        self.db.chmod(0o644)
        self.denied()
        self.db.chmod(0o600)
        self.folder.chmod(0o755)
        self.denied()
        self.folder.chmod(0o700)
        wal=Path(str(self.db)+"-wal")
        wal.write_bytes(b"invalid")
        self.denied()

    def test_live_env_or_go_flag_does_not_enable_order(self):
        for opt in (
            {"INDODAX_TRADING_ENABLED":"YES"},
            {"INDODAX_ALLOW_LIVE_EXECUTION":"YES"},
            {"INDODAX_EXECUTION_MODE":"LIVE"},
        ):
            with self.subTest(opt=opt):
                with patch.object(
                    DecisionJournal,"open_m11_offline_fixture_readonly",
                    side_effect=AssertionError("MUST_NOT_OPEN"),
                ):
                    rc,out,err=self.cli(env=opt)
                self.assertEqual((rc,out),(2,""))
                self.assertIn("M01_LIVE_NOT_IMPLEMENTED",err)
        self.assertEqual(self.local_rows(),[])

    def test_original_adapter_all_order_http_remains_hardlocked(self):
        a=OfficialV2OrderAdapter()
        for name in ("create_order","get_order","cancel_order","dispatch"):
            with self.subTest(method=name):
                with self.assertRaises(M07Hardlocked):
                    getattr(a,name)()
        self.assertEqual(a.contract_status()["network_allowed"],False)

    def test_no_paid_ai_telegram_public_or_private_network(self):
        with (
            patch("indodax_ai_bot.__main__.TelegramBotApiClient") as tg,
            patch("indodax_ai_bot.__main__.SumopodChatAdapter") as ai,
            patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket") as pub,
            patch("indodax_ai_bot.orchestrator.OfficialV2OrderAdapter") as adapt,
        ):
            # No actual order adapter dispatch; the static hardlock
            # report here is patched only as a contract spy.
            adapt.contract_status.return_value={
                "network_allowed":False,"execution_allowed":False,
            }
            self.call()
        for mock in (tg, ai, pub):
            mock.assert_not_called()
        adapt.contract_status.assert_called_once()
        adapt.create_order.assert_not_called()
        adapt.dispatch.assert_not_called()

    def test_review_uses_only_existing_same_journal_no_new_events(self):
        self.call()
        before=sha256(self.db.read_bytes()).hexdigest()
        for _ in range(3):
            r=self.call(payload=scenario(action="REVIEW"))
            self.assert_untrusted(r)
        self.assertEqual(sha256(self.db.read_bytes()).hexdigest(),before)
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM m08_position_settled_idr_events"
            ).fetchone()[0],0)
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM m08_post_sell_discovery_claims"
            ).fetchone()[0],0)


if __name__ == "__main__":
    unittest.main()
