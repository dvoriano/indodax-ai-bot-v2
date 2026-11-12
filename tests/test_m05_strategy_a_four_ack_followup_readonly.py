"""Synthetic hardlocked tests for four original Strategy A ACK price followup."""
from __future__ import annotations

from unittest.mock import patch
import json
import unittest

from scripts import m05_strategy_a_four_ack_followup_readonly as app

FOUR = app.COHORT


def report(*, five=None, fifteen=None):
    five = {} if five is None else five
    fifteen = {} if fifteen is None else fifteen
    return {
        "status": "M05_ORIGINAL_WS3_POSTACK_QUALITY_AUDIT_READONLY",
        "research_evidence_source_status": "EXPLICIT_READONLY_RESEARCH_SOURCE",
        "execution_allowed": False, "orders_sent": 0,
        "telegram_messages_sent": 0,
        "events": [{
            "pair": pair,
            "status": "MATCHED_WS3_ACK",
            "delivery_state": "ACKED",
            "price_proxies": {
                "5m": five.get(pair, {
                    "status": "NO_FRESH_HORIZON_QUOTE",
                    "gross_move_bps": None}),
                "15m": fifteen.get(pair, {
                    "status": "HORIZON_NOT_MATURE",
                    "gross_move_bps": None}),
            },
        } for pair in FOUR],
    }


def move(bps):
    return {
        "status": "OBSERVED_PRICE_ONLY_NOT_EXECUTABLE",
        "gross_move_bps": bps,
        "post_ack_quote_wib": "2026-11-12 11:06:33 WIB",
        "horizon_quote_wib": "2026-11-12 11:21:33 WIB",
        "assumed_cost_adjusted_bps": None,
    }


class OriginalFourAckPriceFollowupTests(unittest.TestCase):
    def test_original_observations_never_invent_four_winners(self):
        r = report(
            five={"nbtnew_idr": move("360.13")},
            fifteen={"aaplx_idr": move("-740.76")})
        result = app.summarize_four_signal_quality_nonlive(r)
        self.assertEqual(result["status"],
                         "M05_STRATEGY_A_FOUR_ACK_PRICE_FOLLOWUP_READONLY")
        self.assertEqual(result["quality_counts"]["5m"]["observed_price_only"], 1)
        self.assertEqual(result["quality_counts"]["15m"]["observed_price_only"], 1)
        self.assertEqual(result["quality_counts"]["15m"]["not_mature"], 3)
        self.assertEqual(result["quality_counts"]["5m"]["observed_price_positive"], 1)
        self.assertEqual(result["quality_counts"]["15m"]["observed_price_negative"], 1)
        self.assertIsNone(result["events"][0]["5m"]["gross_last_price_pct"])
        self.assertEqual(result["events"][1]["5m"]["gross_last_price_pct"],
                         "3.6013")
        self.assertEqual(result["events"][0]["15m"]["gross_last_price_pct"],
                         "-7.4076")
        self.assertTrue(result["observed_price_direction_is_not_trade_win_rate"])
        self.assertFalse(result["execution_allowed"])
        self.assertEqual(result["orders_sent"], 0)
        self.assertEqual(result["telegram_sent_by_audit"], 0)
        self.assertFalse(result["service_restarted"])

    def test_matured_recovery_but_missing_quote_is_not_zero_return(self):
        r = report(fifteen={
            "aaplx_idr": move("-740.76"),
            "nbtnew_idr": move("275.00"),
            "neon_idr": {"status": "NO_FRESH_POST_ACK_ENTRY_QUOTE"},
            "anoa_idr": {"status": "NO_FRESH_HORIZON_QUOTE"},
        })
        result = app.summarize_four_signal_quality_nonlive(r)
        counts = result["quality_counts"]["15m"]
        self.assertEqual(counts["observed_price_only"], 2)
        self.assertEqual(counts["not_mature"], 0)
        self.assertEqual(counts["no_fresh_entry_quote"], 1)
        self.assertEqual(counts["no_fresh_horizon_quote"], 1)
        self.assertEqual(counts["observed_price_positive"], 1)
        self.assertEqual(counts["observed_price_negative"], 1)
        self.assertIsNone(result["events"][2]["15m"]["gross_last_price_bps"])

    def test_reject_extra_missing_or_duplicate_pair(self):
        for broken in ("MISSING", "DUPLICATE", "EXTRA"):
            with self.subTest(broken=broken):
                r = report()
                if broken == "MISSING":
                    r["events"].pop()
                elif broken == "DUPLICATE":
                    r["events"][1]["pair"] = FOUR[0]
                else:
                    r["events"][0]["pair"] = "different_idr"
                with self.assertRaises(ValueError):
                    app.summarize_four_signal_quality_nonlive(r)

    def test_non_ack_or_non_ws3_ack_must_not_pass(self):
        r = report()
        r["events"][0]["delivery_state"] = "UNCERTAIN"
        with self.assertRaises(ValueError):
            app.summarize_four_signal_quality_nonlive(r)
        r = report()
        r["events"][0]["status"] = "ACK_NOT_MATCHED_WS3_SOURCE"
        with self.assertRaises(ValueError):
            app.summarize_four_signal_quality_nonlive(r)

    def test_missing_research_and_unsafe_transport_block(self):
        for edit in ("research_evidence_source_status",
                     "execution_allowed", "orders_sent",
                     "telegram_messages_sent"):
            with self.subTest(edit=edit):
                r = report()
                if edit == "research_evidence_source_status":
                    r[edit] = "NOT_REQUESTED"
                elif edit == "execution_allowed":
                    r[edit] = True
                else:
                    r[edit] = 1
                with self.assertRaises(ValueError):
                    app.summarize_four_signal_quality_nonlive(r)

    def test_unknown_or_untrusted_price_state_fails_closed(self):
        r = report(fifteen={"neon_idr": {"status": "SUCCESS_BUT_NOT_REAL"}})
        with self.assertRaises(ValueError):
            app.summarize_four_signal_quality_nonlive(r)
        for invalid in ("NaN", "Infinity", "wrong", "100000000000"):
            with self.subTest(invalid=invalid):
                r = report(five={"nbtnew_idr": move(invalid)})
                with self.assertRaises(ValueError):
                    app.summarize_four_signal_quality_nonlive(r)

    def test_output_does_not_include_owner_secret_or_api_key(self):
        raw = json.dumps(app.summarize_four_signal_quality_nonlive(report()))
        for forbidden in ("telegram_message_id", "owner_user_id", "BOT_TOKEN",
                          "INDODAX_KEY", "account_fingerprint",
                          "original_private_path", "owner_db"):
            self.assertNotIn(forbidden, raw)

    def test_original_readonly_integration_mock_and_pid_stability(self):
        result = report(five={"nbtnew_idr": move("360.13")})
        with (
            patch.object(app, "_unit_pid", side_effect=[588864, 588864]),
            patch.object(app, "_process_env",
                         return_value={"owner_db": "/tmp/original.sqlite3"}),
            patch.object(app, "inspect_postack_quality_readonly",
                         return_value=result) as audit,
        ):
            out = app.inspect_original_four_ack_followup_readonly()
        self.assertEqual(out["original_m05_pid"], 588864)
        self.assertEqual(out["status"],
                         "M05_STRATEGY_A_FOUR_ACK_PRICE_FOLLOWUP_READONLY")
        kwargs = audit.call_args.kwargs
        self.assertEqual(kwargs["pairs"], FOUR)
        self.assertNotIn("fee_bps", kwargs)
        self.assertNotIn("slippage_bps", kwargs)
        self.assertFalse(out["execution_allowed"])

    def test_pid_change_or_untrusted_process_fails_closed(self):
        for starts in ([588864, 588865], [588865, 588864]):
            with self.subTest(starts=starts):
                with (
                    patch.object(app, "_unit_pid", side_effect=starts),
                    patch.object(app, "_process_env",
                                 return_value={"owner_db": "/tmp/owner.sqlite3"}),
                    patch.object(app, "inspect_postack_quality_readonly",
                                 return_value=report()),
                ):
                    out = app.inspect_original_four_ack_followup_readonly()
                self.assertEqual(out["status"],
                    "M05_STRATEGY_A_FOUR_ACK_FOLLOWUP_BLOCKED_READONLY")
                self.assertFalse(out["service_restarted"])
        with patch.object(app, "_unit_pid",
                          side_effect=OSError("BOT_TOKEN=secret")):
            out = app.inspect_original_four_ack_followup_readonly()
        self.assertNotIn("secret", json.dumps(out))
        self.assertFalse(out["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
