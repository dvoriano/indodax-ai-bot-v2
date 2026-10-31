"""M03 shared-egress admission fail-closed; OFFLINE-only, zero real HTTP.

These tests verify no bypass by one-shot CLI or original PublicMarket._get;
operator policy is NOT actual measurement of all machines sharing the IP.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.m03_egress_admission import (
    M03EgressAdmissionError, M03SharedEgressAdmission,
)
from indodax_ai_bot.public_market import IndodaxPublicMarket, PublicDataError
from indodax_ai_bot.public_rest_budget import SharedPublicRestBudget


NOW = 1800000000


def accepted_policy(*, external=100):
    return {
        "schema_version": 1,
        "owner_approved_public_only": True,
        "all_shared_ip_consumers_accounted": True,
        "egress_public_ip": "8.8.8.8",  # synthetic validation fixture, NOT host IP
        "peer_service_names": [
            "crypto-bot-view.service",
            "crypto-bot-telegram.service",
            "hermes-gateway.service",
        ],
        "external_peak_requests_rolling_60s": external,
        "issued_at_epoch": NOW - 5,
        "expires_at_epoch": NOW + 100,
    }


def write_policy(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o600)


class M03SharedEgressAdmissionTests(unittest.TestCase):
    def test_missing_policy_denies_without_creating_any_file(self):
        with TemporaryDirectory() as td:
            target = Path(td) / "missing.json"
            with self.assertRaisesRegex(
                M03EgressAdmissionError, "M03_EGRESS_POLICY_UNAVAILABLE"
            ):
                M03SharedEgressAdmission(target, clock=lambda: NOW).allowed_local_limit()
            self.assertFalse(target.exists())

    def test_owner_complete_private_fresh_policy_reserves_external_capacity(self):
        with TemporaryDirectory() as td:
            policy = Path(td) / "owner.json"
            write_policy(policy, accepted_policy(external=100))
            self.assertEqual(
                M03SharedEgressAdmission(policy, clock=lambda: NOW).allowed_local_limit(),
                68,
            )

    def test_missing_peers_approval_unknown_capacity_and_stale_policy_denied(self):
        cases = []
        missing = accepted_policy()
        missing["peer_service_names"] = ["crypto-bot-view.service"]
        cases.append(missing)
        unaccounted = accepted_policy()
        unaccounted["all_shared_ip_consumers_accounted"] = False
        cases.append(unaccounted)
        low = accepted_policy()
        low["external_peak_requests_rolling_60s"] = -1
        cases.append(low)
        nonpublic = accepted_policy()
        nonpublic["egress_public_ip"] = "127.0.0.1"
        cases.append(nonpublic)
        stale = accepted_policy()
        stale["expires_at_epoch"] = NOW - 1
        cases.append(stale)
        future = accepted_policy()
        future["issued_at_epoch"] = NOW + 10
        cases.append(future)
        for i, value in enumerate(cases):
            with self.subTest(case=i), TemporaryDirectory() as td:
                target = Path(td) / "policy.json"
                write_policy(target, value)
                with self.assertRaises(M03EgressAdmissionError):
                    M03SharedEgressAdmission(
                        target, clock=lambda: NOW
                    ).allowed_local_limit()

    def test_symlink_world_readable_and_oversized_policy_denied(self):
        with TemporaryDirectory() as td:
            target = Path(td) / "policy.json"
            write_policy(target, accepted_policy())
            link = Path(td) / "link.json"
            link.symlink_to(target)
            with self.assertRaises(M03EgressAdmissionError):
                M03SharedEgressAdmission(link, clock=lambda: NOW).allowed_local_limit()
            target.chmod(0o644)
            with self.assertRaisesRegex(
                M03EgressAdmissionError, "M03_EGRESS_POLICY_UNTRUSTED"
            ):
                M03SharedEgressAdmission(target, clock=lambda: NOW).allowed_local_limit()
            target.write_bytes(b"x" * 8200)
            target.chmod(0o600)
            with self.assertRaises(M03EgressAdmissionError):
                M03SharedEgressAdmission(target, clock=lambda: NOW).allowed_local_limit()

    def test_original_get_uses_existing_atomic_budget_with_lower_peer_cap(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "policy.json"
            write_policy(path, accepted_policy(external=167))
            quota = SharedPublicRestBudget(
                Path(td) / "shared.sqlite3", clock=lambda: NOW
            )
            calls = []
            with (patch.dict(os.environ, {
                    "INDODAX_M03_REQUIRE_EGRESS_ADMISSION": "YES",
                    "INDODAX_M03_EGRESS_ADMISSION_FILE": str(path),
                }),
                patch("indodax_ai_bot.m03_egress_admission.time.time",
                      return_value=NOW)):
                market = IndodaxPublicMarket(
                    fetch=lambda _: calls.append("GET") or {"ok": True},
                    rest_budget=quota,
                )
                self.assertEqual(market._get("/api/pairs"), {"ok": True})
                with self.assertRaisesRegex(
                    PublicDataError, "PUBLIC_SHARED_REST_RATE_BUDGET_EXCEEDED"
                ):
                    market._get("/api/pairs")
            self.assertEqual(calls, ["GET"])

    def test_policy_expiring_between_gets_blocks_before_transport(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "policy.json"
            value = accepted_policy(external=1)
            value["expires_at_epoch"] = NOW + 1
            write_policy(path, value)
            calls = []
            quota = SharedPublicRestBudget(
                Path(td) / "rate.sqlite3", clock=lambda: NOW
            )
            with (patch.dict(os.environ, {
                    "INDODAX_M03_REQUIRE_EGRESS_ADMISSION": "YES",
                    "INDODAX_M03_EGRESS_ADMISSION_FILE": str(path),
                }),
                patch("indodax_ai_bot.m03_egress_admission.time.time",
                      return_value=NOW) as fake_clock):
                # Constructor captures the clock; advance that SAME clock.
                market = IndodaxPublicMarket(
                    fetch=lambda _: calls.append("GET") or {}, rest_budget=quota
                )
                market._get("/api/pairs")
                fake_clock.return_value = NOW + 2
                with self.assertRaisesRegex(
                    PublicDataError, "M03_EGRESS_ATTESTATION_EXPIRED"
                ):
                    market._get("/api/pairs")
            self.assertEqual(calls, ["GET"])

    def test_policy_cannot_bypass_v2_atomic_reservation(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "policy.json"
            write_policy(path, accepted_policy(external=0))
            network = []
            with (patch.dict(os.environ, {
                    "INDODAX_M03_REQUIRE_EGRESS_ADMISSION": "YES",
                    "INDODAX_M03_EGRESS_ADMISSION_FILE": str(path),
                }),
                patch("indodax_ai_bot.m03_egress_admission.time.time",
                      return_value=NOW)):
                market = IndodaxPublicMarket(
                    fetch=lambda _: network.append("GET") or {}
                )
                with self.assertRaisesRegex(
                    PublicDataError, "M03_EGRESS_LOCAL_BUDGET_REQUIRED"
                ):
                    market._get("/api/pairs")
            self.assertEqual(network, [])

    def test_scheduled_cli_missing_policy_fails_before_journal_or_http(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            ws = root / "existing-ws.sqlite3"
            ws.write_bytes(b"read-only fixture")
            missing = root / "never-created.json"
            db = root / "decisions.sqlite3"
            out, err = StringIO(), StringIO()
            with (patch.dict(os.environ, {
                    "INDODAX_M03_REQUIRE_EGRESS_ADMISSION": "YES",
                    "INDODAX_M03_EGRESS_ADMISSION_FILE": str(missing),
                    "INDODAX_TRADING_ENABLED": "NO",
                    "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
                    "INDODAX_EXECUTION_MODE": "DRY_RUN",
                }),
                patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket",
                      side_effect=AssertionError("NO_REAL_HTTP")) as network,
                redirect_stdout(out), redirect_stderr(err)):
                rc = main([
                    "--scheduled-public-scanner-once", "--ws-db", str(ws),
                    "--db", str(db),
                ])
            self.assertEqual(rc, 2)
            self.assertIn("M03_EGRESS_POLICY_UNAVAILABLE", err.getvalue())
            self.assertEqual(out.getvalue(), "")
            self.assertFalse(db.exists())
            network.assert_not_called()

    def test_original_unit_explicitly_requires_admission_and_all_hardlocks(self):
        root = Path(__file__).resolve().parents[1]
        svc = (root / "deploy/indodax-v2-m03-mainpath.service").read_text()
        self.assertIn("Environment=INDODAX_M03_REQUIRE_EGRESS_ADMISSION=YES", svc)
        self.assertIn("INDODAX_M03_EGRESS_ADMISSION_FILE=", svc)
        self.assertIn("ExecStartPre=/usr/bin/echo M03_ORIGINAL_SERVICE_PRE_EXEC", svc)
        self.assertIn("ExecStartPost=/usr/bin/echo M03_ORIGINAL_SERVICE_POST_EXEC", svc)
        self.assertEqual(
            len([v for v in svc.splitlines() if v.startswith("ExecStart=")]), 1
        )
        for required in (
            "--scheduled-public-scanner-once",
            "INDODAX_TRADING_ENABLED=NO",
            "INDODAX_ALLOW_LIVE_EXECUTION=NO",
            "INDODAX_EXECUTION_MODE=DRY_RUN",
            "Restart=no",
        ):
            self.assertIn(required, svc)


    def test_live_peer_quiet_gate_uses_real_units_not_owner_boolean_alone(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "operator.json"
            write_policy(path, accepted_policy(external=100))
            seen = []
            def quiet(unit):
                seen.append(unit)
                return True
            admission = M03SharedEgressAdmission(
                path, clock=lambda: NOW, require_peer_quiet=True,
                peer_probe=quiet,
            )
            self.assertEqual(admission.allowed_local_limit(), 68)
            self.assertEqual(set(seen), set(accepted_policy()["peer_service_names"]))

    def test_self_declared_all_peers_cannot_override_live_peer_activity(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "operator.json"
            write_policy(path, accepted_policy(external=10))
            def active(unit):
                if unit == "crypto-bot-view.service":
                    raise M03EgressAdmissionError(
                        "M03_EGRESS_ACTIVE_UNCOORDINATED_PEER")
                return True
            admission = M03SharedEgressAdmission(
                path, clock=lambda: NOW, require_peer_quiet=True,
                peer_probe=active,
            )
            with self.assertRaisesRegex(
                M03EgressAdmissionError, "M03_EGRESS_ACTIVE_UNCOORDINATED_PEER"
            ):
                admission.allowed_local_limit()

    def test_unverified_peer_state_fails_closed_before_public_http(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "operator.json"
            write_policy(path, accepted_policy(external=0))
            attempted = []
            with (patch.dict(os.environ, {
                    "INDODAX_M03_REQUIRE_EGRESS_ADMISSION": "YES",
                    "INDODAX_M03_REQUIRE_PEER_QUIET": "YES",
                    "INDODAX_M03_EGRESS_ADMISSION_FILE": str(path),
                }),
                patch("indodax_ai_bot.m03_egress_admission.time.time",
                      return_value=NOW),
                patch("indodax_ai_bot.m03_egress_admission._systemd_peer_quiet",
                      side_effect=M03EgressAdmissionError(
                          "M03_EGRESS_PEER_ACTIVITY_UNVERIFIED"))):
                market = IndodaxPublicMarket(
                    fetch=lambda _: attempted.append("HTTP") or {},
                    rest_budget=SharedPublicRestBudget(
                        Path(td) / "quota.sqlite3", clock=lambda: NOW),
                )
                with self.assertRaisesRegex(
                    PublicDataError, "M03_EGRESS_PEER_ACTIVITY_UNVERIFIED"
                ):
                    market._get("/api/pairs")
            self.assertEqual(attempted, [])
            self.assertFalse((Path(td) / "quota.sqlite3").exists())

    def test_real_systemd_peer_probe_rejects_active_unknown_and_broken_states(self):
        from subprocess import CompletedProcess
        from indodax_ai_bot.m03_egress_admission import _systemd_peer_quiet
        quiet = "LoadState=loaded\nActiveState=inactive\nMainPID=0\n"
        active = "LoadState=loaded\nActiveState=active\nMainPID=100\n"
        unknown = "LoadState=not-found\nActiveState=inactive\nMainPID=0\n"
        with patch("indodax_ai_bot.m03_egress_admission.subprocess.run",
                   return_value=CompletedProcess([], 0, stdout=quiet, stderr="")) as run:
            self.assertTrue(_systemd_peer_quiet("crypto-bot-view.service"))
            self.assertIn("--user", run.call_args.args[0])
        for text, expected in (
                (active, "M03_EGRESS_ACTIVE_UNCOORDINATED_PEER"),
                (unknown, "M03_EGRESS_PEER_ACTIVITY_UNVERIFIED"),
                (quiet + "Extra=1\n", "M03_EGRESS_PEER_ACTIVITY_UNVERIFIED")):
            with self.subTest(state=expected), patch(
                    "indodax_ai_bot.m03_egress_admission.subprocess.run",
                    return_value=CompletedProcess([], 0, stdout=text, stderr="")):
                with self.assertRaisesRegex(M03EgressAdmissionError, expected):
                    _systemd_peer_quiet("crypto-bot-view.service")

    def test_installed_unit_requires_real_peer_isolation(self):
        root = Path(__file__).resolve().parents[1]
        unit = (root / "deploy/indodax-v2-m03-mainpath.service").read_text()
        self.assertIn("Environment=INDODAX_M03_REQUIRE_PEER_QUIET=YES", unit)
        self.assertIn("Environment=INDODAX_M03_REQUIRE_EGRESS_ADMISSION=YES", unit)
        self.assertIn("Restart=no", unit)


    def test_original_scheduled_cli_active_peer_rejects_before_journal_or_http(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            p = root / "owner.json"
            write_policy(p, accepted_policy(external=10))
            ws = root / "existing-ws.sqlite3"
            ws.write_bytes(b"existing-public-ws-readonly")
            journal = root / "would-be-journal.sqlite3"
            rest = root / "would-be-rate.sqlite3"
            out, err = StringIO(), StringIO()
            with (patch.dict(os.environ, {
                    "INDODAX_M03_REQUIRE_EGRESS_ADMISSION": "YES",
                    "INDODAX_M03_REQUIRE_PEER_QUIET": "YES",
                    "INDODAX_M03_EGRESS_ADMISSION_FILE": str(p),
                    "INDODAX_PUBLIC_REST_RATE_DB": str(rest),
                    "INDODAX_TRADING_ENABLED": "NO",
                    "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
                    "INDODAX_EXECUTION_MODE": "DRY_RUN",
                }),
                patch("indodax_ai_bot.m03_egress_admission.time.time",
                      return_value=NOW),
                patch("indodax_ai_bot.m03_egress_admission._systemd_peer_quiet",
                      side_effect=M03EgressAdmissionError(
                          "M03_EGRESS_ACTIVE_UNCOORDINATED_PEER")),
                patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket",
                      side_effect=AssertionError("NO_SCANNER_OR_HTTP")) as scanner,
                redirect_stdout(out), redirect_stderr(err)):
                rc = main(["--scheduled-public-scanner-once",
                           "--ws-db", str(ws), "--db", str(journal)])
            self.assertEqual(rc, 2)
            self.assertEqual(out.getvalue(), "")
            self.assertIn("M03_EGRESS_ACTIVE_UNCOORDINATED_PEER", err.getvalue())
            self.assertFalse(journal.exists())
            self.assertFalse(rest.exists())
            scanner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
