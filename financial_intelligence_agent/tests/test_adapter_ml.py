import sys
import unittest
from pathlib import Path
from datetime import date,timedelta
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from financial_intelligence_agent.engine.operators import Point,Series
from financial_intelligence_agent.engine.adapters import FredAdapter,NYFedAdapter,FFIECAdapter,CsvSeriesAdapter,fetch_json
from financial_intelligence_agent.engine.ml import ALGORITHMS,make_supervised,walk_forward

class TestAdaptersAndML(unittest.TestCase):
    def test_fred_vintage(self):
        s=FredAdapter("SOFR","percent","daily").parse({"observations":[
            {"date":"2026-01-01","value":"3.50","realtime_start":"2026-01-02"},
            {"date":"2026-01-01","value":"3.55","realtime_start":"2026-01-10"}]})
        self.assertEqual(s.select("2026-01-05").points[0].value,3.5)
        self.assertEqual(s.select("2026-01-12").points[0].value,3.55)
        self.assertEqual(s.convention,"FRED_realtime_vintage")
    def test_provider_independent(self):
        ny=NYFedAdapter(key="repo",unit="percent",frequency="daily",records_path=("data","rates"),
                        date_field="date",value_field="rate")
        parsed=ny.parse({"data":{"rates":[{"date":"2026-01-01","rate":"3.85"}]}})
        self.assertEqual(parsed.points[0].value,3.85)
        ff=FFIECAdapter(field="RCON0010",institution_field="IDRSSD",institution_id="42",
                        date_field="report_date",unit="USD_thousands")
        bank=ff.parse("IDRSSD\treport_date\tRCON0010\n42\t2026-03-31\t100\n43\t2026-03-31\t999\n")
        self.assertEqual(len(bank.points),1)
        self.assertEqual(bank.points[0].value,100)
        csv=CsvSeriesAdapter(key="A",source="EXTERNAL",unit="USD",frequency="monthly",
                            date_field="date",value_field="value")
        self.assertEqual(csv.parse("date,value\n2026-01-01,3\n").points[0].value,3)
    def test_ssrf_guard(self):
        with self.assertRaisesRegex(ValueError,"Blocked"):
            fetch_json("http://localhost/secret",allowed_hostname="api.stlouisfed.org")
    def test_registry_and_walkforward(self):
        self.assertGreaterEqual(len(ALGORITHMS),23)
        dates=[date(2026,1,1)+timedelta(days=i) for i in range(65)]
        x=Series("feat","FRED","percent","daily",tuple(Point(d,i+1) for i,d in enumerate(dates)))
        target=Series("tgt","FRED","percent","daily",tuple(Point(d,2*i+1) for i,d in enumerate(dates)))
        data=make_supervised({"feat":x},target,horizon=2)
        report=walk_forward(data,algorithm="historical_mean",initial_train=25,folds=3,test_size=5)
        self.assertEqual(len(report["splits"]),3)
        self.assertEqual(report["splits"][0]["embargo_rows"],2)
        self.assertEqual(report["model_metrics"],report["baseline_metrics"])
        self.assertEqual(report["dataset_metadata"]["as_of_claim"],"not_point_in_time_verified")
    def test_leakage_boundaries(self):
        dates=[date(2026,1,1)+timedelta(days=i) for i in range(60)]
        a=Series("a","S","unit","daily",tuple(Point(d,i) for i,d in enumerate(dates)))
        ds=make_supervised({"a":a},a,horizon=5)
        with self.assertRaisesRegex(ValueError,"Embargo"):
            walk_forward(ds,algorithm="last_value",embargo=3)
        with self.assertRaisesRegex(ValueError,"Insufficient"):
            walk_forward(ds,algorithm="last_value",initial_train=50,folds=4,test_size=10)

if __name__=="__main__":
    unittest.main()
