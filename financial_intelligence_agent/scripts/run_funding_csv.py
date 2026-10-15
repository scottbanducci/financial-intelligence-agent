"""Run the analytical tool against a manual Databricks rates CSV export.

From bundle root: python scripts/run_funding_csv.py --as-of 2026-10-10 --lookback-days 30 --summary
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from financial_intelligence_agent.data.csv_rates import CsvRatesReader
from financial_intelligence_agent.tools.funding_conditions import FundingConditionsTool


DEFAULT_CSV = ROOT / "fixtures" / "rates_daily_2026_06_01_to_2026_10_10.csv"


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze exported FRED funding data; no cloud credentials needed")
    parser.add_argument("--file", type=Path, default=DEFAULT_CSV, help="Manually exported rates_daily CSV")
    parser.add_argument("--as-of", default="2026-10-10", help="Requested as-of date (YYYY-MM-DD)")
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--summary", action="store_true", help="Print concise result instead of all evidence")
    args = parser.parse_args()

    reader = CsvRatesReader(args.file)
    tool = FundingConditionsTool(reader, snapshot_id=reader.snapshot_id)
    result = tool.get_funding_conditions(args.as_of, args.lookback_days)
    if args.summary:
        result = {
            "source": "manually exported Databricks CSV (not a live query)",
            "snapshot_id": result["snapshot_id"],
            "rows_in_export": reader.row_count,
            "window": result["window"],
            "spreads": result["spreads"],
            "warnings": result["warnings"],
        }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
