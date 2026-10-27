"""M04-P: explicit discontinuous PUBLIC WS rebaseline, historical rows retained.

Target only the changed replay/GAP handling and one bounded recovery
inside the EXISTING collector; never source trust, AI or order authority.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from indodax_ai_bot.trade_stream import (
    PublicTradeStreamJournal, StreamEvidenceError, collect_official_public_ws,
)
from test_m04e_trade_stream import ack, pub, replay_pub


def fake_public_session(journal, *, pair, sessions_seen):
    # Mirror the validated public probe result shape, with no G1/trade power.
    j=journal.status(pair)
    return {
        "subscription_acknowledged":True,
        "protocol_heartbeat_confirmed":True,
        "stream_status_during_probe":"CONTIGUOUS_OBSERVED",
        "stream_status":"DISCONNECTED",
        "resume_requested":False,
        "resume_requested_offset":None,
        "recovery_diagnostic":"INITIAL_SUBSCRIPTION",
        "server_replayed_publication_frames":0,
        "recovered_publications_newly_persisted":0,
        "live_publications_newly_persisted":1,
        "last_publication_offset":j["last_publication_offset"],
        "persisted_publications":j["persisted_publications"],
        "g1_receipt_issuance_allowed":False,
        "g2_provider_call_allowed":False,
        "execution_allowed":False,
    }


class M04PDiscontinuousPublicWSRecovery(unittest.TestCase):
    def test_broken_replay_commits_gap_instead_of_sqlite_rollback(self):
        with TemporaryDirectory() as td:
            j=PublicTradeStreamJournal(Path(td)/"journal.sqlite3")
            j.acknowledge("btc_idr",ack(100))
            j.mark_disconnected("btc_idr")
            report=j.acknowledge("btc_idr",ack(
                104,publications=[replay_pub(101),replay_pub(103),
                                  replay_pub(104)],
            ))
            self.assertEqual(report["stream_status"],"GAP")
            self.assertEqual(report["last_publication_offset"],101)
            self.assertEqual(report["persisted_publications"],1)
            self.assertEqual(j.subscription("btc_idr")["params"],
                             {"channel":"market:trade-activity-btcidr"})
            self.assertEqual(j.mark_disconnected("btc_idr")["stream_status"],
                             "GAP")
            self.assertFalse(report["source_authenticated_for_g1"])

    def test_fresh_baseline_records_gap_without_deleting_earlier_trade(self):
        with TemporaryDirectory() as td:
            db=Path(td)/"journal.sqlite3"
            j=PublicTradeStreamJournal(db)
            j.acknowledge("btc_idr",ack(100))
            j.ingest("btc_idr",pub(101))
            j.mark_disconnected("btc_idr")
            old=j.acknowledge("btc_idr",ack(106,
                publications=[replay_pub(103),replay_pub(106)]))
            self.assertEqual(old["stream_status"],"GAP")
            new=j.acknowledge("btc_idr",ack(110),fresh_segment=True)
            self.assertEqual(new["stream_status"],"BASELINE_ONLY")
            self.assertEqual(new["last_publication_offset"],110)
            self.assertEqual(new["unrecoverable_gap_rebaseline_count"],1)
            j.ingest("btc_idr",pub(111))
            self.assertEqual(j.mark_disconnected("btc_idr")["stream_status"],
                             "DISCONNECTED")
            with sqlite3.connect(db) as con:
                offsets=[x[0] for x in con.execute(
                    "SELECT offset FROM ws_trade_publications ORDER BY offset"
                )]
                incident=con.execute(
                    "SELECT prior_offset,new_offset,reason FROM "
                    "ws_stream_discontinuities WHERE pair='btc_idr'"
                ).fetchone()
            self.assertEqual(offsets,[101,111])
            self.assertEqual(incident[0],101)
            self.assertEqual(incident[1],110)
            self.assertEqual(incident[2],"UNRECOVERABLE_WS_GAP_REBASELINED")
            self.assertFalse(j.status("btc_idr")["g2_provider_call_allowed"])
            self.assertFalse(j.status("btc_idr")["execution_allowed"])

    def test_same_epoch_stale_fresh_ack_is_rejected_without_destructive_reset(self):
        with TemporaryDirectory() as td:
            db=Path(td)/"journal.sqlite3"
            j=PublicTradeStreamJournal(db)
            j.acknowledge("btc_idr",ack(100))
            j.ingest("btc_idr",pub(101))
            j.ingest("btc_idr",pub(104))
            self.assertEqual(j.status("btc_idr")["stream_status"],"GAP")
            with self.assertRaisesRegex(StreamEvidenceError,
                                        "WS_FRESH_BASELINE_NONMONOTONIC"):
                j.acknowledge("btc_idr",ack(101),fresh_segment=True)
            self.assertEqual(j.status("btc_idr")["stream_status"],"GAP")
            self.assertEqual(j.status("btc_idr")["persisted_publications"],1)
            self.assertEqual(j.status("btc_idr")["unrecoverable_gap_rebaseline_count"],0)

    def test_changed_epoch_is_new_labeled_segment_and_archives_old_epoch(self):
        with TemporaryDirectory() as td:
            db=Path(td)/"journal.sqlite3"
            j=PublicTradeStreamJournal(db)
            j.acknowledge("btc_idr",ack(100))
            j.ingest("btc_idr",pub(101))
            j.mark_disconnected("btc_idr")
            self.assertEqual(
                j.acknowledge("btc_idr",ack(3,epoch="1633996542"))["stream_status"],
                "GAP",
            )
            report=j.acknowledge("btc_idr",ack(4,epoch="1633996542"),
                                 fresh_segment=True)
            self.assertEqual(report["stream_epoch"],"1633996542")
            self.assertEqual(report["last_publication_offset"],4)
            self.assertEqual(report["unrecoverable_gap_rebaseline_count"],1)
            self.assertEqual(report["persisted_publications"],1)
            self.assertFalse(report["indodax_trade_tape_completeness_proven"])

    def test_complete_same_epoch_replay_remains_normal_without_gap_marker(self):
        with TemporaryDirectory() as td:
            j=PublicTradeStreamJournal(Path(td)/"journal.sqlite3")
            j.acknowledge("btc_idr",ack(100))
            j.mark_disconnected("btc_idr")
            x=j.acknowledge("btc_idr",ack(102,publications=[
                replay_pub(101),replay_pub(102),
            ]))
            self.assertEqual(x["last_publication_offset"],102)
            self.assertEqual(x["unrecoverable_gap_rebaseline_count"],0)
            self.assertEqual(x["stream_status"],"CONTIGUOUS_OBSERVED")
            self.assertTrue(j.subscription("btc_idr")["params"]["recover"])

    def test_one_bounded_gap_rebaseline_retry_resumes_collection_unattested(self):
        with TemporaryDirectory() as td:
            db=Path(td)/"journal.sqlite3"
            j=PublicTradeStreamJournal(db)
            j.acknowledge("btc_idr",ack(100))
            j.ingest("btc_idr",pub(101))
            j.mark_disconnected("btc_idr")
            calls=[]
            def probe(journal, *, pair, seconds):
                calls.append(len(calls)+1)
                if len(calls)==1:
                    result=journal.acknowledge(pair,ack(105,publications=[
                        replay_pub(102),replay_pub(104),replay_pub(105),
                    ]))
                    self.assertEqual(result["stream_status"],"GAP")
                    raise StreamEvidenceError("WS_RECOVERY_GAP_UNRESOLVED")
                if len(calls)==2:
                    self.assertNotIn("recover",journal.subscription(pair)["params"])
                    journal.acknowledge(pair,ack(110),fresh_segment=True)
                    journal.ingest(pair,pub(111))
                if len(calls)==3:
                    self.assertTrue(journal.subscription(pair)["params"]["recover"])
                    journal.acknowledge(pair,ack(111))
                    journal.ingest(pair,pub(112))
                journal.mark_disconnected(pair)
                return fake_public_session(journal,pair=pair,sessions_seen=len(calls))
            r=collect_official_public_ws(
                j,pair="btc_idr",sessions=2,session_seconds=3,
                pause_seconds=0,probe=probe,sleep=lambda _:None,
            )
            self.assertEqual(r["status"],"PUBLIC_SAMPLE_COLLECTED_UNATTESTED")
            self.assertEqual(len(calls),3)
            self.assertEqual(r["completed_safe_sessions"],2)
            self.assertEqual(r["fresh_retry_after_unrecoverable_gap_count"],1)
            self.assertEqual(r["unrecoverable_gap_rebaseline_count"],1)
            self.assertFalse(r["full_stream_continuity_proven"])
            self.assertFalse(r["g1_receipt_issuance_allowed"])
            self.assertFalse(r["g2_provider_call_allowed"])
            self.assertFalse(r["execution_allowed"])
            self.assertEqual(j.status("btc_idr")["last_publication_offset"],112)

    def test_non_gap_transport_failure_is_not_restarted_or_rebaselined(self):
        with TemporaryDirectory() as td:
            j=PublicTradeStreamJournal(Path(td)/"journal.sqlite3")
            calls=[]
            def probe(*args,**kwargs):
                calls.append(True)
                raise StreamEvidenceError("PUBLIC_WS_AUTH_TIMEOUT")
            result=collect_official_public_ws(
                j,sessions=2,session_seconds=3,pause_seconds=0,
                probe=probe,sleep=lambda _:None,
            )
            self.assertEqual(calls,[True])
            self.assertEqual(result["status"],"REJECT")
            self.assertEqual(result["reason"],
                             "WS_SESSION_REJECTED:PUBLIC_WS_AUTH_TIMEOUT")
            self.assertEqual(result["fresh_retry_after_unrecoverable_gap_count"],0)
            self.assertFalse(result["execution_allowed"])


if __name__=="__main__":
    unittest.main()
