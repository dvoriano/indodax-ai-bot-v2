"""Prevent stale stablecoin-denominated principal from re-entering V2 docs/tests."""
from __future__ import annotations

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
STALE_UNITS = re.compile(
    r"\b(?:5[01])\s*(?:USDT)\b|\bUSDT\s*(?:5[01])\b|"
    r"\busdt[-_](?:5[01])\b|(?:5[01])[-_]usdt\b",
    re.IGNORECASE,
)


class IDROnlyCapitalPolicyTests(unittest.TestCase):
    def test_no_old_unit_nominals_in_current_text_files(self):
        inspected = 0
        paths = (
            [ROOT / "README.md", ROOT / "AGENTS.md"]
            + list((ROOT / "docs").glob("*.md"))
            + list((ROOT / "indodax_ai_bot").glob("*.py"))
            + list((ROOT / "tests").glob("*.py"))
        )
        for path in paths:
            if not path.is_file():
                continue
            inspected += 1
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertIsNone(STALE_UNITS.search(path.read_text()))
        self.assertGreater(inspected, 100)

    def test_policy_explicitly_distinguishes_settlement_from_available_idr(self):
        guideline = (ROOT / "docs/PROJECT_GUIDELINE.md").read_text()
        roadmap = (ROOT / "docs/ROADMAP.md").read_text()
        self.assertIn("Modal utama V2 IDR-ONLY", guideline)
        self.assertIn("tidak membuktikan", guideline)
        self.assertIn("hasil konversi Rp923.916", guideline)
        self.assertIn("Rp920.078", roadmap)
        self.assertIn("spendable_idr_verified=false", roadmap)
        self.assertIn("M07 checklist **0/5**", roadmap)


if __name__ == "__main__":
    unittest.main()
