"""CLI -> existing DailyScalpingOrchestrator -> M06 HTTPS seam -> reject journal.

Mocked exchange only. Never opens a private connection.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from indodax_ai_bot.__main__ import main


class M06OriginalPrivateGOCLI(unittest.TestCase):
    def test_explicit_go_cli_to_original_mainpath_redacts_wallet(self):
        with tempfile.TemporaryDirectory() as td:
            directory=Path(td)
            now=int(time.time()*1000)
            go=directory/"go.json"
            go.write_text(json.dumps({
                "approval_id":"M06_PRIVATE_READONLY_GO_20261031",
                "issued_ms":now-1000,
                "expires_ms":now+899000,
                "max_requests":20,
                "symbol":"injidr",
            }))
            go.chmod(0o600)
            creds=directory/"credentials.json"
            creds.write_text(json.dumps({
                "api_key":"fixture-key","secret_key":"fixture-secret"}))
            creds.chmod(0o600)
            payload={
                "/api/v2/account":{"uid":192837465,
                    "canTrade":False,"canWithdraw":False,
                    "canWithdrawFiat":False,
                    "balances":[{"asset":"IDR",
                        "free":"987654321","locked":"123456"}]},
                "/api/v2/openOrders":[],
                "/api/v2/order/histories":{"data":[]},
                "/api/v2/myTrades":{"data":[]},
            }
            seen=[]
            def fixture(request):
                seen.append(request.path)
                return payload[request.path]
            output=io.StringIO()
            with patch.dict(os.environ,{
                "INDODAX_TRADING_ENABLED":"NO",
                "INDODAX_ALLOW_LIVE_EXECUTION":"NO",
                "INDODAX_EXECUTION_MODE":"DRY_RUN",
            }), patch("indodax_ai_bot.m06_scoped_https._read_json_over_verified_tls",
                      side_effect=fixture), contextlib.redirect_stdout(output):
                rc=main(["--db",str(directory/"journal.sqlite3"),
                    "--m06-private-readonly-once",
                    "--m06-go-file",str(go),
                    "--m06-credentials-file",str(creds),
                    "--m06-budget-db",str(directory/"budget.sqlite3")])
            self.assertEqual(rc,0)
            self.assertEqual(len(seen),4)
            report=json.loads(output.getvalue())
            self.assertFalse(report["execution_allowed"])
            self.assertFalse(report["history_complete"])
            self.assertTrue(report["view_only_permissions_verified"])
            self.assertNotIn("fixture-key",output.getvalue())
            self.assertNotIn("fixture-secret",output.getvalue())
            self.assertNotIn("987654321",output.getvalue())
            self.assertNotIn("192837465",output.getvalue())

    def test_missing_go_never_invokes_network(self):
        with patch.dict(os.environ,{
                "INDODAX_TRADING_ENABLED":"NO",
                "INDODAX_ALLOW_LIVE_EXECUTION":"NO",
                "INDODAX_EXECUTION_MODE":"DRY_RUN",
            }), patch("indodax_ai_bot.m06_scoped_https._read_json_over_verified_tls"
                      ) as network, contextlib.redirect_stderr(io.StringIO()):
            rc=main(["--m06-private-readonly-once"])
            self.assertEqual(rc,2)
            network.assert_not_called()


if __name__=="__main__":
    unittest.main()
