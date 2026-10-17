"""Credential-free agent regression tests."""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from financial_intelligence_agent.agent.dispatcher import Engine, TOOLS
from financial_intelligence_agent.data.csv_rates import CsvRatesReader
from financial_intelligence_agent.tools.snapshot_insights import MacroSnapshot

class AgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = Engine(
            MacroSnapshot(ROOT / "fixtures/briefing_2026_10_10.json"),
            CsvRatesReader(ROOT / "fixtures/rates_daily_2026_06_01_to_2026_10_10.csv"),
        )

    def test_route_four_allowlisted_tools(self):
        cases = {
            "Show SOFR versus IORB": "get_funding_conditions",
            "Change in reserves and TGA?": "get_fed_liquidity",
            "Bank loans and deposits?": "get_bank_credit",
            "Are dates and sources fresh?": "get_data_freshness",
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                answer = self.engine.answer(question)
                self.assertEqual(answer["selected_tool"], expected)
                self.assertTrue(answer["result"]["evidence"])
                self.assertFalse(answer["trace"][-1]["model_generated_numbers"])

    def test_h41_units(self):
        result = self.engine.invoke("get_fed_liquidity")
        self.assertEqual(result["levels"]["total_assets"], 6747.56)
        self.assertEqual(result["weekly_changes"]["reserve_balances_wow"], 81.57)
        self.assertIn("weekly-average", " ".join(result["warnings"]))
        self.assertNotIn("liquidity_impulse", result)

    def test_bank_yoy_and_breadth_warning(self):
        result = self.engine.invoke("get_bank_credit")
        self.assertEqual(result["levels"]["bank_credit"], 19883.19)
        self.assertGreater(result["bank_credit_yoy_pct"], 0)
        self.assertIn("individual institutions", " ".join(result["warnings"]))

    def test_funding_spread_from_existing_calculator(self):
        answer = self.engine.answer("SOFR IORB funding")
        self.assertEqual(answer["result"]["window"]["actual_end_date"], "2026-10-08")
        self.assertIn("-3.00 bp", answer["report"])

    def test_no_future_snapshot_claim(self):
        with self.assertRaisesRegex(ValueError, "Frozen sample ends"):
            self.engine.invoke("get_bank_credit", cutoff="2026-10-16")
        with self.assertRaisesRegex(ValueError, "No frozen observation"):
            self.engine.invoke("get_bank_credit", cutoff="2026-09-15")

    def test_allowlist_and_bounds(self):
        self.assertEqual(len(TOOLS), 4)
        with self.assertRaisesRegex(ValueError, "Unknown tool"):
            self.engine.invoke("run_sql")
        for bad in (True, 0, 366, 30.5):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.engine.invoke("get_funding_conditions", lookback_days=bad)

    def test_corrupt_numeric_rejected(self):
        old = self.engine.macro.data
        corrupt = copy.deepcopy(old)
        corrupt["fed_balance_sheet"]["reserve_balances"] = float("nan")
        self.engine.macro.data = corrupt
        try:
            with self.assertRaisesRegex(ValueError, "non-finite"):
                self.engine.invoke("get_fed_liquidity")
        finally:
            self.engine.macro.data = old

if __name__ == "__main__":
    unittest.main()
