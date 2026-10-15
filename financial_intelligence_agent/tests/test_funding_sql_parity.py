"""Independent SQLite reference over the exported CSV (NOT a live Databricks check).

The SQLite reference re-derives spreads solely from raw rates, without calling
Python alignment or spread-calculation helpers.
"""

import csv
import sqlite3
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from financial_intelligence_agent.data.csv_rates import CsvRatesReader
from financial_intelligence_agent.tools.funding_conditions import FundingConditionsTool

SAMPLE = ROOT / "fixtures" / "rates_daily_2026_06_01_to_2026_10_10.csv"
SPREADS = {
    "sofr_iorb_spread_bps": ("sofr", "iorb"),
    "sofr_effr_spread_bps": ("sofr", "effr"),
    "effr_iorb_spread_bps": ("effr", "iorb"),
    "dtb3_effr_spread_bps": ("dtb3", "effr"),
}


def reference_connection():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE rates (observation_date TEXT PRIMARY KEY, sofr REAL, effr REAL, iorb REAL, dtb3 REAL)")
    with SAMPLE.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            values = [row[k] for k in ("sofr", "effr", "iorb", "dtb3")]
            converted = [None if s.strip().lower() in ("null", "") else float(s) for s in values]
            db.execute("INSERT INTO rates VALUES (?,?,?,?,?)", (row["observation_date"], *converted))
    return db


def reference_result(db, as_of: str, lookback_days: int):
    # Every date operation and every numeric calculation is done in SQLite.
    core = "sofr IS NOT NULL AND effr IS NOT NULL AND iorb IS NOT NULL"
    end = db.execute(
        f"SELECT MAX(observation_date) FROM rates WHERE observation_date <= ? AND {core}", (as_of,)
    ).fetchone()[0]
    target = db.execute("SELECT date(?, ?)", (end, f"-{lookback_days} days")).fetchone()[0]
    baseline = db.execute(
        f"SELECT MAX(observation_date) FROM rates WHERE observation_date <= ? AND {core}", (target,)
    ).fetchone()[0]
    ncore = db.execute(
        f"SELECT COUNT(*) FROM rates WHERE observation_date BETWEEN ? AND ? AND {core}", (target, end)
    ).fetchone()[0]

    spreads = {}
    for name, (left, right) in SPREADS.items():
        # Allowlisted column names from test constants, not model/user input.
        expression = f"ROUND(({left} - {right}) * 100, 6)"
        clause = f"{core} AND {left} IS NOT NULL AND {right} IS NOT NULL"
        rows = db.execute(
            f"SELECT observation_date, {expression} AS spread FROM rates "
            f"WHERE observation_date BETWEEN ? AND ? AND {clause}", (target, end)
        ).fetchall()
        values = dict(rows)
        minimum = sorted(rows, key=lambda row: (row[1], row[0]))[0]
        maximum = sorted(rows, key=lambda row: (-row[1], row[0]))[0]
        # Baseline can be BEFORE the 30-day window when the target is a
        # non-publication day. Query it separately instead of joining to
        # in-window rows (which would silently omit a legitimate baseline).
        baseline_value = db.execute(
            f"SELECT {expression} FROM rates WHERE observation_date = ? AND {clause}",
            (baseline,),
        ).fetchone() if baseline is not None else None
        baseline_bp = baseline_value[0] if baseline_value is not None else None
        spreads[name] = {
            "baseline_bps": round(baseline_bp, 2) if baseline_bp is not None else None,
            "end_bps": round(values[end], 2),
            "change_bps": round(values[end] - baseline_bp, 2) if baseline_bp is not None else None,
            "min_bps": round(minimum[1], 2),
            "min_date": minimum[0],
            "max_bps": round(maximum[1], 2),
            "max_date": maximum[0],
            "observations": len(rows),
        }
    return {"target": target, "baseline": baseline, "end": end, "ncore": ncore, "spreads": spreads}


class SqlParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = reference_connection()
        reader = CsvRatesReader(SAMPLE)
        cls.tool = FundingConditionsTool(reader, snapshot_id=reader.snapshot_id)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_sql_parity_at_three_cutoffs(self):
        for cutoff in ("2026-10-10", "2026-10-07", "2026-07-05"):
            with self.subTest(cutoff=cutoff):
                expected = reference_result(self.db, cutoff, 30)
                actual = self.tool.get_funding_conditions(cutoff, 30)
                window = actual["window"]
                self.assertEqual(window["target_start_date"], expected["target"])
                self.assertEqual(window["actual_baseline_date"], expected["baseline"])
                self.assertEqual(window["actual_end_date"], expected["end"])
                self.assertEqual(window["complete_core_observations_in_window"], expected["ncore"])
                for name, values in expected["spreads"].items():
                    with self.subTest(spread=name):
                        for key, val in values.items():
                            self.assertEqual(actual["spreads"][name][key], val, (cutoff, name, key))


if __name__ == "__main__":
    unittest.main()
