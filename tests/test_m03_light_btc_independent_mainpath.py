"""Original scheduled M03 CLI routing under BTC HOLD, offline.

Only the existing mainpath and original names are used. No socket,
provider, systemd, Telegram, G1 issuer, private API or orders.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
import unittest

from indodax_ai_bot import __main__ as cli
from indodax_ai_bot.m03_egress_admission import M03EgressAdmissionError


class OriginalM03LightIndependentDispatchTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory(prefix="m03-light-mainpath-")
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.db = root / "decisions.sqlite3"
        self.ws = root / "btc.sqlite3"
        self.ws.write_bytes(b"NO_NETWORK_OFFLINE_PLACEHOLDER")
        self.args = [
            "--scheduled-public-scanner-once",
            "--db", str(self.db), "--ws-db", str(self.ws),
            "--bulk-deep-limit", "20",
            "--bulk-min-volume-idr", "150000000",
        ]
        self.env = {
            "INDODAX_TRADING_ENABLED": "NO",
            "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
            "INDODAX_EXECUTION_MODE": "DRY_RUN",
            "INDODAX_M03_REQUIRE_EGRESS_ADMISSION": "YES",
        }

    @staticmethod
    def _make_synthetic_outcome(*, bulk=None):
        return SimpleNamespace(
            trace_id="OFFLINE_FIXED_TRACE",
            final_decision=None,
            stage_disposition="ADVISORY_ONLY_NO_OWNER_DECISION",
            reason="G1_DEEP_ANALYSIS_AND_RISK_NOT_CONNECTED",
            execution_allowed=False,
            market=None,
            screening={"status": "PUBLIC_WATCH_ONLY_G1_UNVERIFIED"},
            bulk=bulk, ai_shadow=None, owner_advisory=None,
        )

    def invoke(self, *, now, btc_ok, public_policy_ok=True, light_only=False):
        inspections = []

        def admission(*, require_btc_source=True):
            inspections.append(require_btc_source)
            if not public_policy_ok:
                raise M03EgressAdmissionError("M03_EGRESS_POLICY_UNAVAILABLE")
            if require_btc_source and not btc_ok:
                raise M03EgressAdmissionError(
                    "M03_STANDING_BTC_SOURCE_UNHEALTHY"
                )
            return 90

        with patch.dict(os.environ, self.env), patch.object(
            cli, "M03SharedEgressAdmission",
            return_value=SimpleNamespace(allowed_local_limit=admission),
        ), patch.object(
            cli, "IndodaxPublicMarket",
        ) as client_factory, patch.object(
            cli, "DailyScalpingOrchestrator",
        ) as orchestrator, patch.object(
            cli, "DecisionJournal", return_value=MagicMock(),
        ), patch.object(
            cli.time, "time", return_value=now,
        ), patch("sys.stdout", new_callable=io.StringIO) as out, patch(
            "sys.stderr", new_callable=io.StringIO,
        ) as err:
            orchestrator.return_value.run_scan_cycle.return_value = (
                self._make_synthetic_outcome()
            )
            orchestrator.return_value.run_cycle.return_value = (
                self._make_synthetic_outcome(bulk={
                    "coverage_status": "NO_BULK_EVIDENCE",
                    "g1_verified": False,
                    "execution_allowed": False,
                })
            )
            status = cli.main(self.args + (["--m03-light-only"] if light_only else []))
            stdout = out.getvalue()
            stderr = err.getvalue()
            light_calls = orchestrator.return_value.run_scan_cycle.call_args_list
            deep_calls = orchestrator.return_value.run_cycle.call_args_list
            factory_calls = client_factory.call_args_list

        return status, stdout, stderr, inspections, light_calls, deep_calls, factory_calls

    def test_btc_hold_on_deep_minute_downgrades_to_original_light(self):
        code, out, err, checks, lights, deeps, factory = self.invoke(
            now=1794000010, btc_ok=False,
        )
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        info = json.loads(out)
        self.assertEqual(info["scheduled_m03_lane"], "LIGHT_ALL_IDR_EVERY_MINUTE")
        self.assertEqual(info["m03_deep_deferred_reason"],
                         "BTC_PUBLIC_SOURCE_UNHEALTHY_LIGHT_WATCH_ONLY")
        self.assertEqual(checks, [False, True])
        self.assertEqual(len(lights), 1)
        self.assertEqual(len(deeps), 0)
        self.assertEqual(len(factory), 1)
        self.assertIs(factory[0].kwargs["m03_light_only_public_watch"], True)
        self.assertFalse(info["execution_allowed"])

    def test_btc_hold_on_light_minute_does_not_check_btc(self):
        code, out, err, checks, lights, deeps, factory = self.invoke(
            now=1794000070, btc_ok=False,
        )
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["scheduled_m03_lane"],
                         "LIGHT_ALL_IDR_EVERY_MINUTE")
        self.assertNotIn("m03_deep_deferred_reason", json.loads(out))
        self.assertEqual(checks, [False])
        self.assertEqual(len(lights), 1)
        self.assertFalse(deeps)
        self.assertEqual(len(factory), 1)

    def test_missing_btc_sqlite_does_not_prevent_original_light_tick(self):
        self.ws.unlink()
        code, out, err, checks, lights, deeps, factory = self.invoke(
            now=1794000070, btc_ok=False,
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")
        result = json.loads(out)
        self.assertEqual(result["scheduled_m03_lane"],
                         "LIGHT_ALL_IDR_EVERY_MINUTE")
        self.assertEqual(checks, [False])
        self.assertEqual(len(lights), 1)
        self.assertFalse(deeps)
        self.assertFalse(self.ws.exists())
        self.assertTrue(factory[0].kwargs["m03_light_only_public_watch"])
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["live_trading_authorized"])

    def test_missing_btc_sqlite_downgrades_deep_to_original_light(self):
        self.ws.unlink()
        code, out, err, checks, lights, deeps, factory = self.invoke(
            now=1794000010, btc_ok=True,
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")
        result = json.loads(out)
        self.assertEqual(result["scheduled_m03_lane"],
                         "LIGHT_ALL_IDR_EVERY_MINUTE")
        self.assertEqual(result["m03_deep_deferred_reason"],
                         "BTC_PUBLIC_SOURCE_UNHEALTHY_LIGHT_WATCH_ONLY")
        self.assertEqual(checks, [False])
        self.assertEqual(len(lights), 1)
        self.assertFalse(deeps)
        self.assertFalse(self.ws.exists())
        self.assertTrue(factory[0].kwargs["m03_light_only_public_watch"])
        self.assertFalse(result["execution_allowed"])

    def test_missing_btc_sqlite_does_not_bypass_owner_egress_policy(self):
        self.ws.unlink()
        code, out, err, checks, lights, deeps, factory = self.invoke(
            now=1794000010, btc_ok=False, public_policy_ok=False,
        )
        self.assertEqual(code, 2)
        self.assertIn("M03_EGRESS_POLICY_UNAVAILABLE", err)
        self.assertEqual(out, "")
        self.assertEqual(checks, [False])
        self.assertFalse(lights)
        self.assertFalse(deeps)
        self.assertFalse(factory)

    def test_original_systemd_m03_unit_does_not_require_btc_sqlite(self):
        unit = (Path(__file__).resolve().parents[1] / "deploy" /
                "indodax-v2-m03-mainpath.service").read_text()
        self.assertNotIn(
            "ConditionPathExists=%h/indodax-ai-bot-v2/runtime/"
            "public_ws_btc.sqlite3", unit,
        )
        self.assertIn(
            "ConditionPathExists=%h/indodax-ai-bot-v2/"
            "indodax_ai_bot/__main__.py", unit,
        )
        self.assertIn("--scheduled-public-scanner-once", unit)
        self.assertIn("Restart=no", unit)
        self.assertIn("INDODAX_TRADING_ENABLED=NO", unit)
        self.assertIn("INDODAX_ALLOW_LIVE_EXECUTION=NO", unit)
        self.assertIn("INDODAX_EXECUTION_MODE=DRY_RUN", unit)

    def test_explicit_canary_bypasses_deep_every_fifth_minute_even_if_btc_healthy(self):
        code, out, err, checks, lights, deeps, factory = self.invoke(
            now=1794000010, btc_ok=True, light_only=True,
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")
        doc = json.loads(out)
        self.assertTrue(doc["m03_light_only_canary_active"])
        self.assertEqual(doc["scheduled_m03_lane"], "LIGHT_ALL_IDR_EVERY_MINUTE")
        self.assertNotIn("m03_deep_deferred_reason", doc)
        self.assertEqual(checks, [False])
        self.assertEqual(len(lights), 1)
        self.assertFalse(deeps)
        self.assertTrue(factory[0].kwargs["m03_light_only_public_watch"])
        self.assertFalse(doc["execution_allowed"])
        self.assertFalse(doc["live_trading_authorized"])

    def test_explicit_canary_without_btc_db_preserves_original_light(self):
        self.ws.unlink()
        code, out, err, checks, lights, deeps, _factory = self.invoke(
            now=1794000010, btc_ok=False, light_only=True,
        )
        self.assertEqual(code, 0, err)
        doc = json.loads(out)
        self.assertEqual(doc["scheduled_m03_lane"], "LIGHT_ALL_IDR_EVERY_MINUTE")
        self.assertTrue(doc["m03_light_only_canary_active"])
        self.assertEqual(checks, [False])
        self.assertFalse(deeps)
        self.assertFalse(self.ws.exists())

    def test_canary_cannot_override_public_owner_egress_denial(self):
        code, out, err, checks, lights, deeps, factory = self.invoke(
            now=1794000010, btc_ok=True,
            public_policy_ok=False, light_only=True,
        )
        self.assertEqual(code, 2)
        self.assertIn("M03_EGRESS_POLICY_UNAVAILABLE", err)
        self.assertEqual(out, "")
        self.assertEqual(checks, [False])
        self.assertFalse(lights)
        self.assertFalse(deeps)
        self.assertFalse(factory)

    def test_canary_requires_original_scheduled_cli_action(self):
        with patch.dict(os.environ, self.env), patch(
            "sys.stdout", new_callable=io.StringIO,
        ) as out, patch("sys.stderr", new_callable=io.StringIO) as err:
            rc = cli.main(["--m03-light-only"])
        self.assertEqual(rc, 2)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("M03_LIGHT_ONLY_REQUIRES_SCHEDULED_ORIGINAL", err.getvalue())

    def test_public_owner_policy_failure_is_still_denied_before_scanner(self):
        code, out, err, checks, lights, deeps, factory = self.invoke(
            now=1794000010, btc_ok=True, public_policy_ok=False,
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("M03_EGRESS_POLICY_UNAVAILABLE", err)
        self.assertEqual(checks, [False])
        self.assertFalse(lights)
        self.assertFalse(deeps)
        self.assertFalse(factory)


if __name__ == "__main__":
    unittest.main()
