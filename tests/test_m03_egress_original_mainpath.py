"""M03 real original mainpath acceptance, with only PUBLIC HTTP boundary mocked.

One scheduled CLI -> real DailyScalpingOrchestrator -> real DailyPairScanner ->
real IndodaxPublicMarket._get -> actual SharedPublicRestBudget SQLite ->
actual DecisionJournal and CLI Final REJECT. No live HTTP/AI/order.
"""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.public_market import IndodaxPublicMarket
from test_m03a_scanner import FakeIndodaxAll

# Epoch 5-minute boundary plus 70 seconds => light minute, second 10.
TICK = 1800000070
assert TICK % 60 == 10 and (TICK // 60) % 5 == 1


def policy(external: int) -> dict:
    # Synthetic globally-routable address only; never claim this is VPS IP.
    return {
        "schema_version": 1,
        "owner_approved_public_only": True,
        "all_shared_ip_consumers_accounted": True,
        "egress_public_ip": "8.8.8.8",
        "peer_service_names": [
            "crypto-bot-view.service",
            "crypto-bot-telegram.service",
            "hermes-gateway.service",
        ],
        "external_peak_requests_rolling_60s": external,
        "issued_at_epoch": TICK - 10,
        "expires_at_epoch": TICK + 60,
    }


def save_private(path: Path, data: dict):
    path.write_text(json.dumps(data))
    path.chmod(0o600)


def sqlite_rows(path: Path, sql: str):
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
        return con.execute(sql).fetchall()


class OriginalM03EgressMainpathTests(unittest.TestCase):
    def run_scheduled_cli(self, root: Path, *, external: int, expire_after_first: bool = False):
        p = root / "owner-only-policy.json"
        save_private(p, policy(external))
        ws_db = root / "read-only-existing-ws.sqlite3"
        ws_db.write_bytes(b"EXISTING_BTC_WS_FIXTURE_NOT_USED_BY_LIGHT")
        db = root / "decisions.sqlite3"
        quota = root / "rest-rate.sqlite3"

        fake = FakeIndodaxAll()
        fake.now = TICK
        for t in fake.tickers.values():
            t["server_time"] = TICK - 1

        calls = []
        def http_boundary(_client, path):
            calls.append(path)
            if expire_after_first and len(calls) == 1:
                stale = policy(external)
                stale["expires_at_epoch"] = TICK - 1
                save_private(p, stale)
            return fake.fetch(path)

        safe_env = {
            "INDODAX_TRADING_ENABLED": "NO",
            "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
            "INDODAX_EXECUTION_MODE": "DRY_RUN",
            "INDODAX_M03_REQUIRE_EGRESS_ADMISSION": "YES",
            "INDODAX_M03_EGRESS_ADMISSION_FILE": str(p),
            "INDODAX_PUBLIC_REST_RATE_DB": str(quota),
        }
        out, err = StringIO(), StringIO()
        argv = [
            "--scheduled-public-scanner-once",
            "--ws-db", str(ws_db),
            "--db", str(db),
            "--bulk-deep-limit", "20",
            "--bulk-min-volume-idr", "150000000",
        ]
        # Patch only the time source + original HTTP transport boundary,
        # never orchestrator/scanner/client._get/reserve/SQLite/journal.
        with (
            patch.dict(os.environ, safe_env),
            patch("indodax_ai_bot.__main__.time.time", return_value=TICK),
            patch("indodax_ai_bot.public_market.time", return_value=TICK),
            patch("indodax_ai_bot.m03_egress_admission.time.time", return_value=TICK),
            patch.object(IndodaxPublicMarket, "_http_json",
                         autospec=True, side_effect=http_boundary),
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            rc = main(argv)

        self.assertEqual(err.getvalue(), "")
        result = json.loads(out.getvalue())
        return rc, result, calls, db, quota

    def test_original_cli_light_3_gets_pass_peer_reservation_and_persist_reject(self):
        with TemporaryDirectory() as td:
            rc, body, calls, db, quota = self.run_scheduled_cli(
                Path(td), external=164
            )
            self.assertEqual(rc, 0)
            self.assertEqual(calls, [
                "/api/pairs", "/api/server_time", "/api/ticker_all"
            ])
            self.assertEqual(body["scheduled_m03_lane"],
                             "LIGHT_ALL_IDR_EVERY_MINUTE")
            self.assertEqual(body["public_scanner"]["all_idr_pairs"], 45)
            self.assertEqual(body["public_scanner"]["shortlist_count"], 30)
            self.assertEqual(body["final_decision"], "REJECT")
            self.assertIs(body["execution_allowed"], False)
            self.assertIs(body["paid_network_allowed"], False)
            self.assertIs(body["live_trading_authorized"], False)
            self.assertEqual(
                sqlite_rows(quota, "SELECT COUNT(*) FROM rest_get_reservations"),
                [(3,)],
            )
            self.assertEqual(
                sqlite_rows(db, "SELECT final_decision, reason FROM decisions"),
                [("REJECT", "G1_DEEP_ANALYSIS_AND_RISK_NOT_CONNECTED")],
            )
            self.assertEqual(
                sqlite_rows(db, "SELECT COUNT(*) FROM public_ticker_minute_samples"),
                [(45,)],
            )

    def test_original_cli_exhaustion_stops_second_get_and_journals_reject(self):
        with TemporaryDirectory() as td:
            rc, body, calls, db, quota = self.run_scheduled_cli(
                Path(td), external=167
            )
            self.assertEqual(rc, 3)
            self.assertEqual(calls, ["/api/pairs"])
            self.assertIsNone(body["public_scanner"])
            self.assertEqual(body["scheduled_m03_lane"],
                             "LIGHT_ALL_IDR_EVERY_MINUTE")
            self.assertIn("PUBLIC_SHARED_REST_RATE_BUDGET_EXCEEDED",
                          body["reason"])
            self.assertEqual(body["final_decision"], "REJECT")
            self.assertIs(body["execution_allowed"], False)
            self.assertEqual(
                sqlite_rows(quota, "SELECT COUNT(*) FROM rest_get_reservations"),
                [(1,)],
            )
            self.assertEqual(
                sqlite_rows(db, "SELECT final_decision FROM decisions"),
                [("REJECT",)],
            )

    def test_original_cli_policy_expiry_between_gets_stops_and_journals_reject(self):
        with TemporaryDirectory() as td:
            rc, body, calls, db, quota = self.run_scheduled_cli(
                Path(td), external=164, expire_after_first=True
            )
            self.assertEqual(rc, 3)
            self.assertEqual(calls, ["/api/pairs"])
            self.assertEqual(body["scheduled_m03_lane"],
                             "LIGHT_ALL_IDR_EVERY_MINUTE")
            self.assertIn("M03_EGRESS_ATTESTATION_EXPIRED", body["reason"])
            self.assertEqual(body["final_decision"], "REJECT")
            self.assertIs(body["execution_allowed"], False)
            self.assertEqual(
                sqlite_rows(quota, "SELECT COUNT(*) FROM rest_get_reservations"),
                [(1,)],
            )
            self.assertEqual(
                sqlite_rows(db, "SELECT final_decision FROM decisions"),
                [("REJECT",)],
            )


if __name__ == "__main__":
    unittest.main()
