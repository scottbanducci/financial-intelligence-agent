import sys,unittest
from pathlib import Path
from datetime import date
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from financial_intelligence_agent.live.panels import (
    fetch_bank_panel,Panel,BankDatum,contraction_breadth)
from decimal import Decimal
class PanelTests(unittest.TestCase):
    def test_pagination_and_contraction_breadth(self):
        def data(url,**kwargs):
            if "offset=0" in url:
                return {"meta":{"total":4},"data":[
                    {"data":{"CERT":1,"REPDTE":"2025-03-31","LNLSGR":100,"ASSET":200}},
                    {"data":{"CERT":2,"REPDTE":"2025-03-31","LNLSGR":100,"ASSET":500}},
                    {"data":{"CERT":1,"REPDTE":"2026-03-31","LNLSGR":80,"ASSET":220}},
                    {"data":{"CERT":2,"REPDTE":"2026-03-31","LNLSGR":110,"ASSET":550}},
                ]}
            self.fail("Unexpected page")
        panel=fetch_bank_panel("LNLSGR","2025-01-01","2026-10-16",fetch=data)
        result=contraction_breadth(panel)
        row=result["table"][0]
        self.assertEqual(row["banks_with_yoy_pair"],2)
        self.assertEqual(row["banks_declining"],1)
        self.assertEqual(row["share_declining_pct"],50)
        self.assertAlmostEqual(row["asset_weighted_share_declining_pct"],220/(220+550)*100)
    def test_duplicate_bank_rejected(self):
        r=BankDatum(1,date(2026,3,31),Decimal(1),Decimal(100))
        with self.assertRaises(ValueError):Panel("LNLSGR",(r,r))
    def test_no_pair_fail_closed(self):
        with self.assertRaisesRegex(ValueError,"Insufficient matching"):
            contraction_breadth(Panel("LNLSGR",(BankDatum(1,date(2026,3,31),Decimal(1),None),)))
    def test_source_total_must_be_complete(self):
        with self.assertRaisesRegex(ValueError,"Unexpected empty"):
            fetch_bank_panel("LNLSGR","2025-01-01","2026-10-16",
                fetch=lambda url,**kw:{"meta":{"total":2500},"data":[]})

if __name__=="__main__":unittest.main()
