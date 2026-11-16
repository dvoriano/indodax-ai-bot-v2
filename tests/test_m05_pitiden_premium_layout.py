"""Premium WATCH presentation regression; no Telegram or exchange transport."""
from decimal import Decimal
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from indodax_ai_bot.m05_pitiden_premium_layout import (
    build_timeframes, format_pitiden_premium, read_price_references, FRAMES,
)

END = 1794794400


def candidate(**updates):
    c = {
        "pair": "ondo_idr", "observed_epoch": END,
        "reference_epoch": END-245, "observed_rise_pct": "1.77",
        "latest_price_idr": "5739", "reference_price_idr": "5639",
        "volume_24h_idr": "461460000",
        "volume_24h_net_change_idr": "19780",
        "microstructure_observation": {"status": "NOT_OBSERVED"},
    }
    c.update(updates)
    return c


class PremiumWatchTests(unittest.TestCase):
    def test_all_seven_intervals_have_independent_signs(self):
        refs = [(END-secs, Decimal(price)) for (_, secs), price in zip(
            FRAMES, ["5692", "5765", "5804", "5703", "5664", "5588", "5268"]
        )]
        rows = build_timeframes(candidate(), refs)
        self.assertEqual([r["tf"] for r in rows],
                         ["1m", "5m", "10m", "15m", "30m", "1h", "24h"])
        self.assertGreater(rows[0]["change"], 0)
        self.assertLess(rows[1]["change"], 0)
        self.assertLess(rows[2]["change"], 0)
        self.assertGreater(rows[3]["change"], 0)
        msg = format_pitiden_premium(candidate(), refs, source_kind="WS3",
                                     now_epoch=END)
        self.assertIn("📡 <b>ONDO/IDR · Rp5.739</b>", msg)
        self.assertIn("🟢 1m", msg)
        self.assertIn("🔴 10m", msg)
        self.assertIn("🟢 15m", msg)
        self.assertIn("5.804", msg)
        self.assertIn("461,46jt", msg)
        self.assertIn("Mixed", msg)
        self.assertIn("Score N/A", msg)
        self.assertIn("Not BUY", msg)
        self.assertNotIn("19,78rb", msg)
        self.assertLessEqual(len(msg.splitlines()), 14)

    def test_no_interpolation_or_forward_reference_or_fake_volume(self):
        refs = [(END-59, Decimal("1")), (END-76, Decimal("2")),
                (END-310, Decimal("5700"))]
        rows = build_timeframes(candidate(), refs)
        self.assertIsNone(rows[0]["change"])
        self.assertTrue(rows[1]["approx"])
        self.assertEqual(rows[1]["reference_epoch"], END-310)
        self.assertTrue(all(r["volume"] is None for r in rows[:-1]))
        self.assertEqual(rows[-1]["volume"], Decimal("461460000"))
        self.assertIsNone(rows[-1]["reference_price"])
        msg = format_pitiden_premium(candidate(), refs,
                                     source_kind="WS3", now_epoch=END)
        self.assertIn("5m~", msg)
        self.assertIn("N/A", msg)
        self.assertIn("24h rolling", msg)
        self.assertIn("Trigger +1,77% / 4m05s", msg)

    def test_flat_and_subrupiah_keep_actual_precision(self):
        c = candidate(latest_price_idr="0.00000014")
        refs = [(END-60, Decimal("0.00000014")),
                (END-300, Decimal("0.00000012"))]
        msg = format_pitiden_premium(c, refs, source_kind="WS3",
                                     now_epoch=END)
        self.assertIn("Rp0,00000014", msg)
        self.assertIn("0,00000012", msg)
        self.assertIn("⚪ 1m", msg)
        self.assertIn("🟢 5m", msg)

    def test_current_partial_book_spread_only_no_stale_or_forged(self):
        c = candidate(microstructure_observation={
            "status": "PARTIAL_SOURCE_OBSERVATION",
            "source_malformed": False,
            "orderbook_snapshot_observed": True,
            "orderbook_locally_observed_epoch": END-4,
            "spread_bps": "12",
        })
        msg = format_pitiden_premium(c, [], source_kind="WS3", now_epoch=END)
        self.assertIn("Spread 0,12%", msg)
        for change in ({"source_malformed": True},
                       {"orderbook_locally_observed_epoch": END-91},
                       {"orderbook_locally_observed_epoch": END+1}):
            bad = dict(c)
            bad["microstructure_observation"] = dict(
                c["microstructure_observation"], **change)
            self.assertIn("Spread N/A", format_pitiden_premium(
                bad, [], source_kind="WS3", now_epoch=END))

    def test_archive_queries_have_asof_cutoffs_source_isolation_and_conflict(self):
        with TemporaryDirectory() as td:
            d = Path(td)
            source = d/"source.sqlite3"
            evidence = d/"evidence.sqlite3"
            with sqlite3.connect(source) as db:
                db.execute("CREATE TABLE m03_ws_summary_watch_minute("
                           "pair TEXT,minute_epoch INTEGER,exchange_epoch INTEGER,"
                           "last_idr TEXT,public_offset INTEGER)")
                db.execute("CREATE TABLE public_ticker_minute_samples("
                           "pair TEXT,minute_epoch INTEGER,observed_epoch INTEGER,"
                           "last_idr TEXT)")
                db.executemany("INSERT INTO m03_ws_summary_watch_minute VALUES(?,?,?,?,?)", [
                    ("ondo_idr", END-60, END-60, "5692", 1),
                    ("ondo_idr", END-300, END-300, "5765", 2),
                    ("other_idr", END-600, END-600, "1", 3),
                    ("ondo_idr", END+60, END+60, "1", 4),
                ])
                db.execute("INSERT INTO public_ticker_minute_samples VALUES(?,?,?,?)",
                           ("ondo_idr", END-60, END-60, "55"))
            with sqlite3.connect(evidence) as db:
                db.execute("CREATE TABLE ws3_source_evidence("
                           "pair TEXT,minute_epoch INTEGER,source_exchange_epoch INTEGER,"
                           "last_idr TEXT,public_offset INTEGER,source_status TEXT)")
                db.execute("CREATE TABLE ws3_source_cycles("
                           "minute_epoch INTEGER,recorded_local_epoch INTEGER)")
                for dt, p, captured in [(300, "999", END-290),
                                         (86400, "5268", END-86395),
                                         (600, "99", END+1)]:
                    db.execute("INSERT INTO ws3_source_evidence VALUES(?,?,?,?,?,?)",
                               ("ondo_idr", END-dt, END-dt, p, 3,
                                "ELIGIBLE_SOURCE_OBSERVATION"))
                    db.execute("INSERT INTO ws3_source_cycles VALUES(?,?)",
                               (END-dt, captured))
            source.chmod(0o600)
            evidence.chmod(0o600)
            refs = read_price_references(candidate(), db_path=source,
                now_epoch=END, source_kind="WS3", evidence_path=evidence)
            self.assertEqual(dict(refs), {
                 END-60: Decimal("5692"), END-86400: Decimal("5268")})
            rest = read_price_references(candidate(), db_path=source,
                now_epoch=END, source_kind="REST", evidence_path=evidence)
            self.assertEqual(rest, [(END-60, Decimal("55"))])

    def test_missing_archive_not_created(self):
        with TemporaryDirectory() as td:
            p = Path(td)/"missing.sqlite3"
            self.assertEqual(read_price_references(
                candidate(), db_path=p, now_epoch=END, source_kind="WS3",
                evidence_path=p), [])
            self.assertFalse(p.exists())


if __name__ == "__main__":
    unittest.main()
