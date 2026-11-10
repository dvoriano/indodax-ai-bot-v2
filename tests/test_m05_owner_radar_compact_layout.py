"""Mobile-first original M05 public WATCH cards, no network or service changes."""
from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from indodax_ai_bot.m05_radar_momentum_nonlive import (
    format_owner_radar_compact,
)

WIB = ZoneInfo("Asia/Jakarta")


def moment(hour, minute):
    return int(datetime(2026, 11, 6, hour, minute, tzinfo=WIB).timestamp())


def candidate(**overrides):
    row = {
        "pair": "fun_idr",
        "reference_epoch": moment(20, 15),
        "observed_epoch": moment(20, 20),
        "reference_price_idr": "33933",
        "latest_price_idr": "36899",
        "observed_rise_pct": "8.74",
        "price_watch": True,
        "volume_watch": True,
        "volume_24h_idr": "853286206.00000000",
        "volume_24h_net_change_idr": "61730318.00000000",
        "volume_24h_net_change_pct": "7.80",
        "volume_comparison_seconds": 180,
        "missing_intermediate_minute_samples": False,
        "casebook_early_watch_observed": False,
    }
    row.update(overrides)
    return row


class M05PhoneRadarFormatting(unittest.TestCase):
    def test_real_fun_20261106_compact_separators_and_no_order(self):
        message = format_owner_radar_compact(candidate())
        self.assertIn("FUN/IDR", message)
        self.assertIn("+8,74%", message)
        self.assertIn("Rp33.933 → Rp36.899", message)
        self.assertIn("Rp853,29 million", message)
        self.assertIn("+Rp61,73 million (+7,80%)", message)
        self.assertIn("20:15→20:20", message)
        self.assertNotIn("00000000", message)
        self.assertIn("NOT A BUY RECOMMENDATION", message)
        self.assertIn("UNVERIFIED", message)
        self.assertIn("NOT 1m executed turnover", message)
        self.assertNotIn("PASS/REJECT", message)
        self.assertLessEqual(len(message.splitlines()), 7)
        self.assertLess(len(message), 390)

    def test_real_beat_negative_price_positive_rolling_turnover(self):
        message = format_owner_radar_compact(candidate(
            pair="beat_idr",
            reference_epoch=moment(20, 11),
            observed_epoch=moment(20, 16),
            reference_price_idr="2035",
            latest_price_idr="2029",
            observed_rise_pct="-0.29",
            price_watch=False,
            volume_watch=True,
            volume_24h_idr="2781379603",
            volume_24h_net_change_idr="101204341",
            volume_24h_net_change_pct="3.78",
        ))
        self.assertIn("24H VOLUME CHANGE", message)
        self.assertIn("🔻 Price 20:11→20:16: -0,29%", message)
        self.assertIn("Rp2.035 → Rp2.029", message)
        self.assertIn("Rp2,78 billion", message)
        self.assertIn("+Rp101,2 million (+3,78%)", message)
        self.assertNotIn("REKOMENDASI BUY:", message)
        self.assertLess(len(message), 390)

    def test_early_watch_missing_samples_is_clearly_disclosed(self):
        message = format_owner_radar_compact(candidate(
            pair="uai_idr",
            price_watch=True,
            volume_watch=False,
            casebook_early_watch_observed=True,
            missing_intermediate_minute_samples=True,
        ))
        self.assertIn("EARLY_WATCH (historical)", message)
        self.assertIn("Missing intermediate minute samples", message)
        self.assertIn("NOT a closed 1m candle return", message)
        self.assertNotIn("Δ 24h volume", message)
        self.assertLessEqual(len(message.splitlines()), 7)

    def test_price_subrupiah_precision_not_lost(self):
        message = format_owner_radar_compact(candidate(
            pair="micro_idr",
            reference_price_idr="0.00000012",
            latest_price_idr="0.00000014",
            observed_rise_pct="16.67",
            volume_watch=False,
        ))
        self.assertIn("Rp0,00000012 → Rp0,00000014", message)
        self.assertIn("16,67%", message)

    def test_volume_only_watch_never_implies_buy_flow(self):
        message = format_owner_radar_compact(candidate(
            pair="icnt_idr", price_watch=False, volume_watch=True,
            reference_price_idr="5003", latest_price_idr="4966",
            observed_rise_pct="-0.74",
            volume_24h_idr="749147044",
            volume_24h_net_change_idr="51659240",
            volume_24h_net_change_pct="7.41",
        ))
        self.assertIn("24H VOLUME CHANGE", message)
        self.assertIn("-0,74%", message)
        self.assertIn("Rp749,15 million", message)
        self.assertIn("NOT 1m executed turnover", message)
        self.assertIn("BUY/SELL pressure", message)


if __name__ == "__main__":
    unittest.main()
