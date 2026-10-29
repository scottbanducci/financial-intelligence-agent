"""Release-first FRED crawl; NY Fed dataset and primary dealer metadata."""
import unittest,sys,tempfile
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from financial_intelligence_agent.live.catalog_index import (
    get_connection,seed,search,status,crawl_fred,warm_ny,bootstrap,lookup,release_list)
from financial_intelligence_agent.live.providers import Descriptor

class CatalogIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=get_connection(Path(self.temp.name)/"index.sqlite")
        seed(self.db)
    def tearDown(self):self.db.close();self.temp.cleanup()
    def test_provider_split_and_local_search(self):
        self.assertGreaterEqual(len(search(self.db,"repo",provider="NYFED_DATASET")),3)
        self.assertGreaterEqual(len(search(self.db,"bank",provider="FRED")),1)
        self.assertEqual(len(search(self.db,"",provider="NYFED_PD")),0)
        self.assertFalse(self.db.execute("SELECT 1 FROM entries WHERE provider='NY_ENDPOINT'").fetchone())
    def fake(self):
        calls=[]
        def fetch(url,**kw):
            calls.append(url)
            from urllib.parse import urlsplit,parse_qs
            parsed=urlsplit(url);query=parse_qs(parsed.query)
            if parsed.path.endswith("/releases"):
                return {"count":2,"releases":[{"id":22,"name":"H.8 Bank Credit"},
                                              {"id":20,"name":"H.4.1 Balance Sheet"}]}
            if "release/series" in parsed.path:
                rid=int(query["release_id"][0]);offset=int(query.get("offset",["0"])[0])
                if rid==20:return {"count":1,"seriess":[
                    {"id":"WALCL","title":"Fed assets","units":"Millions of Dollars","frequency_short":"W"}]}
                if offset==0:return {"count":2,"seriess":[
                    {"id":"TOTBKCR","title":"Bank credit","units":"Billions of Dollars","frequency_short":"W"}]}
                if offset==1:return {"count":2,"seriess":[
                    {"id":"WALCL","title":"Fed assets","units":"Millions of Dollars","frequency_short":"W"}]}
            raise AssertionError(url)
        return fetch,calls
    def test_releases_then_all_series_paginated_and_many_to_many(self):
        fetch,calls=self.fake()
        result=crawl_fred(self.db,"a"*32,fetch=fetch,max_requests=20,sleep=lambda x:None)
        self.assertEqual(result["state"]["fred_complete"],"true")
        self.assertEqual(result["fred_releases"]["done"],2)
        self.assertEqual(len(release_list(self.db)),2)
        self.assertEqual(len(search(self.db,"H.8",provider="FRED")),2)
        self.assertEqual(len(search(self.db,"assets",provider="FRED")),1)
        self.assertEqual(len(search(self.db,"",provider="FRED",release_id=22)),2)
        self.assertEqual(len(calls),4)
        self.assertFalse(any("/category/" in c for c in calls))
    def test_resumes_mid_release_with_saved_offset(self):
        fetch,calls=self.fake()
        crawl_fred(self.db,"a"*32,fetch=fetch,max_requests=3,sleep=lambda x:None)
        before=self.db.execute("SELECT next_offset FROM fred_releases WHERE release_id=22").fetchone()[0]
        self.assertEqual(before,1)
        second=crawl_fred(self.db,"a"*32,fetch=fetch,max_requests=10,sleep=lambda x:None)
        self.assertEqual(second["state"]["fred_complete"],"true")
        self.assertEqual(len(release_list(self.db)),2)
    def test_failure_does_not_mark_release_complete(self):
        result=crawl_fred(self.db,"a"*32,
             fetch=lambda *a,**kw:(_ for _ in ()).throw(IOError("secret")),
             max_requests=5,sleep=lambda x:None)
        self.assertNotIn("fred_complete",result["state"])
        self.assertNotIn("secret",result["state"]["fred_error"])
    def test_ny_dealer_series_index_not_endpoint(self):
        def dealers():
            return [Descriptor("NYFED_PD:PDPOSMBS-TOT","Total - mortgage backed","NYFED_PD",
                "USD millions","weekly","Dealer",
                "https://markets.newyorkfed.org/api/pd/get/PDPOSMBS-TOT.json")]
        report=warm_ny(self.db,dealers=dealers)
        self.assertEqual(report["counts"]["NYFED_PD"],1)
        self.assertEqual(lookup(self.db,"NYFED_PD:PDPOSMBS-TOT")["kind"],"series")
        self.assertEqual(report["counts"]["NYFED_DATASET"],7)
    def test_bootstrap_stale_complete_resets_release_progress(self):
        path=Path(self.temp.name)/"fresh.sqlite"
        db=get_connection(path);seed(db)
        db.execute("INSERT INTO fred_releases(release_id,name,status,next_offset) VALUES(20,'H4','done',2)")
        db.execute("INSERT INTO crawl_state VALUES('fred_complete','true')")
        db.execute("INSERT INTO crawl_state VALUES('fred_updated','2020-01-01T00:00:00+00:00')")
        db.commit();db.close()
        bootstrap(path,enable_network=False)
        check=get_connection(path)
        self.assertEqual(release_list(check)[0]["status"],"pending")
        self.assertNotIn("fred_complete",status(check)["state"])
        check.close()
if __name__=="__main__":unittest.main()
