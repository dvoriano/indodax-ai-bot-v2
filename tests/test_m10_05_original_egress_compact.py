"""M10-05 original V2 scoped egress + compact journal regression.

All fixtures synthetic and isolated. No network, paid models, owner secrets,
external services, Indodax orders, or systemd start is permitted.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import os
import sqlite3
import unittest

from indodax_ai_bot.__main__ import main, _m03_compact_from_original
from indodax_ai_bot.config import Settings
from indodax_ai_bot.contracts import FinalDecision
from indodax_ai_bot.journal import DecisionConflictError, DecisionJournal
from indodax_ai_bot.m03_egress_admission import (
    M03EgressAdmissionError, M03SharedEgressAdmission,
)
from indodax_ai_bot.orchestrator import CycleResult
from indodax_ai_bot.public_market import IndodaxPublicMarket, PublicDataError
from indodax_ai_bot.public_rest_budget import SharedPublicRestBudget
from test_m03a_scanner import FakeIndodaxAll

NOW = 1800000070  # light minute, second 10
assert NOW % 60 == 10 and (NOW // 60) % 5 == 1


def owner_policy(*, external=164, issued=NOW-5, expires=NOW+45):
    return {
        "schema_version": 1,
        "owner_approved_public_only": True,
        "all_shared_ip_consumers_accounted": True,
        "egress_public_ip": "8.8.8.8",  # synthetic, NOT a VPS IP check
        "external_consumer_ids": ["other-consumer-a"],
        "external_peak_requests_rolling_60s": external,
        "issued_at_epoch": issued,
        "expires_at_epoch": expires,
    }


def write_private(path: Path, data: dict):
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(0o600)


class M10OriginalEgressTests(unittest.TestCase):
    def test_generic_peer_policy_reserves_remaining_capacity(self):
        with TemporaryDirectory() as td:
            target = Path(td) / "owner.json"
            write_private(target, owner_policy(external=100))
            self.assertEqual(
                M03SharedEgressAdmission(target, clock=lambda: NOW).allowed_local_limit(),
                68,
            )

    def test_missing_symlink_worldreadable_and_invalid_policy_fail_closed(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            target = root / "policy.json"
            with self.assertRaises(M03EgressAdmissionError):
                M03SharedEgressAdmission(target, clock=lambda: NOW).allowed_local_limit()
            write_private(target, owner_policy())
            link = root / "link.json"
            link.symlink_to(target)
            with self.assertRaises(M03EgressAdmissionError):
                M03SharedEgressAdmission(link, clock=lambda: NOW).allowed_local_limit()
            target.chmod(0o644)
            with self.assertRaisesRegex(M03EgressAdmissionError, "POLICY_UNTRUSTED"):
                M03SharedEgressAdmission(target, clock=lambda: NOW).allowed_local_limit()
            target.chmod(0o600)
            for mutate in (
                lambda p: p.update(all_shared_ip_consumers_accounted=False),
                lambda p: p.update(external_consumer_ids=["invalid consumer!"]),
                lambda p: p.update(external_consumer_ids=["x", "x"]),
                lambda p: p.update(external_consumer_ids=[]),
                lambda p: p.update(external_peak_requests_rolling_60s=-1),
                lambda p: p.update(external_peak_requests_rolling_60s=168),
                lambda p: p.update(egress_public_ip="127.0.0.1"),
                lambda p: p.update(expires_at_epoch=NOW-1),
                lambda p: p.update(issued_at_epoch=NOW+1),
                lambda p: p.update(expires_at_epoch=NOW+901),
            ):
                with self.subTest(mutate=str(mutate)):
                    policy = owner_policy()
                    mutate(policy)
                    write_private(target, policy)
                    with self.assertRaises(M03EgressAdmissionError):
                        M03SharedEgressAdmission(
                            target, clock=lambda: NOW
                        ).allowed_local_limit()

    def test_original_public_get_reserves_before_transport_and_never_refunds(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "owner.json"
            write_private(path, owner_policy(external=167))
            calls = []
            budget = SharedPublicRestBudget(
                Path(td) / "rate.sqlite3", clock=lambda: NOW,
            )
            with (patch.dict(os.environ, {
                    "INDODAX_M03_REQUIRE_EGRESS_ADMISSION": "YES",
                    "INDODAX_M03_EGRESS_ADMISSION_FILE": str(path),
                }),
                patch("indodax_ai_bot.m03_egress_admission.time.time",
                      return_value=NOW)):
                client = IndodaxPublicMarket(
                    fetch=lambda p: calls.append(p) or {},
                    rest_budget=budget,
                )
                self.assertEqual(client._get("/api/pairs"), {})
                with self.assertRaisesRegex(
                    PublicDataError, "PUBLIC_SHARED_REST_RATE_BUDGET_EXCEEDED"
                ):
                    client._get("/api/server_time")
                self.assertEqual(client.public_get_attempt_count, 2)
            self.assertEqual(calls, ["/api/pairs"])

    def test_policy_expiring_between_requests_stops_before_http(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "owner.json"
            write_private(path, owner_policy(external=2, expires=NOW+1))
            calls = []
            budget = SharedPublicRestBudget(
                Path(td) / "quota.sqlite3", clock=lambda: NOW,
            )
            with (patch.dict(os.environ, {
                    "INDODAX_M03_REQUIRE_EGRESS_ADMISSION": "YES",
                    "INDODAX_M03_EGRESS_ADMISSION_FILE": str(path),
                }),
                patch("indodax_ai_bot.m03_egress_admission.time.time",
                      return_value=NOW) as clk):
                client = IndodaxPublicMarket(
                    fetch=lambda p: calls.append(p) or {},
                    rest_budget=budget,
                )
                client._get("/api/pairs")
                clk.return_value = NOW + 2
                with self.assertRaisesRegex(
                    PublicDataError, "M03_EGRESS_ATTESTATION_EXPIRED"
                ):
                    client._get("/api/server_time")
            self.assertEqual(calls, ["/api/pairs"])

    def test_declared_mode_cannot_bypass_atomic_quota_with_synthetic_fetch(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "owner.json"
            write_private(path, owner_policy())
            calls = []
            with (patch.dict(os.environ, {
                    "INDODAX_M03_REQUIRE_EGRESS_ADMISSION": "YES",
                    "INDODAX_M03_EGRESS_ADMISSION_FILE": str(path),
                }),
                patch("indodax_ai_bot.m03_egress_admission.time.time",
                      return_value=NOW)):
                client = IndodaxPublicMarket(
                    fetch=lambda p: calls.append(p) or {},
                )
                with self.assertRaisesRegex(
                    PublicDataError, "M03_EGRESS_LOCAL_BUDGET_REQUIRED"
                ):
                    client._get("/api/pairs")
            self.assertEqual(calls, [])

    def test_original_scheduled_cli_without_owner_policy_prevents_journal(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            ws = root / "ws.sqlite3"
            ws.write_bytes(b"FIXTURE")
            db = root / "decisions.sqlite3"
            out, err = StringIO(), StringIO()
            with (patch.dict(os.environ, {
                    "INDODAX_TRADING_ENABLED": "NO",
                    "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
                    "INDODAX_EXECUTION_MODE": "DRY_RUN",
                    "INDODAX_M03_REQUIRE_EGRESS_ADMISSION": "NO",
                }, clear=True),
                patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket",
                      side_effect=AssertionError("NO_NETWORK")),
                redirect_stdout(out), redirect_stderr(err)):
                rc = main([
                    "--scheduled-public-scanner-once",
                    "--ws-db", str(ws), "--db", str(db),
                ])
            self.assertEqual(rc, 2)
            self.assertIn("M03_EGRESS_ADMISSION_REQUIRED", err.getvalue())
            self.assertEqual(out.getvalue(), "")
            self.assertFalse(db.exists())

    def original_light(self, td, external):
        root = Path(td)
        policy = root / "policy.json"
        write_private(policy, owner_policy(external=external))
        ws = root / "ws.sqlite3"
        ws.write_bytes(b"EXISTING_ONLY_NOT_READ_IN_LIGHT")
        db = root / "decision.sqlite3"
        rate = root / "rate.sqlite3"
        fake = FakeIndodaxAll()
        fake.now = NOW
        for t in fake.tickers.values():
            t["server_time"] = NOW - 1
        calls = []
        original_init = IndodaxPublicMarket.__init__
        def fixed_clock(client, *a, **kw):
            kw.setdefault("clock", lambda: NOW)
            return original_init(client, *a, **kw)
        def fake_http(_client, path):
            calls.append(path)
            return fake.fetch(path)
        env = {
            "INDODAX_TRADING_ENABLED": "NO",
            "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
            "INDODAX_EXECUTION_MODE": "DRY_RUN",
            "INDODAX_M03_REQUIRE_EGRESS_ADMISSION": "YES",
            "INDODAX_M03_EGRESS_ADMISSION_FILE": str(policy),
            "INDODAX_PUBLIC_REST_RATE_DB": str(rate),
        }
        out, err = StringIO(), StringIO()
        with (patch.dict(os.environ, env),
              patch("indodax_ai_bot.__main__.time.time", return_value=NOW),
              patch.object(IndodaxPublicMarket, "__init__", new=fixed_clock),
              patch.object(IndodaxPublicMarket, "_http_json",
                           autospec=True, side_effect=fake_http),
              redirect_stdout(out), redirect_stderr(err)):
            rc = main([
                "--scheduled-public-scanner-once",
                "--ws-db", str(ws), "--db", str(db),
                "--bulk-min-volume-idr", "150000000",
            ])
        self.assertEqual(err.getvalue(), "")
        return rc, json.loads(out.getvalue()), calls, db, rate

    def test_original_cli_light_three_requests_with_peer_cap(self):
        with TemporaryDirectory() as td:
            rc, data, calls, db, rate = self.original_light(td, external=164)
            self.assertEqual(rc, 0)
            self.assertEqual(
                calls, ["/api/pairs", "/api/server_time", "/api/ticker_all"],
            )
            self.assertEqual(data["scheduled_m03_lane"],
                             "LIGHT_ALL_IDR_EVERY_MINUTE")
            self.assertFalse(data["execution_allowed"])
            self.assertFalse(data["live_trading_authorized"])
            self.assertEqual(DecisionJournal(db).count(), 1)
            with sqlite3.connect(rate) as con:
                self.assertEqual(con.execute(
                    "SELECT COUNT(*) FROM rest_get_reservations"
                ).fetchone()[0], 3)

    def test_original_cli_exhaustion_blocks_second_public_request(self):
        with TemporaryDirectory() as td:
            rc, data, calls, db, rate = self.original_light(td, external=167)
            self.assertEqual(rc, 3)
            self.assertEqual(calls, ["/api/pairs"])
            self.assertFalse(data["execution_allowed"])
            self.assertIn("PUBLIC_SHARED_REST_RATE_BUDGET_EXCEEDED",
                          data["reason"])
            self.assertEqual(DecisionJournal(db).count(), 1)


class M10CompactOriginalTests(unittest.TestCase):
    @staticmethod
    def summary():
        return {
            "schema_version": 1,
            "lane": "DEEP20_EVERY_FIVE_MINUTES",
            "coverage_status": "PARTIAL_INVALID_PAIR_EVIDENCE",
            "attempted_count": 20,
            "analyzed_count": 17,
            "final_decision": None,
            "stage_disposition": "ADVISORY_ONLY_NO_OWNER_DECISION",
            "execution_allowed": False,
            "g1_verified": False,
            "paid_network_allowed": False,
        }

    def test_compact_original_journal_idempotent_and_conflict_safe(self):
        with TemporaryDirectory() as td:
            journal = DecisionJournal(Path(td) / "decision.sqlite3")
            data = self.summary()
            journal.record_m03_compact_tick("trace-1", data)
            journal.record_m03_compact_tick("trace-1", data)
            self.assertEqual(journal.m03_compact_count(), 1)
            self.assertEqual(journal.count(), 0)
            changed = deepcopy(data)
            changed["analyzed_count"] = 18
            with self.assertRaises(DecisionConflictError):
                journal.record_m03_compact_tick("trace-1", changed)
            journal.record_reject("owner-final-present", "SAFE_NO_ORDER")
            with self.assertRaises(DecisionConflictError):
                journal.record_m03_compact_tick(
                    "owner-final-present", data,
                )

    def test_compact_never_accepts_final_or_execution_claims(self):
        with TemporaryDirectory() as td:
            journal = DecisionJournal(Path(td) / "decision.sqlite3")
            for key, value in (
                ("final_decision", "REJECT"),
                ("execution_allowed", True),
                ("g1_verified", True),
                ("paid_network_allowed", True),
                ("attempted_count", 41),
                ("analyzed_count", 22),
            ):
                data = self.summary()
                data[key] = value
                with self.subTest(key=key):
                    with self.assertRaises(ValueError):
                        journal.record_m03_compact_tick(key, data)
            self.assertEqual(journal.m03_compact_count(), 0)

    def test_compact_observation_redacts_unverified_extra_fields(self):
        outcome = CycleResult(
            "deep",
            None,
            "G1_PUBLIC_BULK_SOURCE_UNVERIFIED",
            False,
            bulk={
                "coverage_status": "PARTIAL_INVALID_PAIR_EVIDENCE",
                "attempted_count": 20,
                "analyzed_count": 12,
                "g1_verified": False,
                "execution_allowed": False,
                "api_secret": "NEVER_PERSIST_THIS",
            },
            stage_disposition="ADVISORY_ONLY_NO_OWNER_DECISION",
        )
        compact = _m03_compact_from_original(outcome)
        self.assertNotIn("api_secret", json.dumps(compact))
        self.assertIsNone(compact["final_decision"])
        self.assertFalse(compact["execution_allowed"])
        with TemporaryDirectory() as td:
            db = Path(td) / "decision.sqlite3"
            journal = DecisionJournal(db)
            journal.record_m03_compact_tick("deep", compact)
            self.assertNotIn("NEVER_PERSIST_THIS", db.read_bytes().decode(
                "utf-8", errors="ignore",
            ))

    def test_staged_service_is_off_and_owner_gated_by_default(self):
        service = (Path(__file__).resolve().parents[1]
                   / "deploy/indodax-v2-m03-mainpath.service").read_text()
        self.assertIn("Environment=INDODAX_M03_REQUIRE_EGRESS_ADMISSION=YES",
                      service)
        self.assertIn("Environment=INDODAX_TRADING_ENABLED=NO", service)
        self.assertIn("Environment=INDODAX_ALLOW_LIVE_EXECUTION=NO", service)
        self.assertIn("Environment=INDODAX_EXECUTION_MODE=DRY_RUN", service)
        self.assertNotIn("systemctl enable", service)
        self.assertNotIn("crypto-bot", service)
        self.assertNotIn("hermes-gateway", service)


if __name__ == "__main__":
    unittest.main()
