"""M03/M05 original radar case: public UAI early watch, no buy advice.

Test the SAME existing M03 archive and same original owner M05 SQLite,
but no HTTP, Telegram network, paid providers, services or orders.
"""
from datetime import datetime
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.m05_radar_momentum_nonlive import (
    inspect_original_m03_radar_readonly,
    send_one_owner_radar_momentum_nonlive,
)

WIB = ZoneInfo("Asia/Jakarta")
AT = lambda hour, minute, second=0: int(
    datetime(2026, 11, 6, hour, minute, second, tzinfo=WIB).timestamp()
)


class FakeTelegram:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    def call(self, method, payload):
        self.calls.append((method, payload))
        if self.fail:
            raise RuntimeError("synthetic transport uncertainty")
        return {"message_id": 9, "chat": {
            "id": payload["chat_id"], "type": "private",
        }}


class M03M05RadarOriginalNonlive(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        parent = Path(tmp.name)
        self.source = parent / "decisions.sqlite3"
        with sqlite3.connect(self.source) as db:
            db.executescript("""
                CREATE TABLE public_ticker_minute_samples (
                    pair TEXT NOT NULL,
                    minute_epoch INTEGER NOT NULL,
                    observed_epoch INTEGER NOT NULL,
                    ticker_epoch INTEGER NOT NULL,
                    last_idr TEXT NOT NULL,
                    volume_24h_idr TEXT NOT NULL,
                    spread_bps TEXT NOT NULL,
                    PRIMARY KEY(pair,minute_epoch)
                );
                CREATE TABLE public_surge_cases (
                    pair TEXT NOT NULL,
                    minute_epoch INTEGER NOT NULL,
                    cohort TEXT NOT NULL
                );
            """)
        os.chmod(self.source, 0o600)
        self.owner_path = parent / "owner.sqlite3"
        self.owner = OwnerDecisionLedger(self.owner_path, owner_user_id=456789123)
        os.chmod(self.owner_path, 0o600)
        self.now = AT(9, 25, 35)

    def add(self, pair, hour, minute, price, *, vol="15420018760",
            spread="20"):
        epoch = AT(hour, minute)
        with sqlite3.connect(self.source) as db:
            db.execute(
                "INSERT INTO public_ticker_minute_samples "
                "VALUES (?,?,?,?,?,?,?)",
                (pair, epoch, epoch+10, epoch+10, str(price), vol, spread),
            )

    def early(self, pair, hour=9, minute=25):
        with sqlite3.connect(self.source) as db:
            db.execute(
                "INSERT INTO public_surge_cases VALUES (?,?,?)",
                (pair, AT(hour, minute), "EARLY_WATCH"),
            )

    def uai(self, *, all_minutes=True):
        minutes = [
            (20, 1633), (21, 1650), (22, 1660),
            (23, 1720), (24, 1780), (25, 1840),
        ]
        for minute, price in minutes:
            if not all_minutes and minute == 24:
                continue
            self.add("uai_idr", 9, minute, price)
        self.early("uai_idr")

    def inspect(self, now=None):
        return inspect_original_m03_radar_readonly(
            db_path=self.source, now_epoch=self.now if now is None else now
        )

    def send(self, api, now=None):
        return send_one_owner_radar_momentum_nonlive(
            api=api, ledger=self.owner,
            db_path=self.source, now_epoch=self.now if now is None else now,
        )

    def test_uai_0925_early_watch_becomes_no_button_radar_notice(self):
        self.uai()
        sample = self.inspect()
        self.assertEqual(sample["status"],
                         "M03_ORIGINAL_PUBLIC_RADAR_OBSERVED_UNATTESTED")
        self.assertEqual(sample["candidates"][0]["pair"], "uai_idr")
        self.assertTrue(sample["candidates"][0]["casebook_early_watch_observed"])
        api = FakeTelegram()
        sent = self.send(api)
        self.assertEqual(sent["messages_sent"], 1)
        self.assertEqual(len(api.calls), 1)
        method, payload = api.calls[0]
        self.assertEqual(method, "sendMessage")
        self.assertNotIn("reply_markup", payload)
        self.assertIn("OBSERVASI, BUKAN REKOMENDASI BUY", payload["text"])
        self.assertIn("EARLY_WATCH historis", payload["text"])
        self.assertIn("BELUM TERVERIFIKASI", payload["text"])
        self.assertFalse(sent["execution_allowed"])
        self.assertIsNone(sent["owner_final_decision"])

    def test_no_duplicate_even_if_same_source_replayed(self):
        self.uai()
        api = FakeTelegram()
        self.assertEqual(self.send(api)["messages_sent"], 1)
        self.assertEqual(self.send(api)["messages_sent"], 0)
        self.assertEqual(len(api.calls), 1)

    def test_source_gap_is_explicit_and_never_called_1m_return(self):
        self.uai(all_minutes=False)
        sample = self.inspect()
        self.assertTrue(
            sample["candidates"][0]["missing_intermediate_minute_samples"]
        )
        api = FakeTelegram()
        self.send(api)
        self.assertIn(
            "ada menit tanpa sampel", api.calls[0][1]["text"]
        )
        self.assertIn("BUKAN return candle 1m", api.calls[0][1]["text"])

    def test_stale_source_does_not_send(self):
        self.uai()
        api = FakeTelegram()
        self.assertEqual(self.send(api, now=AT(9,28))["messages_sent"], 0)
        self.assertFalse(api.calls)

    def test_volume_below_original_floor_is_ignored(self):
        self.add("uai_idr", 9,20,1633,vol="149999999")
        self.add("uai_idr", 9,25,1840,vol="149999999")
        self.early("uai_idr")
        self.assertEqual(self.inspect()["candidates"], [])

    def test_excluded_main_strategy_coin_never_alerts(self):
        self.add("floki_idr",9,20,100)
        self.add("floki_idr",9,25,150)
        self.early("floki_idr")
        self.assertEqual(self.inspect()["candidates"], [])

    def test_unknown_or_unsafe_original_db_no_notice(self):
        self.uai()
        alias = self.source.with_name("alias.sqlite3")
        alias.symlink_to(self.source)
        report = inspect_original_m03_radar_readonly(
            db_path=alias, now_epoch=self.now,
        )
        self.assertEqual(report["candidates"], [])
        self.assertEqual(report["status"], "M03_RADAR_ORIGINAL_DB_NOT_TRUSTED")

    def test_failed_send_is_claimed_and_not_retried(self):
        self.uai()
        failed = FakeTelegram(fail=True)
        self.assertEqual(self.send(failed)["messages_sent"], 0)
        self.assertEqual(len(failed.calls), 1)
        retry = FakeTelegram()
        self.assertEqual(self.send(retry)["messages_sent"], 0)
        self.assertFalse(retry.calls)

    def test_no_provider_no_exchange_and_no_final(self):
        self.uai()
        snap = self.inspect()
        self.assertFalse(snap["execution_allowed"])
        self.assertFalse(snap["source_authenticated_for_g1"])
        self.assertFalse(snap["three_ai_role_responses_observed"])
        self.assertFalse(snap["owner_order_specific_final_verified"])

    def test_default_off_and_separate_opt_in_same_fast_receiver(self):
        from indodax_ai_bot import telegram_service_tick as service
        cfg = {"M05_TELEGRAM_FAST_LONGPOLL_GO": "YES"}
        with (patch.object(
                service, "_prepare_tick_runtime",
                return_value=("fake-api","fake-ledger",123,"fake-db",None)),
              patch.object(
                service, "dedicated_owner_fast_loop_nonlive",
                return_value={"status":"OFFLINE_MOCK"}) as run):
            service.service_fast_loop_nonlive(env=cfg, max_batches=1)
            self.assertIsNone(run.call_args.kwargs["auto_radar_alert"])
            cfg["M05_RADAR_MOMENTUM_ALERT_GO"] = "YES"
            service.service_fast_loop_nonlive(env=cfg, max_batches=1)
            self.assertTrue(callable(
                run.call_args.kwargs["auto_radar_alert"]
            ))


if __name__ == "__main__":
    unittest.main()
