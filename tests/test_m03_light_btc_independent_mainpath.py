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
    def _outcome(*, bulk=None):
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

    def invoke(self, *, now, btc_ok, public_policy_ok=True):
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
                self._outcome()
            )
            orchestrator.return_value.run_cycle.return_value = (
                self._outcome(bulk={
                    "coverage_status": "NO_BULK_EVIDENCE",
                    "g1_verified": False,
                    "execution_allowed": False,
                })
            )
            status = cli.main(self.args)
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
