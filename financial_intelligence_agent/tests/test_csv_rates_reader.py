"""Local fixture/CSV tests; no Databricks Connect or third-party dependencies."""

import csv
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from financial_intelligence_agent.data.csv_rates import CsvRatesReader
from financial_intelligence_agent.tools.funding_conditions import FundingConditionsTool

SAMPLE = ROOT / "fixtures" / "rates_daily_2026_06_01_to_2026_10_10.csv"


class CsvRatesReaderTests(unittest.TestCase):
    def test_real_export_coverage_and_missing_values(self):
        reader = CsvRatesReader(SAMPLE)
        self.assertEqual(reader.row_count, 132)
        self.assertTrue(reader.snapshot_id.startswith("manual-csv-sha256:"))
        self.assertEqual(len(reader.sha256), 64)
        self.assertEqual(len(reader.read_rates_through(date(2026, 10, 8))), 130)
        last = reader.read_rates_through(date(2026, 10, 10))[-1]
        self.assertEqual(last["observation_date"], "2026-10-10")
        self.assertIsNone(last["sofr"])
        self.assertEqual(last["iorb"], "3.9")

    def test_real_export_30_day_window(self):
        reader = CsvRatesReader(SAMPLE)
        result = FundingConditionsTool(reader, snapshot_id=reader.snapshot_id).get_funding_conditions("2026-10-10", 30)
        window = result["window"]
        self.assertEqual(window["target_start_date"], "2026-09-08")
        self.assertEqual(window["actual_baseline_date"], "2026-09-08")
        self.assertEqual(window["actual_end_date"], "2026-10-08")
        self.assertEqual(window["end_date_gap_days"], 2)
        self.assertEqual(window["complete_core_observations_in_window"], 23)
        expected = {
            "sofr_iorb_spread_bps": (-1, -3, -2),
            "sofr_effr_spread_bps": (1, -1, -2),
            "effr_iorb_spread_bps": (-2, -2, 0),
            "dtb3_effr_spread_bps": (17, 17, 0),
        }
        for name, (baseline, end, change) in expected.items():
            with self.subTest(spread=name):
                entry = result["spreads"][name]
                self.assertEqual(entry["status"], "ok")
                self.assertEqual(entry["baseline_bps"], baseline)
                self.assertEqual(entry["end_bps"], end)
                self.assertEqual(entry["change_bps"], change)
                self.assertEqual(entry["observations"], 23)
                self.assertIn(entry["evidence_id"], [e["id"] for e in result["evidence"]])
        self.assertEqual(result["spreads"]["dtb3_effr_spread_bps"]["max_bps"], 36)

    def test_hash_changes_on_export_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "rates.csv"
            shutil.copy2(SAMPLE, dest)
            original = CsvRatesReader(dest).snapshot_id
            with dest.open("a", encoding="utf-8") as out:
                out.write("\n")
            self.assertNotEqual(CsvRatesReader(dest).snapshot_id, original)

    def test_blank_and_null_equivalent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rates.csv"
            path.write_text("observation_date,sofr,effr,iorb,dtb3\n2026-10-08,3.87,3.88,3.90,\n2026-10-09,NULL,null,3.90,null\n", encoding="utf-8")
            reader = CsvRatesReader(path)
            last = reader.read_rates_through(date(2026, 10, 9))[-1]
            self.assertIsNone(last["sofr"])
            self.assertIsNone(last["effr"])
            self.assertIsNone(last["dtb3"])
            result = FundingConditionsTool(reader, snapshot_id=reader.snapshot_id).get_funding_conditions("2026-10-09", 1)
            self.assertEqual(result["spreads"]["dtb3_effr_spread_bps"]["status"], "unavailable_at_end_date")

    def test_duplicate_dates_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rates.csv"
            path.write_text("observation_date,sofr,effr,iorb,dtb3\n2026-10-08,3.87,3.88,3.90,4.05\n2026-10-08,3.87,3.88,3.90,4.05\n")
            with self.assertRaisesRegex(ValueError, "duplicate observation_date"):
                CsvRatesReader(path)

    def test_invalid_numeric_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rates.csv"
            path.write_text("observation_date,sofr,effr,iorb,dtb3\n2026-10-08,NaN,3.88,3.90,4.05\n")
            with self.assertRaisesRegex(ValueError, "Non-finite"):
                CsvRatesReader(path)

    def test_required_columns_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rates.csv"
            path.write_text("observation_date,sofr,effr\n2026-10-08,3.87,3.88\n")
            with self.assertRaisesRegex(ValueError, "missing required columns"):
                CsvRatesReader(path)

    def test_corrupt_derived_spread_fails_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rates.csv"
            path.write_text("observation_date,sofr,effr,iorb,dtb3,sofr_iorb_spread_bps\n2026-10-08,3.87,3.88,3.90,4.05,-30\n")
            reader = CsvRatesReader(path)
            with self.assertRaisesRegex(ValueError, "inconsistent sofr_iorb_spread_bps"):
                FundingConditionsTool(reader, snapshot_id=reader.snapshot_id).get_funding_conditions("2026-10-10", 1)


if __name__ == "__main__":
    unittest.main()
