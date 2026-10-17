import sys,unittest
from pathlib import Path
from decimal import Decimal
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from financial_intelligence_agent.live.nyfed_explorer import discover,map_series,validate_endpoint,records_from_path
class ExplorerTests(unittest.TestCase):
    def test_restricted_endpoint_and_dynamic_record_map(self):
        data=discover("rp/results/search.json",start="2026-10-01",end="2026-10-03",
               fetch=lambda u,**k:{"repo":{"operations":[
                   {"operationDate":"2026-10-01","totalAccepted":"25","type":"Reverse Repo"},
                   {"operationDate":"2026-10-01","totalAccepted":"30","type":"Reverse Repo"},
                   {"operationDate":"2026-10-02","totalAccepted":"10","type":"Repo"}]}})
        self.assertEqual(data.candidate_paths,("repo.operations",))
        with self.assertRaisesRegex(ValueError,"Multiple records"):
            map_series(data,path="repo.operations",key="RRP",date_field="operationDate",
                       value_field="totalAccepted",unit="USD millions",frequency="daily")
        actual=map_series(data,path="repo.operations",key="RRP",date_field="operationDate",
            value_field="totalAccepted",unit="USD millions",frequency="daily",aggregation="sum",
            filter_field="type",filter_value="Reverse Repo")
        self.assertEqual(len(actual.points),1)
        self.assertEqual(actual.points[0].value,Decimal("55"))
    def test_host_and_endpoint_security(self):
        for path in ("http://attacker/abc.json","../../secrets.json","rp/results/../../foo.json",
                     "evil/foo.json","rp/get.json?api_key=secret","soma/query.xml"):
            with self.subTest(path=path),self.assertRaises(ValueError):
                validate_endpoint(path)
    def test_schema_rejection(self):
        with self.assertRaisesRegex(ValueError,"discovered"):
            records_from_path(discover("soma/summary.json",
                fetch=lambda *a,**k:{"soma":{"holdings":[{"date":"2026-01-01","value":3}]}}),"x.y")
    def test_browser_cannot_control_http_host(self):
        def fetch(url,**kwargs):
            self.assertTrue(url.startswith("https://markets.newyorkfed.org/api/"))
            self.assertEqual(kwargs["allowed_hostname"],"markets.newyorkfed.org")
            return {"x":{"series":[{"date":"2026-10-01","value":"1"}]}}
        d=discover("seclending/all/results/summary/latest.json",fetch=fetch)
        self.assertTrue(d.candidate_paths)
if __name__=="__main__":unittest.main()
