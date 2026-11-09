"""M03 original official REST 403/429 status classes, offline NO network/trades."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from unittest.mock import patch
import unittest

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.public_market import IndodaxPublicMarket, PublicDataError


def block_http(code: int) -> HTTPError:
    # Deliberately include a fake secret in remote reason. The adapter
    # must never echo any HTTP error body, headers or arbitrary message.
    return HTTPError("https://indodax.com/api/pairs", code,
                     "UNTRUSTED_DO_NOT_ECHO_SECRET", {}, None)


class M03PublicRestTypedReasonsTests(unittest.TestCase):
    def test_403_propagates_as_fixed_safe_reason_to_original_scanner(self):
        with TemporaryDirectory() as td:
            client = IndodaxPublicMarket(
                fetch=lambda _: None, clock=lambda: 1794236900,
            )
            # Reuse REAL official REST adapter and scanner/orchestrator, but
            # inject synthetic urllib transport failure, no real socket.
            client._fetch = client._http_json
            app = DailyScalpingOrchestrator(
                Settings.from_env(), DecisionJournal(Path(td) / "m03.sqlite3"),
            )
            with patch("indodax_ai_bot.public_market.urlopen",
                       side_effect=block_http(403)) as network:
                result = app.run_scan_cycle("M03_HTTP_403_OFFLINE", market=client)
            self.assertEqual(network.call_count, 1)
            self.assertEqual(result.reason,
                             "PUBLIC_SCANNER_INVALID:PUBLIC_REST_HTTP_403_FORBIDDEN")
            self.assertFalse(result.execution_allowed)
            self.assertIsNone(result.screening)
            self.assertNotIn("UNTRUSTED_DO_NOT_ECHO_SECRET", result.reason)

    def test_typed_429_5xx_other_status_and_generic_transport(self):
        client = IndodaxPublicMarket(fetch=lambda _: None)
        for http, expected in (
            (403, "PUBLIC_REST_HTTP_403_FORBIDDEN"),
            (429, "PUBLIC_REST_HTTP_429_RATE_LIMITED"),
            (502, "PUBLIC_REST_HTTP_5XX_UNAVAILABLE"),
            (503, "PUBLIC_REST_HTTP_5XX_UNAVAILABLE"),
            (504, "PUBLIC_REST_HTTP_5XX_UNAVAILABLE"),
            (401, "PUBLIC_HTTP_OR_JSON_UNAVAILABLE"),
        ):
            with self.subTest(http=http), patch(
                "indodax_ai_bot.public_market.urlopen",
                side_effect=block_http(http),
            ):
                with self.assertRaises(PublicDataError) as caught:
                    client._http_json("/api/pairs")
                self.assertEqual(str(caught.exception), expected)
                self.assertNotIn("UNTRUSTED_DO_NOT_ECHO_SECRET",
                                 str(caught.exception))

    def test_network_oserror_keeps_previous_safe_generic_reason(self):
        client = IndodaxPublicMarket(fetch=lambda _: None)
        with patch("indodax_ai_bot.public_market.urlopen",
                   side_effect=OSError("DO_NOT_LOG_HOST_SECRET")):
            with self.assertRaisesRegex(
                PublicDataError, "PUBLIC_HTTP_OR_JSON_UNAVAILABLE"
            ):
                client._http_json("/api/pairs")


if __name__ == "__main__":
    unittest.main()
