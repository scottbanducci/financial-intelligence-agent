import sys,unittest
from pathlib import Path
from datetime import date
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from financial_intelligence_agent.live.databricks_gold import read_gold,descriptors,GOLD_FIELDS

class Cursor:
    def __init__(self):self.statement=None;self.params=None
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def execute(self,statement,params):self.statement,self.params=statement,params
    def fetchall(self):return [(date(2026,10,7),6747560),(date(2026,10,14),6750000)]
class Conn:
    def __init__(self):self.cur=Cursor();self.closed=False
    def cursor(self):return self.cur
    def close(self):self.closed=True
class GoldTests(unittest.TestCase):
    def test_readonly_and_parameterized(self):
        conn=Conn()
        result=read_gold("fed_balance_sheet_weekly","total_assets",
            "2026-10-01","2026-10-16",catalog="fed_monitor",connect=lambda:conn)
        self.assertEqual(len(result.series.points),2)
        self.assertEqual(result.series.unit,"Millions of USD")
        self.assertIn("BETWEEN ? AND ?",conn.cur.statement)
        self.assertEqual(conn.cur.params,("2026-10-01","2026-10-16"))
        self.assertTrue(conn.closed)
    def test_rejects_arbitrary_table_and_catalog(self):
        with self.assertRaises(ValueError):read_gold("rate;drop","sofr","2026-01-01","2026-10-01")
        with self.assertRaises(ValueError):descriptors("fed_monitor;drop")
        self.assertGreaterEqual(len(descriptors("fed_monitor")),16)
    def test_gold_exception_redacted(self):
        def fail():raise ValueError("TOKEN secret")
        with self.assertRaisesRegex(ValueError,"Databricks read failed") as err:
            read_gold("rates_daily","sofr","2026-10-01","2026-10-16",connect=fail)
        self.assertNotIn("TOKEN",str(err.exception))
if __name__=="__main__":unittest.main()
