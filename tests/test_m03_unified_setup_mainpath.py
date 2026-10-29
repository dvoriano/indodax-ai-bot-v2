"""M03 source-bound setup-stage handoff to existing G1/M04 preview.

No network, paid models, account, trading, or future candle leakage.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
import hmac
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest
from unittest.mock import patch

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.deep_analysis import (
    DeepCandidate, _quality_review, _same_window_gate,
    bulk_public_deep_scan, classify_candidate_entry_stage,
)
from indodax_ai_bot.three_role_ai import ThreeRoleShadow
from indodax_ai_bot.g1_receipt import G1ReceiptVerifier, canonical_receipt_bytes
from indodax_ai_bot.scanner import DailyPairScanner
from test_m04a_three_role_shadow import RoleCaller, TEST_G1_SECRET
from test_m03b_deep import observation
from test_m03g_bulk import FakeBulkMarket, fake_screen
from test_m03_volume_screen import boosted_fake


def candidate(pair, *, score=85, structure=None, early=None, rsi="55"):
    return DeepCandidate(
        pair=pair, score=score, state="PUBLIC_EVIDENCE_UNATTESTED",
        rejection_reason=None, closed_1m_epoch=1800000120,
        rsi14=Decimal(rsi), ema8=Decimal("101"), ema21=Decimal("100"),
        volume_ratio=Decimal("2"),
        spread_bps=Decimal("18"), book_imbalance=Decimal("0.2"),
        visible_buy_idr=Decimal("200"), visible_sell_idr=Decimal("100"),
        market_structure_diagnostic=(
            {"label": structure, "closed_1m_epoch": 1800000120,
             "trade_signal": False, "g1_verified": False}
            if structure else None
        ),
        early_momentum={"status": early, "source_trust_verified": False}
        if early else None,
    )


class M03UnifiedSetupMainpathTests(unittest.TestCase):
    def test_existing_momentum_structure_exhaustion_reused_without_new_score(self):
        for expected, props in (
            ("EARLY_ACCELERATION_WATCH", {"early": "EARLY_WATCH_UNCALIBRATED"}),
            ("PULLBACK_REBOUND_WATCH", {"structure": "PULLBACK_REBOUND_OBSERVED"}),
            ("BREAKOUT_CONTINUATION_WATCH", {"structure": "SURGE_BREAKOUT_OBSERVED"}),
            ("FALSE_BREAKOUT_CAUTION", {"structure": "FALSE_BREAKOUT_RISK",
                                         "early": "EARLY_WATCH_UNCALIBRATED"}),
            ("EXHAUSTED_PUMP_CAUTION", {"early": "EXHAUSTION_CAUTION"}),
        ):
            with self.subTest(expected=expected):
                c = candidate("token_idr", **props)
                s = classify_candidate_entry_stage(c)
                self.assertEqual(s["stage"], expected)
                self.assertEqual(c.summary()["scalp_setup_stage"], s)
                self.assertFalse(s["entry_timing_confirmed"])
                self.assertFalse(s["g1_verified"])
                self.assertFalse(s["trade_authorized"])

    def test_high_score_exhausted_pump_and_false_breakout_cannot_win_preview(self):
        for suspect in (
            candidate("bad_idr", score=99, early="EXHAUSTION_CAUTION"),
            candidate("bad_idr", score=99, structure="FALSE_BREAKOUT_RISK",
                      early="EARLY_WATCH_UNCALIBRATED"),
            candidate("bad_idr", score=99, rsi="78"),
        ):
            with self.subTest(stage=classify_candidate_entry_stage(suspect)["stage"]):
                review = _quality_review(suspect, "BTC_UPTREND_DIAGNOSTIC")
                self.assertFalse(review["clear_of_provisional_cautions"])
                self.assertTrue(any("CAUTION" in flag or "RSI" in flag
                                    for flag in review["review_flags"]))
                self.assertEqual(review["score"], 99)

    def test_same_closed_minute_prefers_clean_pullback_early_over_raw_high_score(self):
        pairs = ("raw_idr", "pull_idr", "early_idr", "exhausted_idr", "fakeout_idr")
        scored = (
            candidate("raw_idr", score=95),
            candidate("pull_idr", score=80, structure="PULLBACK_REBOUND_OBSERVED"),
            candidate("early_idr", score=84, early="EARLY_WATCH_UNCALIBRATED"),
            candidate("exhausted_idr", score=99, early="EXHAUSTION_CAUTION"),
            candidate("fakeout_idr", score=98, structure="FALSE_BREAKOUT_RISK",
                      early="EARLY_WATCH_UNCALIBRATED"),
        )
        obs = {p: observation(p) for p in pairs}
        btc = observation("btc_idr")
        gate = _same_window_gate(fake_screen(), scored, obs, btc)
        self.assertEqual(gate["status"], "ALIGNED_PUBLIC_UNATTESTED")
        self.assertEqual(gate["ranked_same_window_diagnostics"][0]["pair"],
                         "exhausted_idr")
        self.assertEqual(gate["provisional_watchlist_preview"],
                         ["pull_idr", "early_idr"])
        self.assertFalse(gate["g2_candidate_authorized"])
        self.assertFalse(gate["live_trading_authorized"])

    def test_original_bulk_market_digest_works_for_preview_outside_score_top10(self):
        def assigned(obs, *, early_momentum=None):
            p = obs.pair
            if p == "token018_idr":
                return candidate(p, score=78, structure="PULLBACK_REBOUND_OBSERVED")
            if p == "btc_idr":
                return candidate(p, score=99, rsi="78")
            return candidate(p, score=96, rsi="78")
        with patch("indodax_ai_bot.deep_analysis.analyze_observation",
                   side_effect=assigned):
            data = bulk_public_deep_scan(FakeBulkMarket(), fake_screen())
        # BTC is context and a possible candidate; its synthetic RSI=78
        # intentionally gives it an overbought quality caution so this
        # test isolates exactly ONE approved preview (token018_idr).
        self.assertEqual(data["eligible_cohort"]["status"], "ALIGNED_PUBLIC_UNATTESTED")
        self.assertEqual(
            data["eligible_cohort"]["provisional_watchlist_preview"],
            ["token018_idr"],
        )
        self.assertNotIn(
            "token018_idr",
            [x["pair"] for x in data["ranked_diagnostics_top10"]],
        )
        self.assertEqual(
            data["provisional_market_evidence"][0]["pair"],
            "token018_idr",
        )
        self.assertEqual(
            data["provisional_market_evidence"][0]["scalp_setup_stage"]["stage"],
            "PULLBACK_REBOUND_WATCH",
        )
        # No issuer means no model calls. Receipt hash still reaches the
        # correct pair even when it was not in scalar-score top10.
        shadow = ThreeRoleShadow(clock=lambda: 1800000140).run("trace", data)
        self.assertEqual(shadow["reason"], "G1_PUBLIC_EVIDENCE_UNTRUSTED")
        self.assertEqual(shadow["candidate_pair"], "token018_idr")
        self.assertEqual(len(shadow["source_digest_sha256"]), 64)
        self.assertFalse(shadow["execution_allowed"])

    def test_quality_ranked_outside_top10_reaches_market_role_only_with_synthetic_receipt(self):
        # A second, intentionally test-only full chain demonstrates that the
        # chosen low-scalar-score PULLBACK enters MARKET's immutable source
        # digest, but a signed *internal synthetic* receipt still cannot BUY.
        def assigned(obs, *, early_momentum=None):
            if obs.pair == "token018_idr":
                return candidate(obs.pair, score=78,
                                 structure="PULLBACK_REBOUND_OBSERVED")
            if obs.pair == "btc_idr":
                return candidate(obs.pair, score=99, rsi="78")
            return candidate(obs.pair, score=96, rsi="78")
        with patch("indodax_ai_bot.deep_analysis.analyze_observation",
                   side_effect=assigned):
            data = bulk_public_deep_scan(FakeBulkMarket(), fake_screen())
        selected = data["eligible_cohort"]["provisional_watchlist_preview"]
        self.assertEqual(selected, ["token018_idr"])
        for key in ("g1_verified", "source_trust_verified", "trade_history_complete"):
            data[key] = True
            data["eligible_cohort"][key] = True
        before = ThreeRoleShadow(clock=lambda: 1800000140).run("trace", data)
        self.assertEqual(before["reason"], "G1_ISSUER_RECEIPT_UNVERIFIED")
        digest = before["source_digest_sha256"]
        claims = {
            "schema_version": 1, "issuer_id": "synthetic-unit-issuer",
            "trace_id": "trace", "pair": "token018_idr",
            "closed_1m_epoch": 1800000120,
            "source_digest_sha256": digest, "expires_epoch": 1800000180,
        }
        data["g1_attestation"] = {
            "claims": claims,
            "mac_sha256": hmac.new(
                TEST_G1_SECRET, canonical_receipt_bytes(claims),
                sha256,
            ).hexdigest(),
        }
        verifier = G1ReceiptVerifier(
            issuer_id="synthetic-unit-issuer",
            verification_secret=TEST_G1_SECRET,
        )
        role_caller = RoleCaller()
        out = ThreeRoleShadow(
            caller=role_caller, clock=lambda: 1800000140,
            source_verifier=verifier,
        ).run("trace", data)
        self.assertEqual(role_caller.calls, ["MARKET", "RISK", "STRATEGY"])
        self.assertEqual(role_caller.inputs[0]["source"]["pair"], "token018_idr")
        self.assertEqual(
            role_caller.inputs[0]["market_evidence"]["scalp_setup_stage"]["stage"],
            "PULLBACK_REBOUND_WATCH",
        )
        self.assertEqual(out["final_decision"], "REJECT")
        self.assertFalse(out["execution_allowed"])
        self.assertFalse(out["g2_candidate_authorized"])

    def test_original_orchestrator_150m_floor_no_trade_on_same_pipeline(self):
        fake = boosted_fake(high=22, amount="150000000")
        fake.now = 1800000130
        for ticker in fake.tickers.values():
            ticker["server_time"] = fake.now - 1
        screening = DailyPairScanner(fake.client()).scan()
        class TimedMarket(FakeBulkMarket):
            def __init__(self):
                super().__init__()
                self.now = fake.now
        market = TimedMarket()
        with TemporaryDirectory() as td:
            app = DailyScalpingOrchestrator(
                Settings.from_env({}), DecisionJournal(Path(td) / "db.sqlite3")
            )
            with patch.object(app, "_scan_with_early_momentum",
                              return_value=screening):
                result = app.run_bulk_deep_cycle(
                    "setup-volume-bridge", market=market,
                    min_volume_idr=Decimal("150000000"),
                )
            self.assertIsNotNone(result.bulk)
            self.assertEqual(result.bulk["attempted_count"], 20)
            self.assertTrue(all(
                p in {f"m{i:03d}_idr" for i in range(22)}
                for p in result.bulk["requested_pairs"]
            ))
            self.assertEqual(result.final_decision.value, "REJECT")
            self.assertFalse(result.execution_allowed)
            self.assertFalse(result.bulk["g1_verified"])
            self.assertFalse(result.ai_shadow["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
