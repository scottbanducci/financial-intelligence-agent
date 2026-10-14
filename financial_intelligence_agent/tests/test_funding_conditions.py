"""Credential-free unit tests: python -m unittest discover -s tests -p test_funding_conditions.py -v"""

import json
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from financial_intelligence_agent.analytics.series import align_subtract, index_daily_rows
from financial_intelligence_agent.tools.funding_conditions import FundingConditionsTool, InMemoryRatesReader
from decimal import Decimal


def row(d, sofr, effr, iorb, dtb3=None, *, stored=True):
    result = {"observation_date": d, "sofr": sofr, "effr": effr, "iorb": iorb, "dtb3": dtb3}
    if stored and None not in (sofr, effr, iorb):
        result["sofr_iorb_spread_bps"] = (sofr - iorb) * 100
        result["sofr_effr_spread_bps"] = (sofr - effr) * 100
        result["effr_iorb_spread_bps"] = (effr - iorb) * 100
        if dtb3 is not None:
            result["dtb3_effr_spread_bps"] = (dtb3 - effr) * 100
    return result


class TestFundingConditions(unittest.TestCase):
    def tool(self, rows):
        return FundingConditionsTool(InMemoryRatesReader(rows), snapshot_id="fixture-1")

    def test_latest_complete_date_and_floating_point(self):
        # Incorporates the user-verified October 8, 2026 example.
        rows = [
            row("2026-10-01", 3.87, 3.88, 3.90, 4.00),
            row("2026-10-07", 3.88, 3.88, 3.90, 4.05),
            row("2026-10-08", 3.87, 3.88, 3.90, 4.05),
            row("2026-10-09", None, None, 3.90),
            row("2026-10-10", None, None, 3.90),
        ]
        result = self.tool(rows).get_funding_conditions("2026-10-10", 7)
        self.assertEqual(result["window"]["actual_end_date"], "2026-10-08")
        self.assertEqual(result["window"]["end_date_gap_days"], 2)
        self.assertEqual(result["window"]["target_start_date"], "2026-10-01")
        self.assertEqual(result["window"]["actual_baseline_date"], "2026-10-01")
        s = result["spreads"]
        self.assertEqual(s["sofr_iorb_spread_bps"]["end_bps"], -3.0)
        self.assertEqual(s["sofr_effr_spread_bps"]["end_bps"], -1.0)
        self.assertEqual(s["effr_iorb_spread_bps"]["end_bps"], -2.0)
        self.assertEqual(s["dtb3_effr_spread_bps"]["end_bps"], 17.0)
        self.assertEqual(s["dtb3_effr_spread_bps"]["change_bps"], 5.0)
        self.assertEqual(s["sofr_iorb_spread_bps"]["observations"], 3)
        self.assertTrue(result["evidence"])
        json.dumps(result)  # Output is JSON serializable without custom encoders.

    def test_missing_optional_dtb3_is_not_zero(self):
        rows = [row("2026-10-01", 3.87, 3.88, 3.9, 4.0), row("2026-10-08", 3.87, 3.88, 3.9)]
        result = self.tool(rows).get_funding_conditions("2026-10-08", 7)
        self.assertIsNone(result["latest_rates_percent"]["dtb3"])
        self.assertEqual(result["spreads"]["dtb3_effr_spread_bps"]["status"], "unavailable_at_end_date")

    def test_calendar_lag_not_row_lag(self):
        left = {date(2026, 10, 8): Decimal("3.87"), date(2026, 10, 9): Decimal("3.88")}
        right = {date(2026, 9, 8): Decimal("3.50"), date(2026, 9, 10): Decimal("3.51")}
        actual = align_subtract(left, right, right_lag_calendar_days=30)
        self.assertEqual(actual, {date(2026, 10, 8): Decimal("0.37")})

    def test_stored_spread_mismatch_rejected(self):
        bad = row("2026-10-08", 3.87, 3.88, 3.90)
        bad["sofr_effr_spread_bps"] = -10.0
        with self.assertRaisesRegex(ValueError, "inconsistent sofr_effr_spread_bps"):
            self.tool([bad]).get_funding_conditions("2026-10-10", 7)

    def test_null_derived_spread_with_complete_inputs_rejected(self):
        bad = row("2026-10-08", 3.87, 3.88, 3.90)
        bad["sofr_iorb_spread_bps"] = None
        with self.assertRaisesRegex(ValueError, "is null"):
            self.tool([bad]).get_funding_conditions("2026-10-10", 7)

    def test_duplicate_date_rejected(self):
        r = row("2026-10-08", 3.87, 3.88, 3.90)
        with self.assertRaisesRegex(ValueError, "Duplicate observation_date"):
            self.tool([r, r]).get_funding_conditions("2026-10-10", 7)

    def test_no_baseline_disclosed(self):
        result = self.tool([row("2026-10-08", 3.87, 3.88, 3.90)]).get_funding_conditions("2026-10-10", 30)
        self.assertIsNone(result["window"]["actual_baseline_date"])
        self.assertIsNone(result["spreads"]["sofr_iorb_spread_bps"]["change_bps"])
        self.assertEqual(result["spreads"]["sofr_iorb_spread_bps"]["status"], "insufficient_baseline")

    def test_incomplete_core_is_not_a_complete_observation(self):
        with self.assertRaisesRegex(ValueError, "No observations with complete"):
            self.tool([row("2026-10-10", None, None, 3.9)]).get_funding_conditions("2026-10-10")

    def test_invalid_bounds(self):
        t = self.tool([row("2026-10-08", 3.87, 3.88, 3.90)])
        for bad in (0, -1, 3651, True, 3.14):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                t.get_funding_conditions("2026-10-10", bad)
        with self.assertRaises(ValueError):
            t.get_funding_conditions("2026-99-10")

    def test_frozen_briefing_rates(self):
        frozen_path = ROOT / "fixtures" / "briefing_2026_10_10.json"
        if not frozen_path.exists():
            self.skipTest("Existing frozen briefing fixture not present; test it after overlaying onto project root")
        snapshot = json.loads(frozen_path.read_text(encoding="utf-8"))
        rates = snapshot["rates"]
        result = self.tool([rates]).get_funding_conditions("2026-10-10", 30)
        self.assertEqual(result["spreads"]["sofr_iorb_spread_bps"]["end_bps"], -3.0)
        self.assertEqual(result["spreads"]["sofr_effr_spread_bps"]["end_bps"], -1.0)
        self.assertEqual(result["spreads"]["effr_iorb_spread_bps"]["end_bps"], -2.0)
        self.assertEqual(result["spreads"]["dtb3_effr_spread_bps"]["end_bps"], 17.0)


if __name__ == "__main__":
    unittest.main()
