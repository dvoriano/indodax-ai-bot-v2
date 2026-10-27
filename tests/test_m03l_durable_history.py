"""M03-L scoped immutable public research OHLC archive, fail-closed.

Fixture HTTP only. Does not call Sumopod or exchange private/order API.
"""
from __future__ import annotations

from dataclasses import replace
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sqlite3
import unittest
from unittest.mock import patch

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.public_market import PublicDataError
from test_m03k_public_history_research import PublicHistoryFixture


class M03LDurableHistoryTests(unittest.TestCase):
    def test_two_successive_bounded_backfills_make_two_days_contiguous(self):
        with TemporaryDirectory() as td:
            db=Path(td)/"research.sqlite3"
            fixture=PublicHistoryFixture()
            app=DailyScalpingOrchestrator(Settings.from_env({}),DecisionJournal(db))
            first=app.run_public_history_archive_cycle(
                "m03l-day1",market=fixture.market(),minutes=360,windows=4
            )
            self.assertEqual(first.reason,"PUBLIC_OHLC_ARCHIVE_UNATTESTED_NO_TRADE")
            self.assertEqual(first.research_archive["windows_committed_this_call"],4)
            self.assertEqual(first.research_archive["latest_contiguous_1m_rows"],1440)
            self.assertEqual(first.research_archive["archive_summary"]["stored_1m_rows"],1440)
            self.assertEqual(first.research_archive["latest_contiguous_research"]["status"],
                             "HOLDOUT_DESCRIPTIVE_ONLY_NOT_VALIDATED")
            self.assertEqual(first.research_archive["max_public_get_estimate"],12)
            self.assertEqual(len(fixture.paths),12)
            second=app.run_public_history_archive_cycle(
                "m03l-day2",market=fixture.market(),minutes=360,windows=4
            )
            self.assertEqual(second.research_archive["latest_contiguous_1m_rows"],2880)
            self.assertEqual(second.research_archive["archive_summary"]["stored_1m_rows"],2880)
            self.assertEqual(second.research_archive["archive_summary"]["immutable_source_window_count"],8)
            self.assertEqual(second.research_archive["latest_contiguous_research"]["closed_1m_rows_visible"],2880)
            self.assertFalse(second.research_archive["latest_contiguous_research"]["out_of_sample_validated"])
            self.assertFalse(second.research_archive["g1_verified"])
            self.assertFalse(second.execution_allowed)
            self.assertEqual(second.final_decision.value,"REJECT")
            self.assertEqual(app.journal.count(),2)

    def test_restarting_existing_journal_resumes_oldest_not_current(self):
        with TemporaryDirectory() as td:
            db=Path(td)/"journal.sqlite3"
            one=DecisionJournal(db)
            previous=PublicHistoryFixture().market().closed_1m_research_history(minutes=360)
            row=one.archive_public_ohlc_history(previous)
            two=DecisionJournal(db)
            self.assertEqual(two.earliest_public_ohlc_epoch("btc_idr"),previous.from_epoch)
            app=DailyScalpingOrchestrator(Settings.from_env({}),two)
            out=app.run_public_history_archive_cycle(
                "resume",minutes=360,windows=1,market=PublicHistoryFixture().market()
            )
            self.assertEqual(out.research_archive["individual_batches"][0]["to_epoch"],
                             previous.from_epoch)
            self.assertEqual(out.research_archive["archive_summary"]["stored_1m_rows"],720)
            self.assertEqual(out.research_archive["latest_contiguous_1m_rows"],720)

    def test_replay_identical_window_is_deduplicated_and_not_rewritten(self):
        with TemporaryDirectory() as td:
            journal=DecisionJournal(Path(td)/"db.sqlite3")
            source=PublicHistoryFixture().market().closed_1m_research_history(minutes=180)
            first=journal.archive_public_ohlc_history(source)
            replay=journal.archive_public_ohlc_history(source)
            self.assertEqual(first["new_rows_committed"],180)
            self.assertEqual(replay["new_rows_committed"],0)
            self.assertEqual(replay["repeated_rows_unchanged"],180)
            self.assertEqual(journal.public_ohlc_archive_summary("btc_idr")["immutable_source_window_count"],1)

    def test_changed_same_minute_data_rolls_back_entire_batch(self):
        from decimal import Decimal
        with TemporaryDirectory() as td:
            journal=DecisionJournal(Path(td)/"db.sqlite3")
            source=PublicHistoryFixture().market().closed_1m_research_history(minutes=180)
            journal.archive_public_ohlc_history(source)
            modified=list(source.candles)
            modified[100]=replace(modified[100], volume=Decimal("1000"))
            with self.assertRaisesRegex(PublicDataError,"RESEARCH_ARCHIVE_BATCH_CONFLICT"):
                journal.archive_public_ohlc_history(replace(source,candles=tuple(modified)))
            self.assertEqual(journal.public_ohlc_archive_summary("btc_idr")["stored_1m_rows"],180)

    def test_overlapping_mismatched_candle_conflicts_atomically(self):
        from decimal import Decimal
        with TemporaryDirectory() as td:
            journal=DecisionJournal(Path(td)/"db.sqlite3")
            first=PublicHistoryFixture().market().closed_1m_research_history(minutes=180)
            journal.archive_public_ohlc_history(first)
            second=PublicHistoryFixture().market().closed_1m_research_history(
                minutes=180,end_epoch=first.to_epoch-60
            )
            # This adjacent-shift batch overlaps previous archive in 179 rows;
            # changes must be rejected without inserting its one new oldest row.
            arr=list(second.candles)
            arr[-4]=replace(arr[-4],volume=Decimal("99"))
            with self.assertRaisesRegex(PublicDataError,"RESEARCH_ARCHIVE_CANDLE_CONFLICT"):
                journal.archive_public_ohlc_history(replace(second,candles=tuple(arr)))
            self.assertEqual(journal.public_ohlc_archive_summary("btc_idr")["stored_1m_rows"],180)

    def test_missing_second_window_stops_without_erasing_first_commit(self):
        class FailSecondHistory(PublicHistoryFixture):
            def __init__(self):
                super().__init__()
                self.hist_count=0
            def fetch(self,path):
                obj=super().fetch(path)
                if path.startswith("/tradingview/history_v2?"):
                    self.hist_count+=1
                    if self.hist_count==2:
                        return obj[:-1]
                return obj
        with TemporaryDirectory() as td:
            app=DailyScalpingOrchestrator(
                Settings.from_env({}),DecisionJournal(Path(td)/"db.sqlite3")
            )
            result=app.run_public_history_archive_cycle(
                "gap",market=FailSecondHistory().market(),windows=3
            )
            report=result.research_archive
            self.assertEqual(report["status"],"PUBLIC_ARCHIVE_BACKFILL_INCOMPLETE")
            self.assertEqual(report["windows_committed_this_call"],1)
            self.assertEqual(report["archive_summary"]["stored_1m_rows"],360)
            self.assertEqual(report["latest_contiguous_1m_rows"],360)
            self.assertIn("RESEARCH_HISTORY_1M_INCOMPLETE",report["source_failure"])
            self.assertFalse(result.execution_allowed)

    def test_disconnected_batches_not_falsely_joined(self):
        with TemporaryDirectory() as td:
            journal=DecisionJournal(Path(td)/"db.sqlite3")
            market=PublicHistoryFixture().market()
            newer=market.closed_1m_research_history(minutes=180)
            older=market.closed_1m_research_history(
                minutes=180,end_epoch=newer.from_epoch-60,
            )
            journal.archive_public_ohlc_history(newer)
            journal.archive_public_ohlc_history(older)
            self.assertEqual(journal.public_ohlc_archive_summary("btc_idr")["stored_1m_rows"],360)
            joined=journal.recent_contiguous_public_ohlc_history("btc_idr")
            self.assertEqual(len(joined.candles),180)
            self.assertEqual(joined.to_epoch,newer.to_epoch)

    def test_corrupted_row_digest_fails_closed_on_research_read(self):
        with TemporaryDirectory() as td:
            filename=Path(td)/"db.sqlite3"
            journal=DecisionJournal(filename)
            source=PublicHistoryFixture().market().closed_1m_research_history(minutes=180)
            journal.archive_public_ohlc_history(source)
            with sqlite3.connect(filename) as c:
                c.execute(
                    "UPDATE public_ohlc_1m SET volume='99999' "
                    "WHERE pair='btc_idr' AND start_epoch=?",
                    (source.candles[-1].start,)
                )
            with self.assertRaisesRegex(PublicDataError,"RESEARCH_ARCHIVE_CANDLE_DIGEST_MISMATCH"):
                journal.recent_contiguous_public_ohlc_history("btc_idr")

    def test_cli_persists_nontrade_data_and_keeps_final_reject(self):
        with TemporaryDirectory() as td:
            output=StringIO()
            with (
                patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket",
                      return_value=PublicHistoryFixture().market()),
                patch.dict("os.environ",{
                    "INDODAX_TRADING_ENABLED":"NO",
                    "INDODAX_ALLOW_LIVE_EXECUTION":"NO",
                    "INDODAX_EXECUTION_MODE":"DRY_RUN",
                },clear=True),
                redirect_stdout(output),
            ):
                code=main([
                    "--public-history-archive-once",
                    "--history-minutes","180",
                    "--history-windows","2",
                    "--pair","btc_idr","--db",str(Path(td)/"research.sqlite3")
                ])
            self.assertEqual(code,0)
            data=json.loads(output.getvalue())
            report=data["public_history_archive"]
            self.assertEqual(report["windows_committed_this_call"],2)
            self.assertEqual(report["archive_summary"]["stored_1m_rows"],360)
            self.assertFalse(report["g1_verified"])
            self.assertFalse(data["research_is_trade_signal"])
            self.assertFalse(data["live_trading_authorized"])
            self.assertFalse(data["execution_allowed"])
            self.assertEqual(data["final_decision"],"REJECT")

    def test_invalid_window_count_rejected_before_market_calls(self):
        class NeverTouch:
            pass
        with TemporaryDirectory() as td:
            app=DailyScalpingOrchestrator(
                Settings.from_env({}),DecisionJournal(Path(td)/"db.sqlite3")
            )
            with self.assertRaisesRegex(ValueError,"INVALID_PUBLIC_ARCHIVE_OPTIONS"):
                app.run_public_history_archive_cycle(
                    "invalid",market=NeverTouch(),windows=5
                )


if __name__ == "__main__":
    unittest.main()
