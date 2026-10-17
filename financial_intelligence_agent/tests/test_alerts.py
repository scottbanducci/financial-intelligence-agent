import sys,unittest
from pathlib import Path
from datetime import date
from decimal import Decimal
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from financial_intelligence_agent.engine.operators import Series,Point
from financial_intelligence_agent.engine.alerts import Rule,evaluate
class Alerts(unittest.TestCase):
    def setUp(self):
        self.s=Series("SOFR","NYFED","percent","daily",
            (Point("2026-10-01",3.70),Point("2026-10-02",3.90),
             Point("2026-10-03",4.10)))
    def test_cross_and_freshness(self):
        r=evaluate(Rule("Rate shock","SOFR","crosses_above",Decimal("4.0")),self.s,
                   expected_max_staleness_days=30,as_of=None)
        self.assertTrue(r["triggered"])
        self.assertEqual(r["comparison_value"],"3.9")
        self.assertTrue(r["stale"])
    def test_change_vs_threshold(self):
        r=evaluate(Rule("Spread","SOFR","change_above",Decimal("0.3"),horizon=2),
                   self.s,expected_max_staleness_days=365)
        self.assertTrue(r["triggered"])
    def test_missing_history(self):
        r=evaluate(Rule("Backcast","SOFR","crosses_above",Decimal("4"),horizon=8),
                   self.s)
        self.assertIsNone(r["triggered"])
    def test_fail_closed_bad_key(self):
        with self.assertRaisesRegex(ValueError,"Series key"):
            evaluate(Rule("Bad","IORB","above",Decimal("4")),self.s)
        with self.assertRaisesRegex(ValueError,"Unknown"):
            Rule("Bad","SOFR","run_shell",Decimal("4"))
if __name__=="__main__":unittest.main()
