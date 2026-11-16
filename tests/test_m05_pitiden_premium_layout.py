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
    read_closed_candles, interval_windows, FRAMES, count_green,
    eligible_premium_watch, read_native_previous_24h,
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
    def test_exchange_clock_windows_and_rolling_24h(self):
        wib=ZoneInfo("Asia/Jakarta")
        end=int(datetime(2026,11,16,10,43,27,tzinfo=wib).timestamp())
        def hhmm(t):
            return datetime.fromtimestamp(t,wib).strftime("%H:%M:%S")
        self.assertEqual([(tf,hhmm(a),hhmm(b)) for tf,a,b in
                          interval_windows(end)], [
            ("1m","10:42:00","10:43:00"),
            ("5m","10:35:00","10:40:00"),
            ("10m","10:30:00","10:40:00"),
            ("15m","10:15:00","10:30:00"),
            ("30m","10:00:00","10:30:00"),
            ("1h","09:00:00","10:00:00"),
            ("4h","03:00:00","07:00:00"),
            ("24h","10:43:27","10:43:27"),
        ])
        midnight=int(datetime(2026,11,16,tzinfo=wib).timestamp())
        row=build_timeframes(candidate(observed_epoch=midnight),[],
                             previous_24h=Decimal("5268"))[-1]
        self.assertEqual(row["start_epoch"],midnight-86400)
        self.assertEqual(row["close_price"],Decimal("5739"))
        self.assertGreater(row["change"],0)

    def test_independent_signs_and_close_not_open(self):
        end=int(datetime(2026,11,16,10,43,27,
                         tzinfo=ZoneInfo("Asia/Jakarta")).timestamp())
        c=candidate(observed_epoch=end,reference_epoch=end-245)
        lookup=list(interval_windows(end))
        candles={}
        for _,a,b in lookup[:-1]:
            for t in range(a,b,60):
                candles[t]=(Decimal("5700"),Decimal("5710"))
        for tf,a,b in lookup[:-1]:
            if tf=="1m":
                candles[a]=(Decimal("5700"),Decimal("5730"))
            elif tf=="5m":
                candles[a]=(Decimal("5750"),Decimal("5710"))
            elif tf=="10m":
                candles[a]=(Decimal("5770"),Decimal("5710"))
        rows=build_timeframes(c,[],candles=candles,previous_24h=Decimal("5268"))
        self.assertEqual([r["tf"] for r in rows],
                         ["1m","5m","10m","15m","30m","1h","4h","24h"])
        self.assertEqual(count_green(rows),6)
        self.assertTrue(eligible_premium_watch(c,rows))
        self.assertEqual(rows[1]["close_price"],Decimal("5710"))
        self.assertEqual(rows[2]["close_price"],Decimal("5710"))
        self.assertEqual(rows[-1]["close_price"],Decimal("5739"))
        self.assertTrue(all(r["volume"] is None for r in rows[:-1]))
        self.assertEqual(rows[-1]["volume"],Decimal("461460000"))
        msg=format_pitiden_premium(c,[],source_kind="WS3",now_epoch=end,rows=rows)
        for phrase in ("🟢 1m","🔴 10m","🟢 4h","24h","461,46jt",
                       "Green 6/8","Vol 24h rolling Rp","Not BUY"):
            self.assertIn(phrase,msg)
        self.assertLessEqual(len(msg.splitlines()),15)
        # Telegram <pre> renders a large gray code block and COPY CODE.
        # Keep the eight source rows and compact alignment without it.
        self.assertNotIn("<pre",msg)
        self.assertNotIn("</pre>",msg)
        self.assertEqual(msg.count("<code>"),9)
        self.assertEqual(msg.count("</code>"),9)
        self.assertIn("TF   CHANGE      PRICE   VOL(Rp)",msg)
        self.assertIn("<b>ONDO/IDR · Rp5.739</b>",msg)
        self.assertIn("<b>WATCH</b>",msg)

    def test_four_green_gate_boundary_unknown_flat_volume_and_duplicates(self):
        def rows(values):
            return [{"tf":tf,"change":v} for (tf,_),v in zip(FRAMES,values)]
        for n in range(9):
            r=rows([Decimal("0.01")]*n+[None]*(8-n))
            self.assertEqual(count_green(r),n)
            self.assertEqual(eligible_premium_watch(candidate(),r),n>=4)
        r=rows([Decimal("1")]*3+[Decimal(0),Decimal("-1"),None,None,None])
        self.assertFalse(eligible_premium_watch(candidate(),r))
        r=rows([Decimal("1")]*4+[Decimal(0),Decimal("-1"),None,None])
        self.assertTrue(eligible_premium_watch(candidate(),r))
        r=rows([Decimal("1")]*8)
        self.assertFalse(eligible_premium_watch(candidate(volume_24h_idr="399999999"),r))
        self.assertTrue(eligible_premium_watch(candidate(volume_24h_idr="400000000"),r))
        self.assertFalse(eligible_premium_watch(candidate(volume_24h_idr="NaN"),r))
        r[7]["tf"]="1m"
        self.assertEqual(count_green(r),0)

    def test_sample_edges_approximate_no_cross_boundary(self):
        c=candidate()
        _,start,stop=next(interval_windows(END))
        refs=[(start-1,Decimal("1")),(start+2,Decimal("5600")),
              (stop-1,Decimal("5700")),(stop,Decimal("9999"))]
        rows=build_timeframes(c,refs)
        self.assertTrue(rows[0]["approx"])
        self.assertEqual(rows[0]["close_price"],Decimal("5700"))
        self.assertIsNone(rows[-1]["opening_price"])
        self.assertEqual(rows[-1]["close_price"],Decimal("5739"))
        gap=build_timeframes(c,[(stop-1,Decimal("5700"))])[0]
        self.assertIsNone(gap["change"])
        self.assertEqual(gap["close_price"],Decimal("5700"))
        msg=format_pitiden_premium(c,refs,source_kind="WS3",now_epoch=END)
        self.assertIn("1m~",msg)
        self.assertIn("Trigger +1,77% / 4m05s",msg)

    def test_sparse_minute_quotes_at_49_seconds_can_compare_edges(self):
        c=candidate()
        _,start,stop=next(interval_windows(END))
        prior=(start-11,Decimal("100"))
        last=(stop-11,Decimal("103"))
        row=build_timeframes(c,[prior,last])[0]
        self.assertTrue(row["approx"])
        self.assertEqual(row["opening_price"],Decimal("100"))
        self.assertEqual(row["close_price"],Decimal("103"))
        self.assertEqual(row["change"],Decimal("3"))
        for refs in ([(start-16,Decimal("100")),last],
                     [prior,(stop,Decimal("103"))], [last]):
            self.assertIsNone(build_timeframes(c,refs)[0]["change"])

    def test_native_previous_preferred_and_history_fallback_marked(self):
        c=candidate()
        refs=[(END-86400-10,Decimal("5000")),
              (END-86400+1,Decimal("1"))]
        row=build_timeframes(c,refs,previous_24h=Decimal("5268"))[-1]
        self.assertEqual(row["opening_price"],Decimal("5268"))
        self.assertFalse(row["approx"])
        row=build_timeframes(c,refs)[-1]
        self.assertEqual(row["opening_price"],Decimal("5000"))
        self.assertTrue(row["approx"])

    def test_read_native_snapshot_exact_identity_and_asof(self):
        with TemporaryDirectory() as td:
            p=Path(td)/"observations.sqlite3"
            with sqlite3.connect(p) as db:
                db.execute("CREATE TABLE ws3_source_evidence(pair TEXT,"
                           "minute_epoch INTEGER,source_exchange_epoch INTEGER,"
                           "last_idr TEXT,rolling_24h_idr TEXT,public_offset INTEGER,"
                           "source_status TEXT,previous_24h_idr TEXT)")
                db.execute("CREATE TABLE ws3_source_cycles(minute_epoch INTEGER,"
                           "recorded_local_epoch INTEGER)")
                db.execute("INSERT INTO ws3_source_evidence VALUES(?,?,?,?,?,?,?,?)",
                           ("ondo_idr",END,END,"5739","461460000",1,
                            "ELIGIBLE_SOURCE_OBSERVATION","5268"))
                db.execute("INSERT INTO ws3_source_cycles VALUES(?,?)",(END,END))
            p.chmod(0o600)
            def read(c=candidate(),now=END):
                return read_native_previous_24h(c,evidence_path=p,now_epoch=now)
            self.assertEqual(read(),Decimal("5268"))
            self.assertIsNone(read(candidate(latest_price_idr="5740")))
            self.assertIsNone(read(candidate(volume_24h_idr="1")))
            self.assertIsNone(read(now=END-1))

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
