"""Contract tests use deterministic injected HTTP responses; never network."""
import sys
import unittest
from decimal import Decimal
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from financial_intelligence_agent.live.providers import (
    fred,ny_rate,dealer,dealer_catalog,fetch_series,catalog,validate_dates)

API_KEY="a"*32
class ProviderTests(unittest.TestCase):
    def test_fred_values_are_date_bounded_and_missing_not_zero(self):
        def mock(url,**kwargs):
            self.assertIn("api.stlouisfed.org",url)
            self.assertNotIn("api_key=KEY",url)
            return {"count":3,"observations":[
                {"date":"2026-10-01","value":"3.9","realtime_start":"2026-10-02"},
                {"date":"2026-10-02","value":".","realtime_start":"2026-10-03"},
                {"date":"2026-10-03","value":"4.1","realtime_start":"2026-10-04"}]}
        x=fred("SOFR","2026-10-01","2026-10-03",api_key=API_KEY,fetch=mock)
        self.assertEqual(len(x.series.points),2)
        self.assertEqual(x.series.points[0].available_at.isoformat(),"2026-10-02")
        self.assertEqual(x.descriptor.unit,"percent")
    def test_provider_rejects_pagination_truncation(self):
        with self.assertRaisesRegex(ValueError,"one API page"):
            fred("SOFR","2026-01-01","2026-10-01",api_key=API_KEY,
                 fetch=lambda *a,**k:{"count":12000,"observations":[]})
    def test_key_never_required_for_public_nyfed_rates(self):
        def mock(url,**kwargs):
            self.assertIn("/rates/secured/sofr/search.json",url)
            return {"refRates":[
                {"effectiveDate":"2026-10-01","type":"SOFR","percentRate":3.87},
                {"effectiveDate":"2026-10-01","type":"EFFR","percentRate":3.9},
                {"effectiveDate":"2026-10-02","type":"SOFR","percentRate":3.85}]}
        r=fetch_series("NYFED:SOFR","2026-10-01","2026-10-02",fetch=mock)
        self.assertEqual(len(r.series.points),2)
        self.assertIsNone(r.series.points[0].available_at)
        self.assertEqual(r.series.points[1].value,Decimal('3.85'))
    def test_dealer_dynamic_catalog_and_mapping(self):
        def catalog_mock(*a,**kw):
            return {"pd":{"timeseries":[{"keyid":"PDPOSGST-TOT","description":"Treasury positions"}]}}
        found=dealer_catalog(fetch=catalog_mock)
        self.assertEqual(found[0].id,"NYFED_PD:PDPOSGST-TOT")
        data=lambda *a,**kw:{"pd":{"timeseries":[
            {"keyid":"PDPOSGST-TOT","asof":"2026-10-01","value":"10"},
            {"keyid":"PDPOSGST-TOT","asof":"2026-10-08","value":"12"}]}}
        out=dealer("PDPOSGST-TOT","2026-10-01","2026-10-08",fetch=data,
                   authorized_ids={"PDPOSGST-TOT"})
        self.assertEqual(out.series.points[-1].value,12)
    def test_fail_closed_on_unknown_dealer_layout(self):
        with self.assertRaisesRegex(ValueError,"structure changed"):
            dealer("PDPOSGST-TOT","2026-10-01","2026-10-08",
                   fetch=lambda *a,**kw:{"pd":{"unexpected":[]}})
    def test_bounds_and_catalog(self):
        self.assertGreaterEqual(len(catalog()),30)
        with self.assertRaises(ValueError):
            validate_dates("2026-10-02","2026-10-01")
        with self.assertRaises(ValueError):
            fetch_series("http://attacker","2026-10-01","2026-10-02")
        with self.assertRaises(ValueError):
            fred("../bad","2026-10-01","2026-10-02",api_key=API_KEY)

if __name__=="__main__":
    unittest.main()
