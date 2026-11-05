import json
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import date,datetime,timezone
from decimal import Decimal

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from financial_intelligence_agent.engine.operators import Point,Series
from financial_intelligence_agent.agent.operator_planner import AnalyticalService
from financial_intelligence_agent.engine.snapshots import manifest,verify,publish_pointer

def series(key,vals):
    return Series(key,"FRED","percent","daily",tuple(Point(date(2026,1,i+1),v) for i,v in enumerate(vals)))

class PlannerSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.svc=AnalyticalService({"SOFR":series("SOFR",[3.5,3.6,3.7]),
                                     "IORB":series("IORB",[3.55,3.55,3.6])})
    def test_multistep_typed_plan(self):
        r=self.svc.run({"steps":[
            {"id":"spread","operator":"spread_basis_points","inputs":["SOFR","IORB"]},
            {"id":"moving","operator":"rolling_mean","inputs":["spread"],"window":2}
        ],"result_id":"moving"})
        self.assertEqual(r["unit"],"basis_points")
        self.assertEqual(r["observation_count"],3)
        self.assertEqual(r["latest_observation"]["date"],"2026-01-03")
        self.assertEqual(len(r["execution_trace"]),2)
        self.assertEqual(len(r["source_series"]),2)
    def test_rejects_capability_escalation(self):
        invalid=[
            {"steps":[{"id":"r","operator":"raw_sql","inputs":["SOFR"]}],"result_id":"r"},
            {"steps":[{"id":"r","operator":"identity","inputs":["SOFR"],"python":"exec"}],"result_id":"r"},
            {"steps":[{"id":"r","operator":"identity","inputs":["missing"]}],"result_id":"r"},
            {"steps":[{"id":"r","operator":"identity","inputs":["SOFR"]}],"result_id":"SOFR"},
            {"steps":[],"result_id":"r"},
        ]
        for req in invalid:
            with self.subTest(req=req),self.assertRaises(ValueError):
                self.svc.run(req)
    def test_manifest_checksum_and_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/"rates.csv").write_text("observation_date,value\n2026-01-01,3.5\n")
            m=manifest(root,{"rates":"rates.csv"},
                       captured_at=datetime(2026,10,16,tzinfo=timezone.utc),
                       source_run_id="fixture-test")
            self.assertTrue(verify(root,m))
            ptr=publish_pointer(root,m)
            self.assertEqual(json.loads(ptr.read_text())["manifest_sha256"],m["manifest_sha256"])
            (root/"rates.csv").write_text("tampered")
            with self.assertRaisesRegex(ValueError,"integrity mismatch"):
                verify(root,m)
    def test_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,"relative"):
                manifest(d,{"raw":"../secrets"},captured_at=datetime.now(timezone.utc),source_run_id="id")

if __name__=="__main__":
    unittest.main()
