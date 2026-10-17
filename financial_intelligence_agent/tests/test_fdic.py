import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from financial_intelligence_agent.live.fdic import bank_financial,FIELDS

class FDIC(unittest.TestCase):
    def test_bank_financial_contract(self):
        def response(url,**kwargs):
            self.assertIn("api.fdic.gov",url)
            self.assertIn("CERT%3A14",url)
            return {"meta":{"total":2},"data":[
                {"data":{"CERT":14,"REPDTE":"2026-03-31","ASSET":120000}},
                {"data":{"CERT":14,"REPDTE":"2026-06-30","ASSET":124000}}]}
        r=bank_financial(14,"ASSET","2026-01-01","2026-10-01",fetch=response)
        self.assertEqual(len(r.series.points),2)
        self.assertEqual(r.series.points[-1].value,124000)
        self.assertIsNone(r.series.points[0].available_at)
    def test_identifier_validation_and_unknown_bank(self):
        with self.assertRaises(ValueError):
            bank_financial(0,"ASSET","2026-01-01","2026-10-01")
        with self.assertRaises(ValueError):
            bank_financial(1,"ASSET;DROP TABLE","2026-01-01","2026-10-01")
        with self.assertRaisesRegex(ValueError,"No FDIC observations"):
            bank_financial(14,"ASSET","2026-01-01","2026-10-01",
                fetch=lambda url,**kw:{"data":[]})
    def test_catalog_expanded(self):
        self.assertGreaterEqual(len(FIELDS),10)

if __name__=="__main__":unittest.main()
