import unittest,sys,tempfile,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from financial_intelligence_agent.live.catalog_index import (
    get_connection,seed,search,status,crawl_fred,ny_spec_entries,warm_ny,bootstrap,lookup)
from financial_intelligence_agent.live.providers import Descriptor
class CatalogIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=get_connection(Path(self.temp.name)/"index.sqlite")
        seed(self.db)
    def tearDown(self):
        self.db.close();self.temp.cleanup()
    def test_no_remote_search_uses_local_index(self):
        res=search(self.db,"reserves")
        self.assertTrue(any(x["provider"]=="FRED" for x in res))
        self.assertTrue(all(x["kind"]=="series" for x in search(self.db,"SOFR")))
    def test_full_category_tree_with_pagination(self):
        calls=[]
        def fetch(url,**kwargs):
            calls.append(url)
            if "category/children" in url:
                return {"categories":[{"id":1}]} if "category_id=0" in url else {"categories":[]}
            if "category_id=0" in url:
                return {"count":1,"seriess":[{"id":"ONE","title":"First","units":"Percent","frequency_short":"D"}]}
            if "offset=0" in url:
                return {"count":2,"seriess":[{"id":"TWO","title":"Second","units":"Billions of Dollars","frequency_short":"W"},
                                            {"id":"THREE","title":"Third","units":"Millions of Dollars","frequency_short":"M"}]}
            raise AssertionError(url)
        result=crawl_fred(self.db,"a"*32,fetch=fetch,max_requests=10,sleep=lambda n:None)
        self.assertEqual(result["state"]["fred_complete"],"true")
        self.assertEqual(result["fred_categories"]["done"],2)
        self.assertIsNotNone(lookup(self.db,"FRED:THREE"))
        self.assertEqual(len(calls),4)
    def test_resumes_after_failure(self):
        calls=0
        def broken(url,**kw):
            raise IOError("secret in endpoint")
        result=crawl_fred(self.db,"a"*32,fetch=broken,max_requests=2,sleep=lambda n:None)
        self.assertEqual(result["fred_categories"]["pending"],1)
        self.assertNotIn("secret",result["state"]["fred_error"])
    def test_swagger_get_templates_and_dealers(self):
        spec={"paths":{
            "/api/rp/results/search.json":{"get":{"summary":"Repo operations","tags":["Repo"]}},
            "/api/soma/summary.json":{"get":{"summary":"Holdings","tags":["SOMA"]}},
            "/api/rates/secured/sofr/search.csv":{"get":{"summary":"No JSON"}},
        }}
        def dealers():
            return [Descriptor("NYFED_PD:PDPOSGST-TOT","Dealer Treasury","NYFED_PD",
                "USD millions","weekly","Dealer","https://markets.newyorkfed.org/api/pd/get/PDPOSGST-TOT.json")]
        result=warm_ny(self.db,dealers=dealers,spec_loader=lambda:spec)
        self.assertEqual(result["counts"]["NY_ENDPOINT"],4)  # 3 seeded fallbacks + 1 new SOMA path
        self.assertIsNotNone(lookup(self.db,"NYFED_PD:PDPOSGST-TOT"))
    def test_completed_fred_catalog_is_invalidated_when_stale(self):
        import datetime
        from financial_intelligence_agent.live.catalog_index import bootstrap
        path=Path(self.temp.name)/"refresh.sqlite"
        db=get_connection(path)
        seed(db)
        db.execute("UPDATE fred_categories SET status='done'")
        db.execute("INSERT INTO crawl_state(name,value) VALUES('fred_complete','true')")
        db.execute("INSERT INTO crawl_state(name,value) VALUES('fred_updated','2020-01-01T00:00:00+00:00')")
        db.commit();db.close()
        bootstrap(path,enable_network=False)
        check=get_connection(path)
        current=status(check)
        self.assertEqual(current["fred_categories"].get("pending"),1)
        self.assertNotIn("fred_complete",current["state"])
        check.close()
    def test_bootstrap_is_nonblocking_and_seed_only(self):
        info=bootstrap(Path(self.temp.name)/"second.sqlite",enable_network=False)
        self.assertFalse(info["worker_running"])
if __name__=="__main__":unittest.main()
