"""Preserve safe operator limits while accepting long daily FRED history."""
import sys
import unittest
from datetime import date,timedelta
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))

from financial_intelligence_agent.engine.operators import Point,Series


class LongFredHistoryTests(unittest.TestCase):
    def test_fred_can_hold_more_than_ten_thousand_observations(self):
        rows=tuple(Point(date(1970,1,1)+timedelta(days=i),Decimal("1"))
                   for i in range(10001))
        loaded=Series("FRED:LONG","FRED","percent","daily",rows)
        self.assertEqual(len(loaded.points),10001)

    def test_other_source_limit_remains_ten_thousand(self):
        rows=tuple(Point(date(1970,1,1)+timedelta(days=i),Decimal("1"))
                   for i in range(10001))
        with self.assertRaisesRegex(ValueError,"1..10000"):
            Series("USER:LONG","UPLOAD","percent","daily",rows)


if __name__=="__main__":
    unittest.main()
