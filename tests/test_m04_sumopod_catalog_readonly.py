"""M04 one owner-opt-in Sumopod GET /models metadata, NEVER role inference."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import os
import unittest
from unittest.mock import patch

from indodax_ai_bot.m04_model_catalog import (
    MODELS_URL, M04CatalogDenied, _decode_catalog,
    inspect_sumopod_model_catalog_readonly_once,
)
from ops.m04_sumopod_model_catalog_once import main

SOURCE = {
    "SUMOPOD_API_KEY": "DONT_ECHO_PRIVATE_OWNER_KEY",
    "SUMOPOD_MODEL_MARKET": "glm-5.3-flash",
    "SUMOPOD_MODEL_RISK": "glm-5.3-flash",
    "SUMOPOD_MODEL_STRATEGY": "glm-5.3-flash",
}
SAFE_ENV = {
    "INDODAX_TRADING_ENABLED": "NO",
    "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
    "INDODAX_EXECUTION_MODE": "DRY_RUN",
    "M04_MODEL_CATALOG_READONLY_GO": "YES",
}


class M04MetadataCatalogReadOnlyTests(unittest.TestCase):
    def test_exact_one_metadata_get_and_redacted_source_status(self):
        calls = []
        def transport(method, url, headers, timeout):
            calls.append((method, url, dict(headers), timeout))
            return {
                "object": "list",
                "data": [
                    {"id": "glm-5.3-flash", "object": "model"},
                    {"id": "other-unused-model", "object": "model"},
                ],
            }
        r = inspect_sumopod_model_catalog_readonly_once(
            source=SOURCE, allow_metadata_network=True,
            transport=transport,
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0:2], ("GET", MODELS_URL))
        self.assertEqual(calls[0][3], 6.0)
        self.assertEqual(calls[0][2]["Authorization"],
                         "Bearer " + SOURCE["SUMOPOD_API_KEY"])
        self.assertEqual(r["status"], "M04_MODELS_LISTED_METADATA_ONLY")
        self.assertEqual(r["selected_model_ids_present_in_catalog"],
                         {"MARKET": True, "RISK": True, "STRATEGY": True})
        self.assertFalse(r["actual_provider_inference_verified"])
        self.assertFalse(r["actual_provider_pricing_verified"])
        self.assertFalse(r["paid_ai_go_authorized"])
        self.assertFalse(r["g1_independent_issuer_verified"])
        self.assertEqual(r["paid_chat_completion_calls"], 0)
        self.assertFalse(r["execution_allowed"])
        self.assertEqual(r["final_decision"], "REJECT")
        self.assertNotIn(SOURCE["SUMOPOD_API_KEY"], str(r))

    def test_unlisted_owner_id_stays_metadata_only_no_inference(self):
        source = {**SOURCE, "SUMOPOD_MODEL_RISK": "missing-model"}
        r = inspect_sumopod_model_catalog_readonly_once(
            source=source, allow_metadata_network=True,
            transport=lambda m,u,h,t: {
                "object": "list",
                "data": [{"id": "glm-5.3-flash"}],
            },
        )
        self.assertEqual(r["status"], "M04_SELECTED_MODEL_IDS_NOT_ALL_LISTED")
        self.assertFalse(r["selected_model_ids_present_in_catalog"]["RISK"])
        self.assertFalse(r["execution_allowed"])

    def test_absent_optin_rejects_before_any_network(self):
        attempted = []
        with self.assertRaisesRegex(
                M04CatalogDenied, "EXPLICIT_NETWORK_GO_REQUIRED"):
            inspect_sumopod_model_catalog_readonly_once(
                source=SOURCE,
                transport=lambda *args: attempted.append(args),
            )
        self.assertEqual(attempted, [])
        output = StringIO()
        with patch.dict(os.environ, SAFE_ENV, clear=True), patch(
                "ops.m04_sumopod_model_catalog_once.owner_provider_source",
                side_effect=AssertionError("NO_PROTECTED_FILE_READ")) as owner:
            with redirect_stdout(output):
                code = main([])
        self.assertEqual(code, 3)
        self.assertIn("EXPLICIT_NETWORK_GO_REQUIRED", output.getvalue())
        owner.assert_not_called()

    def test_incomplete_configuration_denied_before_metadata_get(self):
        requests = []
        bad = {**SOURCE, "SUMOPOD_API_KEY": ""}
        with self.assertRaisesRegex(
                M04CatalogDenied, "LOCAL_CONFIG_NOT_READY"):
            inspect_sumopod_model_catalog_readonly_once(
                source=bad, allow_metadata_network=True,
                transport=lambda *args: requests.append(args),
            )
        self.assertEqual(requests, [])

    def test_invalid_json_duplicate_models_and_oversize_fail_closed(self):
        payloads = (
            '{"object":"list","data":[{"id":"a"},{"id":"a"}]}',
            '{"object":"list","data":[{"id":"a"}],"data":[]}',
            '{"object":"list","data":[{"id":"\u0020"}]}',
            '{"object":"list","data":[],"price":NaN}',
            {"object": "list", "data": [{"id": "INVALID KEY"}]},
            {"object": "list", "data": "not-list"},
            "X" * 300000,
        )
        for payload in payloads:
            with self.subTest(payload=str(payload)[:60]):
                with self.assertRaises(M04CatalogDenied):
                    _decode_catalog(payload)

    def test_failed_provider_never_exposes_secret_exception(self):
        with self.assertRaisesRegex(
                M04CatalogDenied, "PROVIDER_GET_UNAVAILABLE") as err:
            inspect_sumopod_model_catalog_readonly_once(
                source=SOURCE, allow_metadata_network=True,
                transport=lambda *args: (_ for _ in ()).throw(
                    RuntimeError("PRIVATE_HEADER_EXPOSED_KEY")
                ),
            )
        self.assertNotIn("PRIVATE_HEADER", str(err.exception))
        self.assertNotIn(SOURCE["SUMOPOD_API_KEY"], str(err.exception))

    def test_cli_protected_owner_source_real_function_fake_get_and_no_post(self):
        out = StringIO()
        with patch.dict(os.environ, SAFE_ENV, clear=True), patch(
                "ops.m04_sumopod_model_catalog_once.owner_provider_source",
                return_value=(SOURCE, "M04_V2_KEY_FILE_PRESENT_LOCAL_ONLY"),
        ) as source, patch(
                "indodax_ai_bot.m04_model_catalog._metadata_get",
                return_value=json.dumps({
                    "object": "list", "data": [{"id": "glm-5.3-flash"}],
                }).encode(),
        ) as get, patch(
                "indodax_ai_bot.sumopod_provider._post",
                side_effect=AssertionError("NO_BILLABLE_AI_POST"),
        ) as paid:
            with redirect_stdout(out):
                rc = main(["--metadata-network-go"])
        self.assertEqual(rc, 0)
        report = json.loads(out.getvalue())
        self.assertEqual(report["status"], "M04_MODELS_LISTED_METADATA_ONLY")
        self.assertEqual(report["paid_chat_completion_calls"], 0)
        self.assertFalse(report["execution_allowed"])
        self.assertNotIn(SOURCE["SUMOPOD_API_KEY"], out.getvalue())
        source.assert_called_once()
        get.assert_called_once()
        paid.assert_not_called()

    def test_cli_explicit_go_cannot_override_live_trading_forbidden(self):
        out = StringIO()
        with patch.dict(os.environ, {
            **SAFE_ENV, "INDODAX_TRADING_ENABLED": "YES",
        }, clear=True), patch(
                "ops.m04_sumopod_model_catalog_once.owner_provider_source",
                side_effect=AssertionError("NO_PRIVATE_READ")) as source:
            with redirect_stdout(out):
                code = main(["--metadata-network-go"])
        self.assertEqual(code, 3)
        self.assertIn('"execution_allowed": false', out.getvalue())
        source.assert_not_called()


if __name__ == "__main__":
    unittest.main()
