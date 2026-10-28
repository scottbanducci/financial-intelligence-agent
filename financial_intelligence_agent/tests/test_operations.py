import sys,unittest
from pathlib import Path
from decimal import Decimal
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from financial_intelligence_agent.live.nyfed_explorer import discover
from financial_intelligence_agent.live.operations import (
    aggregate_operations,metric_options,operation_types)
class OperationsTests(unittest.TestCase):
    def sample(self):
        return discover("rp/results/search.json",fetch=lambda *a,**k:{
            "repo":{"operations":[
              {"operationDate":"2026-10-21","operationType":"Reverse Repo",
               "totalAmtAccepted":"100","totalAmtSubmitted":"120","id":"1"},
              {"operationDate":"2026-10-21","operationType":"Reverse Repo",
               "totalAmtAccepted":"40","totalAmtSubmitted":"45","id":"2"},
              {"operationDate":"2026-10-21","operationType":"Repo",
               "totalAmtAccepted":"20","totalAmtSubmitted":"22","id":"3"},
              {"operationDate":"2026-10-22","operationType":"Repo",
               "totalAmtAccepted":"30","totalAmtSubmitted":"33","id":"4"}
            ]}})
    def test_daily_type_aggregate_and_all_source_rows(self):
        sample=self.sample()
        op=aggregate_operations(sample)
        self.assertEqual(len(op.detail),4)
        self.assertEqual(len(op.daily),3)
        self.assertEqual(len(op.series),2)
        self.assertEqual(op.series["Reverse Repo"].points[0].value,Decimal(140))
        self.assertEqual(op.series["Repo"].points[-1].value,Decimal(30))
        self.assertEqual(op.unit,"USD")
        self.assertEqual(op.daily[0]["operations"],1)
    def test_type_filter_client_side(self):
        op=aggregate_operations(self.sample(),operation_type="reverse repo")
        self.assertEqual(len(op.detail),2)
        self.assertEqual(set(op.series),{"Reverse Repo"})
        self.assertEqual(op.series["Reverse Repo"].points[0].value,Decimal(140))
    def test_missing_amount_never_summed_as_zero(self):
        data=discover("rp/results/search.json",fetch=lambda *a,**k:{
            "repo":{"operations":[{"operationDate":"2026-10-21","operationType":"Repo"}]}})
        with self.assertRaisesRegex(ValueError,"Missing"):
            aggregate_operations(data)
    def test_additive_only(self):
        with self.assertRaisesRegex(ValueError,"additive"):
            aggregate_operations(self.sample(),metric="rate")
        self.assertEqual(operation_types(self.sample(),"repo.operations"),("Repo","Reverse Repo"))
        self.assertIn("totalAmtAccepted",metric_options(self.sample(),"repo.operations"))
if __name__=="__main__":unittest.main()
