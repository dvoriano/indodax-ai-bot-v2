"""M03 DEEP20 compact SQLite telemetry on the original scheduled CLI path.

Only HTTP transport is mocked. Actual orchestrator, scanner, REST quota,
DecisionJournal and M04 reject path run as production modules.
"""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.journal import DecisionConflictError, DecisionJournal
from indodax_ai_bot.public_market import IndodaxPublicMarket
from test_m03a_scanner import FakeIndodaxAll

TICK = 1800000010
assert (TICK // 60) % 5 == 0 and TICK % 60 == 10


class M03CompactOriginalMainpathTests(unittest.TestCase):
    def test_scheduled_deep20_compact_persists_without_network_or_trade(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            policy_file = root / "private-policy.json"
            policy_file.write_text(json.dumps({
                "schema_version": 1,
                "owner_approved_public_only": True,
                "all_shared_ip_consumers_accounted": True,
                "egress_public_ip": "8.8.8.8",
                "peer_service_names": [
                    "crypto-bot-view.service",
                    "crypto-bot-telegram.service",
                    "hermes-gateway.service",
                ],
                "external_peak_requests_rolling_60s": 0,
                "issued_at_epoch": TICK - 1,
                "expires_at_epoch": TICK + 60,
            }))
            policy_file.chmod(0o600)
            quota = root / "quota.sqlite3"
            db = root / "decisions.sqlite3"
            ws = root / "existing-btc-ws.sqlite3"
            ws.write_bytes(b"TEST_EXISTING_NO_REAL_BTC_TAPE")
            fake = FakeIndodaxAll()
            fake.now = TICK
            for item in fake.tickers.values():
                item["server_time"] = TICK - 1
                item["vol_idr"] = "300000000"

            calls = []
            def http_boundary(_client, path):
                calls.append(path)
                if path.startswith("/tradingview/history_v2?"):
                    # No invented candles: PUBLIC source remains unqualified.
                    return []
                return fake.fetch(path)

            original_init = IndodaxPublicMarket.__init__
            def fixed_clock(client, *args, **kwargs):
                kwargs.setdefault("clock", lambda: TICK)
                return original_init(client, *args, **kwargs)

            flags = {
                "INDODAX_TRADING_ENABLED": "NO",
                "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
                "INDODAX_EXECUTION_MODE": "DRY_RUN",
                "INDODAX_M03_REQUIRE_EGRESS_ADMISSION": "YES",
                "INDODAX_M03_EGRESS_ADMISSION_FILE": str(policy_file),
                "INDODAX_PUBLIC_REST_RATE_DB": str(quota),
            }
            stdout, stderr = StringIO(), StringIO()
            with (
                patch.dict(os.environ, flags),
                patch("indodax_ai_bot.__main__.time.time", return_value=TICK),
                patch.object(IndodaxPublicMarket, "__init__", new=fixed_clock),
                patch.object(IndodaxPublicMarket, "_http_json", autospec=True,
                             side_effect=http_boundary),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                rc = main([
                    "--scheduled-public-scanner-once", "--ws-db", str(ws),
                    "--db", str(db),
                    "--bulk-deep-limit", "20",
                    "--bulk-min-volume-idr", "150000000",
                ])
            self.assertEqual(stderr.getvalue(), "")
            self.assertLess(len(stdout.getvalue().encode()), 4096)
            body = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(body["scheduled_m03_lane"],
                             "DEEP20_EVERY_FIVE_MINUTES")
            self.assertEqual(body["final_decision"], "REJECT")
            self.assertFalse(body["execution_allowed"])
            summary = body["m03_compact_telemetry"]
            self.assertEqual(summary["final_decision"], "REJECT")
            self.assertEqual(summary["lane"], "DEEP20_EVERY_FIVE_MINUTES")
            self.assertFalse(summary["g1_issuer_connected"])
            self.assertFalse(summary["paid_network_allowed"])
            self.assertEqual(body["public_bulk"]["attempted_count"],
                             summary["attempted_count"])
            self.assertEqual(body["ai_shadow"]["completed_roles"], [])
            self.assertGreaterEqual(len(calls), 3)
            self.assertLessEqual(len(calls), 168)

            with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as con:
                decision = con.execute(
                    "SELECT final_decision FROM decisions WHERE trace_id=?",
                    (body["trace_id"],),
                ).fetchone()
                durable = con.execute(
                    "SELECT summary_json,final_decision,execution_allowed "
                    "FROM m03_compact_tick WHERE trace_id=?",
                    (body["trace_id"],),
                ).fetchone()
            self.assertEqual(decision, ("REJECT",))
            self.assertIsNotNone(durable)
            self.assertEqual(durable[1:], ("REJECT", 0))
            self.assertEqual(json.loads(durable[0]), summary)
            self.assertLess(len(durable[0]), 1024)

    def test_original_journal_duplicate_trace_conflict_and_unbacked_reject(self):
        with TemporaryDirectory() as td:
            journal = DecisionJournal(Path(td) / "decisions.sqlite3")
            payload = {
                "schema_version": 1,
                "lane": "DEEP20_EVERY_FIVE_MINUTES",
                "coverage_status": "NO_BULK_EVIDENCE",
                "attempted_count": None,
                "analyzed_count": None,
                "final_decision": "REJECT",
                "execution_allowed": False,
                "g1_issuer_connected": False,
                "paid_network_allowed": False,
            }
            with self.assertRaises(DecisionConflictError):
                journal.record_m03_compact_tick("missing", payload)
            journal.record_reject("existing", "PUBLIC_BULK_REJECT")
            journal.record_m03_compact_tick("existing", payload)
            journal.record_m03_compact_tick("existing", payload)
            with self.assertRaises(DecisionConflictError):
                journal.record_m03_compact_tick(
                    "existing", {**payload, "coverage_status": "REJECT_INCOMPLETE"}
                )
            with sqlite3.connect(journal._db_path) as con:
                self.assertEqual(
                    con.execute("SELECT COUNT(*) FROM m03_compact_tick").fetchone(),
                    (1,),
                )


if __name__ == "__main__":
    unittest.main()
