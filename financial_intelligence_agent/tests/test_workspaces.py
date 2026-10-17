import sys,unittest,json
from pathlib import Path
from datetime import date
from decimal import Decimal
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from financial_intelligence_agent.engine.operators import Series,Point
from financial_intelligence_agent.live.providers import Descriptor
from financial_intelligence_agent.live.workspaces import export_workspace,import_workspace
class Workspaces(unittest.TestCase):
    def setUp(self):
        self.s=Series("M2","FRED","Billions of USD","monthly",
                      (Point(date(2026,1,1),Decimal(22000)),Point(date(2026,2,1),Decimal(22100))))
        self.d=Descriptor("FRED:M2","Money stock","FRED","Billions of USD",
                          "monthly","Macro","https://fred.stlouisfed.org/series/M2")
        self.ws={"M2":{"series":self.s,"descriptor":self.d,"retrieved":"2026-10-16","warning":""}}
    def test_round_trip_without_secrets(self):
        raw=export_workspace(self.ws)
        self.assertNotIn(b"api_key",raw)
        copy=import_workspace(raw)
        self.assertEqual(copy["M2"]["series"].fingerprint(),self.s.fingerprint())
    def test_tampering_rejected(self):
        raw=export_workspace(self.ws)
        data=json.loads(raw)
        data["records"][0]["points"][0]["value"]="999999"
        with self.assertRaisesRegex(ValueError,"integrity"):
            import_workspace(json.dumps(data).encode())
    def test_limit_and_schema(self):
        with self.assertRaises(ValueError):
            import_workspace(b"garbage")
        with self.assertRaisesRegex(ValueError,"size"):
            import_workspace(b"a"*9_000_001)
if __name__=="__main__":unittest.main()
