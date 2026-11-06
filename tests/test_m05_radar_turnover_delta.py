"""Original V2 M03->M05 public ticker rolling-volume CHANGE watch.

No new HTTP, private/account data, models, orders or duplicate scanner.
The 24h rolling turnover delta is never claimed as true one-minute volume.
"""
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from indodax_ai_bot.m05_radar_momentum_nonlive import (
    inspect_original_m03_radar_readonly,
    send_one_owner_radar_momentum_nonlive,
)
from indodax_ai_bot.owner_control import OwnerDecisionLedger


NOW = 1800000140
LATEST = 1800000120


class FakeOwnerApi:
    def __init__(self):
        self.calls = []

    def call(self, method, payload):
        self.calls.append((method, payload))
        return {"message_id": 71, "chat": {"id": 1729, "type": "private"}}


class OriginalRollingTickerRadarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "decisions.sqlite3"
        with sqlite3.connect(self.path) as db:
            db.execute("""
                CREATE TABLE public_ticker_minute_samples (
                    pair TEXT, minute_epoch INTEGER, observed_epoch INTEGER,
                    last_idr TEXT, volume_24h_idr TEXT, spread_bps TEXT,
                    PRIMARY KEY(pair,minute_epoch)
                )
            """)
        os.chmod(self.path, 0o600)

    def add(self, pair, price, rolling_volume, *, lag=0, spread="22"):
        minute = LATEST - lag * 60
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO public_ticker_minute_samples "
                "(pair,minute_epoch,observed_epoch,last_idr,"
                "volume_24h_idr,spread_bps) VALUES (?,?,?,?,?,?)",
                (pair, minute, minute + 10, str(price),
                 str(rolling_volume), spread),
            )

    def inspect(self, now=NOW):
        return inspect_original_m03_radar_readonly(
            db_path=self.path, now_epoch=now,
        )

    def test_rolling_turnover_burst_alerts_even_without_price_rally(self):
        for lag, price, volume in (
            (3, "100", "210000000"),
            (2, "100", "215000000"),
            (1, "100", "220000000"),
            (0, "101", "260000000"),
        ):
            self.add("cyber_idr", price, volume, lag=lag)
        snap = self.inspect()
        self.assertEqual(snap["status"],
                         "M03_ORIGINAL_PUBLIC_RADAR_OBSERVED_UNATTESTED")
        self.assertEqual(len(snap["candidates"]), 1)
        row = snap["candidates"][0]
        self.assertEqual(row["pair"], "cyber_idr")
        self.assertEqual(row["watch_type"], "ROLLING_TURNOVER_CHANGE_WATCH")
        self.assertFalse(row["price_watch"])
        self.assertTrue(row["volume_watch"])
        self.assertEqual(row["volume_24h_net_change_idr"], "50000000")
        self.assertEqual(row["volume_comparison_seconds"], 180)
        self.assertTrue(row["rolling_24h_turnover_not_1m_traded_volume"])
        self.assertFalse(row["execution_allowed"] if
                         "execution_allowed" in row else False)

    def test_no_turnover_alert_when_one_minute_is_missing(self):
        self.add("rvn_idr", "100", "210000000", lag=3)
        self.add("rvn_idr", "100", "270000000", lag=1)
        self.add("rvn_idr", "101", "300000000", lag=0)
        # The absent two-minute-prior sample forbids treating a 3m
        # rolling change as a continuous 3-minute observation.
        # The valid adjacent 1-minute difference is only 30m and would
        # qualify, so keep it below the explicit minimum instead.
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE public_ticker_minute_samples SET volume_24h_idr=? "
                "WHERE pair=? AND minute_epoch=?",
                ("285000000", "rvn_idr", LATEST),
            )
        self.assertEqual(self.inspect()["candidates"], [])

    def test_gapped_reference_cannot_be_called_one_minute_volume(self):
        self.add("fun_idr", "100", "210000000", lag=3)
        self.add("fun_idr", "101", "290000000", lag=0)
        self.assertEqual(self.inspect()["candidates"], [])

    def test_price_rally_still_alerts_if_rolling_volume_is_flat(self):
        self.add("pieverse_idr", "100", "500000000", lag=3)
        self.add("pieverse_idr", "101", "500000000", lag=2)
        self.add("pieverse_idr", "102", "500000000", lag=1)
        self.add("pieverse_idr", "112", "500000000", lag=0)
        row = self.inspect()["candidates"][0]
        self.assertTrue(row["price_watch"])
        self.assertFalse(row["volume_watch"])
        self.assertEqual(row["watch_type"], "PRICE_MOMENTUM_WATCH")

    def test_below_minimum_24h_turnover_stays_unalerted(self):
        self.add("tiny_idr", "100", "100000000", lag=1)
        self.add("tiny_idr", "100", "140000000", lag=0)
        self.assertEqual(self.inspect()["candidates"], [])

    def test_wide_spread_stays_unalerted(self):
        self.add("wide_idr", "100", "200000000", lag=1)
        self.add("wide_idr", "140", "250000000", lag=0, spread="101")
        self.assertEqual(self.inspect()["candidates"], [])

    def test_old_source_stays_unalerted(self):
        self.add("old_idr", "100", "200000000", lag=1)
        self.add("old_idr", "101", "240000000", lag=0)
        self.assertEqual(self.inspect(now=LATEST+250)["candidates"], [])

    def test_owner_private_notice_is_explanatory_and_deduplicated(self):
        self.add("cyber_idr", "100", "220000000", lag=1)
        self.add("cyber_idr", "101", "260000000", lag=0)
        ledger_path = Path(self.temp.name) / "owner.sqlite3"
        ledger = OwnerDecisionLedger(ledger_path, owner_user_id=1729)
        os.chmod(ledger_path, 0o600)
        api = FakeOwnerApi()
        result = send_one_owner_radar_momentum_nonlive(
            api=api, ledger=ledger, now_epoch=NOW, db_path=self.path,
        )
        self.assertEqual(result["status"], "M05_RADAR_OWNER_PRIVATE_NOTICE_SENT")
        self.assertEqual(len(api.calls), 1)
        method, payload = api.calls[0]
        self.assertEqual(method, "sendMessage")
        self.assertEqual(payload["chat_id"], 1729)
        self.assertNotIn("reply_markup", payload)
        text = payload["text"]
        self.assertIn("PERUBAHAN VOLUME 24J", text)
        self.assertIn("BUKAN jumlah transaksi 1 menit", text)
        self.assertIn("maintenance/suspend", text)
        self.assertIn("RADAR | CYBER/IDR", text)
        self.assertLess(len(text), 420)
        repeat = send_one_owner_radar_momentum_nonlive(
            api=api, ledger=ledger, now_epoch=NOW+10, db_path=self.path,
        )
        self.assertIn(repeat["status"], (
            "M05_RADAR_THROTTLED",
            "M05_RADAR_NO_CURRENT_QUALIFIED_WATCH",
        ))
        self.assertEqual(len(api.calls), 1)


if __name__ == "__main__":
    unittest.main()
