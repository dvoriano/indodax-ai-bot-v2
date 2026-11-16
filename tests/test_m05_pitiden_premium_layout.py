"""Premium WATCH presentation regression; no Telegram or exchange transport."""
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from zoneinfo import ZoneInfo
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from indodax_ai_bot.m05_pitiden_premium_layout import (
    build_timeframes, format_pitiden_premium, read_price_references,
    read_closed_candles, interval_windows, FRAMES,
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
    def test_wib_windows_and_midnight_rollover(self):
        wib = ZoneInfo("Asia/Jakarta")
        end = int(datetime(2026,11,16,10,43,27,tzinfo=wib).timestamp())
        def hhmm(t):
            return datetime.fromtimestamp(t,wib).strftime("%H:%M:%S")
        self.assertEqual([(tf,hhmm(a),hhmm(b))
                          for tf,a,b in interval_windows(end)], [
            ("1m","10:42:00","10:43:00"),
            ("5m","10:35:00","10:40:00"),
            ("10m","10:30:00","10:40:00"),
            ("15m","10:15:00","10:30:00"),
            ("30m","10:00:00","10:30:00"),
            ("1h","09:00:00","10:00:00"),
            ("1D","00:00:00","10:43:27"),
        ])
        midnight = int(datetime(2026,11,16,tzinfo=wib).timestamp())
        rows = build_timeframes(candidate(observed_epoch=midnight), [])
        self.assertEqual(rows[0]["start_epoch"], midnight-60)
        self.assertEqual(rows[-1]["start_epoch"], midnight)
        self.assertEqual(rows[-1]["close_price"], Decimal("5739"))
        self.assertIsNone(rows[-1]["change"])

    def test_independent_signs_and_close_not_open(self):
        end = int(datetime(2026,11,16,10,43,27,
                           tzinfo=ZoneInfo("Asia/Jakarta")).timestamp())
        c = candidate(observed_epoch=end,reference_epoch=end-245)
        lookup = list(interval_windows(end))
        candles = {}
        # Overlapping intervals intentionally share final close.
        for _,a,b in lookup[:-1]:
            for t in range(a,b,60):
                candles[t] = (Decimal("5700"),Decimal("5710"))
        for tf,a,b in lookup[:-1]:
            if tf == "1m":
                candles[a]=(Decimal("5700"),Decimal("5730"))
            elif tf == "5m":
                candles[a]=(Decimal("5750"),Decimal("5710"))
            elif tf == "10m":
                candles[a]=(Decimal("5770"),Decimal("5710"))
            elif tf == "15m":
                candles[a]=(Decimal("5660"),Decimal("5700"))
        candles[lookup[-1][1]]=(Decimal("5268"),Decimal("5270"))
        rows=build_timeframes(c, [], candles=candles)
        self.assertEqual([r["tf"] for r in rows],
                         ["1m","5m","10m","15m","30m","1h","1D"])
        self.assertGreater(rows[0]["change"],0)
        self.assertLess(rows[1]["change"],0)
        self.assertLess(rows[2]["change"],0)
        self.assertGreater(rows[3]["change"],0)
        self.assertEqual(rows[1]["close_price"],Decimal("5710"))
        self.assertEqual(rows[2]["close_price"],Decimal("5710"))
        self.assertEqual(rows[-1]["close_price"],Decimal("5739"))
        self.assertEqual(rows[-1]["opening_price"],Decimal("5268"))
        self.assertTrue(all(row["volume"] is None for row in rows))
        msg=format_pitiden_premium(c,[],source_kind="WS3",
                                    now_epoch=end,candles=candles)
        self.assertIn("📡 <b>ONDO/IDR · Rp5.739</b>",msg)
        self.assertIn("🟢 1m",msg)
        self.assertIn("🔴 10m",msg)
        self.assertIn("🟢 15m",msg)
        self.assertIn("1D",msg)
        self.assertIn("5.710",msg)
        self.assertNotIn("461,46jt",msg)
        self.assertIn("Mixed",msg)
        self.assertIn("Score N/A",msg)
        self.assertIn("Not BUY",msg)
        self.assertLessEqual(len(msg.splitlines()),14)

    def test_sample_edges_approximate_and_no_future_or_cross_boundary(self):
        c=candidate()
        _,start,stop=next(interval_windows(END))
        _,midnight,_=list(interval_windows(END))[-1]
        refs=[(start-1,Decimal("1")),(start+2,Decimal("5600")),
              (stop-1,Decimal("5700")),(stop,Decimal("9999")),
              (midnight-1,Decimal("3"))]
        rows=build_timeframes(c,refs)
        self.assertTrue(rows[0]["approx"])
        self.assertEqual(rows[0]["opening_price"],Decimal("5600"))
        self.assertEqual(rows[0]["close_price"],Decimal("5700"))
        self.assertIsNone(rows[-1]["opening_price"])
        self.assertEqual(rows[-1]["close_price"],Decimal("5739"))
        self.assertTrue(all(r["volume"] is None for r in rows))
        gap=build_timeframes(c,[(stop-1,Decimal("5700"))])[0]
        self.assertIsNone(gap["change"])
        self.assertEqual(gap["close_price"],Decimal("5700"))
        msg=format_pitiden_premium(c,refs,source_kind="WS3",now_epoch=END)
        self.assertIn("1m~",msg)
        self.assertIn("N/A",msg)
        self.assertIn("1D since 00:00 WIB",msg)
        self.assertIn("Trigger +1,77% / 4m05s",msg)

    def test_daily_open_resets_never_uses_rolling_24h_turnover(self):
        end=int(datetime(2026,11,16,0,1,5,
                         tzinfo=ZoneInfo("Asia/Jakarta")).timestamp())
        c=candidate(observed_epoch=end,latest_price_idr="110")
        midnight=end-65
        rows=build_timeframes(c,[(midnight-1,Decimal("1")),
                                  (midnight+5,Decimal("100"))])
        daily=rows[-1]
        self.assertEqual(daily["change"],Decimal("10"))
        self.assertEqual(daily["close_price"],Decimal("110"))
        self.assertTrue(daily["approx"])
        self.assertIsNone(daily["volume"])

    def test_flat_and_subrupiah_keep_actual_precision(self):
        c=candidate(latest_price_idr="0.00000014")
        _,a,b=next(interval_windows(END))
        msg=format_pitiden_premium(c,[],source_kind="WS3",now_epoch=END,
                    candles={a:(Decimal("0.00000012"),Decimal("0.00000014"))})
        self.assertIn("Rp0,00000014",msg)
        self.assertIn("🟢 1m",msg)
        msg=format_pitiden_premium(c,[],source_kind="WS3",now_epoch=END,
                    candles={a:(Decimal("0.00000014"),Decimal("0.00000014"))})
        self.assertIn("⚪ 1m",msg)
        self.assertIn("⚫ 5m",msg)

    def test_existing_ohlc_archive_batch_and_row_integrity(self):
        with TemporaryDirectory() as td:
            source=Path(td)/"decisions.sqlite3"
            _,start,stop=next(interval_windows(END))
            with sqlite3.connect(source) as db:
                db.execute("CREATE TABLE public_ohlc_1m(pair TEXT,"
                           "start_epoch INTEGER,open TEXT,high TEXT,low TEXT,"
                           "close TEXT,volume TEXT,row_digest TEXT)")
                db.execute("CREATE TABLE public_ohlc_batches(pair TEXT,"
                           "from_epoch INTEGER,to_epoch INTEGER,count INTEGER,"
                           "observed_epoch INTEGER,exchange_epoch INTEGER,"
                           "batch_digest TEXT,provenance TEXT)")
                for stamp,digest_ok in [(start,True),(start-60,False)]:
                    values=("100","115","99","110","123")
                    digest=sha256(",".join((str(stamp),*values)).encode()).hexdigest()
                    db.execute("INSERT INTO public_ohlc_1m VALUES(?,?,?,?,?,?,?,?)",
                               ("ondo_idr",stamp,*values,
                                digest if digest_ok else "0"*64))
                db.execute("INSERT INTO public_ohlc_batches VALUES(?,?,?,?,?,?,?,?)",
                           ("ondo_idr",stop-90*60,stop,90,stop,stop,"local",
                            "INDODAX_PUBLIC_OHLC_V2_UNATTESTED"))
            source.chmod(0o600)
            candles=read_closed_candles(candidate(),db_path=source,now_epoch=END)
            self.assertEqual(candles,{start:(Decimal("100"),Decimal("110"))})
            with sqlite3.connect(source) as db:
                db.execute("UPDATE public_ohlc_batches SET observed_epoch=?,"
                           "exchange_epoch=?", (END+1,END+1))
            self.assertEqual(read_closed_candles(candidate(),db_path=source,
                                                  now_epoch=END),{})

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
