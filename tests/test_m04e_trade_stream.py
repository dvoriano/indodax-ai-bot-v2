"""M04-E deterministic public WS trade-activity cursor, recovery and no-order."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO
import json
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.trade_stream import (
    PublicTradeStreamJournal, StreamEvidenceError, channel_for_pair,
)


def ack(offset=100, epoch="1633996541", publications=None):
    return {"id":2,"result":{
        "recoverable":True, "epoch":epoch, "offset":offset,
        **({"publications":publications} if publications is not None else {}),
    }}


def row(seq, pair="btcidr"):
    return [pair, 1635274052, seq, "buy", 881991000,
            "29740", "0.00003372"]


def pub(offset, seq=None, pair="btc_idr"):
    rows=[row(offset if seq is None else seq, pair=pair.replace("_",""))]
    return {"result":{"channel":channel_for_pair(pair),
                      "data":{"data":rows,"offset":offset}}}


def replay_pub(offset):
    return {"data":[row(offset)],"offset":offset}


class M04EPublicStreamTests(unittest.TestCase):
    def test_subscribe_ack_trade_persist_and_restart_recover_offset(self):
        with TemporaryDirectory() as td:
            path=Path(td)/"ws.sqlite3"
            j=PublicTradeStreamJournal(path)
            self.assertEqual(j.subscription("btc_idr")["params"],
                             {"channel":"market:trade-activity-btcidr"})
            status=j.acknowledge("btc_idr",ack())
            self.assertEqual(status["stream_status"],"BASELINE_ONLY")
            self.assertFalse(status["g1_receipt_issuance_allowed"])
            status=j.ingest("btc_idr",pub(101))
            self.assertEqual(status["last_publication_offset"],101)
            self.assertEqual(status["persisted_publications"],1)
            self.assertTrue(status["observed_stream_continuity"])
            self.assertFalse(status["indodax_trade_tape_completeness_proven"])
            j.mark_disconnected("btc_idr")
            restarted=PublicTradeStreamJournal(path)
            self.assertEqual(restarted.subscription("btc_idr")["params"]["offset"],101)
            self.assertEqual(restarted.subscription("btc_idr")["params"]["recover"],True)
            restarted.acknowledge("btc_idr",ack(102,publications=[replay_pub(102)]))
            status=restarted.status("btc_idr")
            self.assertEqual(status["last_publication_offset"],102)
            self.assertEqual(status["persisted_publications"],2)
            self.assertFalse(status["source_authenticated_for_g1"])

    def test_publication_jump_sets_persistent_gap_and_never_advances(self):
        with TemporaryDirectory() as td:
            j=PublicTradeStreamJournal(Path(td)/"ws.sqlite3")
            j.acknowledge("btc_idr",ack())
            gap=j.ingest("btc_idr",pub(103))
            self.assertEqual(gap["stream_status"],"GAP")
            self.assertEqual(gap["last_publication_offset"],100)
            self.assertFalse(gap["observed_stream_continuity"])
            with self.assertRaisesRegex(StreamEvidenceError,"WS_STREAM_GAP"):
                j.ingest("btc_idr",pub(104))
            self.assertEqual(j.read_count("btc_idr"),0)
            self.assertEqual(j.mark_disconnected("btc_idr")["stream_status"], "GAP")

    def test_recover_gap_requires_exactly_consecutive_publications(self):
        with TemporaryDirectory() as td:
            j=PublicTradeStreamJournal(Path(td)/"ws.sqlite3")
            j.acknowledge("btc_idr",ack())
            j.ingest("btc_idr",pub(103))
            status=j.acknowledge("btc_idr",ack(103,publications=[
                replay_pub(101),replay_pub(102),replay_pub(103)
            ]))
            self.assertEqual(status["stream_status"],"CONTIGUOUS_OBSERVED")
            self.assertEqual(status["last_publication_offset"],103)
            self.assertEqual(status["persisted_publications"],3)
            self.assertFalse(status["g2_provider_call_allowed"])

    def test_incomplete_replay_does_not_skip_missing_publication(self):
        with TemporaryDirectory() as td:
            j=PublicTradeStreamJournal(Path(td)/"ws.sqlite3")
            j.acknowledge("btc_idr",ack())
            j.ingest("btc_idr",pub(104))
            recovered=j.acknowledge("btc_idr",ack(104,publications=[replay_pub(101)]))
            self.assertEqual(recovered["stream_status"],"GAP")
            self.assertEqual(recovered["last_publication_offset"],101)
            self.assertFalse(recovered["g1_receipt_issuance_allowed"])

    def test_duplicate_and_conflicting_replay_rejected_without_duplication(self):
        with TemporaryDirectory() as td:
            j=PublicTradeStreamJournal(Path(td)/"ws.sqlite3")
            j.acknowledge("btc_idr",ack())
            j.ingest("btc_idr",pub(101))
            j.ingest("btc_idr",pub(101))
            self.assertEqual(j.read_count("btc_idr"),1)
            changed=pub(101)
            changed["result"]["data"]["data"][0][5]="99999"
            with self.assertRaisesRegex(StreamEvidenceError,"WS_OFFSET_REPLAY_OR_COLLISION"):
                j.ingest("btc_idr",changed)
            self.assertEqual(j.read_count("btc_idr"),1)

    def test_changed_epoch_is_not_continuity_proof(self):
        with TemporaryDirectory() as td:
            j=PublicTradeStreamJournal(Path(td)/"ws.sqlite3")
            j.acknowledge("btc_idr",ack())
            j.ingest("btc_idr",pub(101))
            changed=j.acknowledge("btc_idr",ack(5,epoch="1633996542"))
            self.assertEqual(changed["stream_status"],"GAP")
            self.assertEqual(changed["last_publication_offset"],101)
            self.assertFalse(changed["source_authenticated_for_g1"])

    def test_foreign_pair_bad_rows_and_offset_rejected_before_cursor_advance(self):
        with TemporaryDirectory() as td:
            j=PublicTradeStreamJournal(Path(td)/"ws.sqlite3")
            j.acknowledge("btc_idr",ack())
            with self.assertRaisesRegex(StreamEvidenceError,"CHANNEL_MISMATCH"):
                j.ingest("btc_idr",pub(101,pair="eth_idr"))
            bad=pub(101)
            bad["result"]["data"]["data"][0][4]="NaN"
            with self.assertRaisesRegex(StreamEvidenceError,"PRICE_INVALID"):
                j.ingest("btc_idr",bad)
            bad=pub(101)
            bad["result"]["data"]["data"][0][2]=-1
            with self.assertRaisesRegex(StreamEvidenceError,"TRADE_SEQUENCE_INVALID"):
                j.ingest("btc_idr",bad)
            self.assertEqual(j.status("btc_idr")["last_publication_offset"],100)
            self.assertEqual(j.read_count("btc_idr"),0)

    def test_invalid_subscribe_reply_fails_without_creating_state(self):
        with TemporaryDirectory() as td:
            j=PublicTradeStreamJournal(Path(td)/"ws.sqlite3")
            with self.assertRaisesRegex(StreamEvidenceError,"WS_RECOVERY_NOT_SUPPORTED"):
                j.acknowledge("btc_idr",{"id":2,"result":{"epoch":"123","offset":20,
                                                          "recoverable":False}})
            self.assertEqual(j.status("btc_idr")["stream_status"],"UNSUBSCRIBED")
            with self.assertRaisesRegex(StreamEvidenceError,"INVALID_PUBLIC_STREAM_PAIR"):
                j.subscription("../btc")

    def test_server_recovered_false_persists_gap_even_at_same_offset(self):
        # Server says a requested recovery failed. recoverable=True only
        # identifies stream capability, not successful historical replay.
        with TemporaryDirectory() as td:
            j = PublicTradeStreamJournal(Path(td) / "ws.sqlite3")
            j.acknowledge("btc_idr", ack())
            j.ingest("btc_idr", pub(101))
            j.mark_disconnected("btc_idr")
            reply = ack(101)
            reply["result"]["recovered"] = False
            state = j.acknowledge("btc_idr", reply)
            self.assertEqual(state["stream_status"], "GAP")
            self.assertEqual(state["last_publication_offset"], 101)
            self.assertEqual(j.read_count("btc_idr"), 1)
            self.assertNotIn("recover", j.subscription("btc_idr")["params"])
            self.assertFalse(state["indodax_trade_tape_completeness_proven"])
            # The existing bounded rebaseline keeps a durable discontinuity
            # without deleting earlier observations or claiming G1.
            fresh = j.acknowledge("btc_idr", ack(103), fresh_segment=True)
            self.assertEqual(fresh["stream_status"], "BASELINE_ONLY")
            self.assertEqual(fresh["unrecoverable_gap_rebaseline_count"], 1)
            self.assertEqual(j.read_count("btc_idr"), 1)
            self.assertFalse(fresh["source_authenticated_for_g1"])

    def test_server_recovered_false_rejects_even_complete_looking_replay(self):
        with TemporaryDirectory() as td:
            j = PublicTradeStreamJournal(Path(td) / "ws.sqlite3")
            j.acknowledge("btc_idr", ack())
            j.ingest("btc_idr", pub(101))
            j.mark_disconnected("btc_idr")
            reply = ack(102, publications=[replay_pub(102)])
            reply["result"]["recovered"] = False
            state = j.acknowledge("btc_idr", reply)
            self.assertEqual(state["stream_status"], "GAP")
            self.assertEqual(state["last_publication_offset"], 101)
            self.assertEqual(j.read_count("btc_idr"), 1)
            self.assertFalse(state["g2_provider_call_allowed"])

    def test_explicit_recovered_true_preserves_verified_cursor_only(self):
        with TemporaryDirectory() as td:
            j = PublicTradeStreamJournal(Path(td) / "ws.sqlite3")
            j.acknowledge("btc_idr", ack())
            j.ingest("btc_idr", pub(101))
            j.mark_disconnected("btc_idr")
            reply = ack(102, publications=[replay_pub(102)])
            reply["result"]["recovered"] = True
            state = j.acknowledge("btc_idr", reply)
            self.assertEqual(state["stream_status"], "CONTIGUOUS_OBSERVED")
            self.assertEqual(state["last_publication_offset"], 102)
            self.assertEqual(j.read_count("btc_idr"), 2)
            self.assertFalse(state["indodax_trade_tape_completeness_proven"])
            self.assertFalse(state["g1_receipt_issuance_allowed"])

    def test_malformed_recovered_flag_fails_before_state_change(self):
        with TemporaryDirectory() as td:
            j = PublicTradeStreamJournal(Path(td) / "ws.sqlite3")
            j.acknowledge("btc_idr", ack())
            j.ingest("btc_idr", pub(101))
            j.mark_disconnected("btc_idr")
            reply = ack(102, publications=[replay_pub(102)])
            reply["result"]["recovered"] = "false"
            with self.assertRaisesRegex(StreamEvidenceError,
                                        "INVALID_WS_RECOVERED_FLAG"):
                j.acknowledge("btc_idr", reply)
            state = j.status("btc_idr")
            self.assertEqual(state["stream_status"], "DISCONNECTED")
            self.assertEqual(state["last_publication_offset"], 101)
            self.assertEqual(j.read_count("btc_idr"), 1)

    def test_public_ws_mainpath_always_final_reject_with_no_ai_or_order(self):
        with TemporaryDirectory() as td:
            buf=StringIO()
            db=str(Path(td)/"safe.sqlite3")
            with patch("indodax_ai_bot.orchestrator.probe_official_public_ws",
                       return_value={"pair":"btc_idr","persisted_publications":3,
                                     "g1_receipt_issuance_allowed":False,
                                     "g2_provider_call_allowed":False,
                                     "execution_allowed":False}):
                with redirect_stdout(buf):
                    rc=main(["--public-ws-once","--db",db,"--pair","btc_idr",
                             "--stream-seconds","3"])
            out=json.loads(buf.getvalue())
            self.assertEqual(rc,0)
            self.assertEqual(out["final_decision"],"REJECT")
            self.assertEqual(out["reason"],"PUBLIC_WS_TRADE_SAMPLE_UNATTESTED")
            self.assertFalse(out["execution_allowed"])
            self.assertFalse(out["live_trading_authorized"])
            self.assertFalse(out["public_ws"]["g2_provider_call_allowed"])

    def test_missing_optional_ws_client_fails_closed(self):
        with TemporaryDirectory() as td:
            from indodax_ai_bot.trade_stream import probe_official_public_ws
            from unittest.mock import patch
            import builtins
            j=PublicTradeStreamJournal(Path(td)/"safe.sqlite3")
            old=builtins.__import__
            def fake_import(name,*args,**kwargs):
                if name=="websocket":
                    raise ModuleNotFoundError("optional package missing")
                return old(name,*args,**kwargs)
            with patch("builtins.__import__", side_effect=fake_import):
                with self.assertRaisesRegex(StreamEvidenceError,
                                            "PUBLIC_WS_CLIENT_PACKAGE_MISSING"):
                    probe_official_public_ws(j, seconds=3)
            self.assertEqual(j.status("btc_idr")["stream_status"],"UNSUBSCRIBED")


if __name__ == "__main__":
    unittest.main()
