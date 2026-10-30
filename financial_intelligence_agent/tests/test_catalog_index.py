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
    def test_seed_cannot_overwrite_publisher_metadata(self):
        self.db.execute("UPDATE entries SET label='Published newer SOFR metadata' WHERE id='FRED:SOFR'")
        self.db.commit()
        seed(self.db)
        self.assertEqual(lookup(self.db,"FRED:SOFR")["label"],"Published newer SOFR metadata")
    def test_discontinued_fred_toggle_filters_before_result_limit(self):
        from financial_intelligence_agent.live.catalog_index import put_entry
        # Deliberately sort discontinued matches ahead of active matches;
        # filtering only after LIMIT would incorrectly return no results.
        for code,label in (
            ("OLD1","AAA Market liquidity (DISCONTINUED)"),
            ("OLD2","AAB Market liquidity (Discontinued)"),
            ("LIVE1","ZZZ Market liquidity"),
        ):
            put_entry(self.db,Descriptor("FRED:"+code,label,"FRED","percent","daily",
                          "Test release","https://fred.stlouisfed.org/series/"+code,
                          "FRED_realtime_vintage"))
        self.db.execute("INSERT INTO fred_releases(release_id,name) VALUES (900,'Liquidity test')")
        for code in ("OLD1","OLD2","LIVE1"):
            self.db.execute("INSERT INTO fred_release_members(release_id,series_id) VALUES (?,?)",
                            (900,"FRED:"+code))
        self.db.commit()
        only_active=search(self.db,"market liquidity",provider="FRED",
                           release_id=900,limit=1,include_discontinued=False)
        self.assertEqual([row["id"] for row in only_active],["FRED:LIVE1"])
        included=search(self.db,"market liquidity",provider="FRED",
                        release_id=900,include_discontinued=True)
        self.assertEqual({row["id"] for row in included},
                         {"FRED:OLD1","FRED:OLD2","FRED:LIVE1"})
        self.assertEqual(len(search(self.db,"market liquidity",provider="FRED",
                                    release_id=900,include_discontinued=False)),1)
        # Non-FRED publishers are not accidentally subject to this filter.
        self.assertTrue(search(self.db,"repo",provider="NYFED_DATASET",
                               include_discontinued=False))

    def test_series_search_matches_displayed_frequency_units_and_id(self):
        from financial_intelligence_agent.live.catalog_index import put_entry
        put_entry(self.db,Descriptor(
            "FRED:WRESBAL",
            "Reserve Balances at Federal Reserve Banks, Total",
            "FRED","Millions of USD","weekly","Federal Reserve reporting",
            "https://fred.stlouisfed.org/series/WRESBAL",
            "FRED_realtime_vintage"))
        put_entry(self.db,Descriptor(
            "FRED:MRESBAL",
            "Reserve Balances at Federal Reserve Banks, Total",
            "FRED","Billions of USD","monthly","Federal Reserve reporting",
            "https://fred.stlouisfed.org/series/MRESBAL",
            "FRED_realtime_vintage"))
        self.db.execute(
            "INSERT INTO fred_releases(release_id,name) VALUES (401,'H.4.1 Federal Reserve Balance Sheet')")
        for code in ("WRESBAL","MRESBAL"):
            self.db.execute(
                "INSERT INTO fred_release_members(release_id,series_id) VALUES (401,?)",
                ("FRED:"+code,))
        self.db.commit()
        weekly=search(self.db,"reserve total weekly",provider="FRED",
                      release_id=401,include_discontinued=False)
        self.assertEqual([row["id"] for row in weekly],["FRED:WRESBAL"])
        # Tokens may appear in any order and match across label, frequency,
        # units, ID and release rather than only the original series title.
        self.assertEqual([x["id"] for x in search(
            self.db,"weekly total reserve",provider="FRED",release_id=401)],
            ["FRED:WRESBAL"])
        self.assertEqual([x["id"] for x in search(
            self.db,"h.4.1 reserve millions weekly",provider="FRED")],
            ["FRED:WRESBAL"])
        self.assertEqual([x["id"] for x in search(
            self.db,"wresbal millions",provider="FRED")],
            ["FRED:WRESBAL"])
        self.assertEqual([x["id"] for x in search(
            self.db,"total monthly billions",provider="FRED",release_id=401)],
            ["FRED:MRESBAL"])

    def test_unlimited_series_search_includes_beyond_old_150_and_250_caps(self):
        from financial_intelligence_agent.live.catalog_index import put_entry
        self.db.execute(
            "INSERT INTO fred_releases(release_id,name) VALUES (901,'Large release')")
        for i in range(311):
            sid=f"FRED:EXAMPL{i:04d}"
            put_entry(self.db,Descriptor(
                sid,f"Example indexed weekly series {i:04d}",
                "FRED","Millions of USD","weekly","Test release",
                "https://fred.stlouisfed.org/series/"+sid[5:],
                "FRED_realtime_vintage"))
            self.db.execute(
                "INSERT INTO fred_release_members(release_id,series_id) VALUES (901,?)",
                (sid,))
        self.db.commit()
        all_matches=search(self.db,"example weekly",provider="FRED",
                           release_id=901)
        self.assertEqual(len(all_matches),311)
        self.assertEqual(len({row["id"] for row in all_matches}),311)
        self.assertIn("FRED:EXAMPL0310",{row["id"] for row in all_matches})
        self.assertEqual(len(search(self.db,"",provider="FRED",release_id=901)),311)
        # Explicit limits remain available to existing NY Fed callers, but
        # the FRED dropdown no longer supplies one.
        self.assertEqual(len(search(self.db,"example",provider="FRED",
                                    release_id=901,limit=2)),2)
        self.assertEqual(len(search(self.db,"example",provider="FRED",
                                    release_id=901,limit=300)),300)

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
    def test_migration_clears_old_category_error_without_erasing_series(self):
        path=Path(self.temp.name)/"migration.sqlite"
        db=get_connection(path)
        seed(db)
        db.execute("INSERT INTO crawl_state VALUES('fred_error','A category request failed; crawl can resume after restart')")
        db.execute("INSERT INTO crawl_state VALUES('fred_complete','true')")
        db.commit();db.close()
        bootstrap(path,enable_network=False)
        check=get_connection(path)
        self.assertNotIn("fred_error",status(check)["state"])
        self.assertNotIn("fred_complete",status(check)["state"])
        self.assertIsNotNone(lookup(check,"FRED:SOFR"))
        check.close()
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
