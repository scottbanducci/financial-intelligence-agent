import unittest,sys
from pathlib import Path
from decimal import Decimal
from datetime import date,timedelta
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from financial_intelligence_agent.engine.operators import Point,Series,execute
from financial_intelligence_agent.engine.normalization import convert_units,resample,align_date_convention
class Normalize(unittest.TestCase):
    def setUp(self):
        self.a=Series("WALCL","FRED","Millions of USD","daily",tuple(
            Point(date(2026,1,i+1),1000+i) for i in range(25)))
    def test_units_exact_scaling(self):
        b=convert_units(self.a,"USD billions")
        self.assertEqual(b.points[0].value,Decimal("1"))
        self.assertEqual(b.unit,"USD billions")
        with self.assertRaises(ValueError):convert_units(self.a,"percent")
        with self.assertRaises(ValueError):convert_units(self.a,"USD thousands (verify field definition)")
    def test_explicit_asof_label_normalization(self):
        b=align_date_convention(self.a)
        self.assertEqual(b.convention,"observation_date")
        self.assertEqual(len(b.lineage),2)
    def test_resample_month_end(self):
        b=resample(self.a,"monthly","mean")
        self.assertEqual(b.points[0].date,date(2026,1,31))
        self.assertEqual(b.points[0].value,Decimal(1012))
        self.assertIsNone(b.points[0].available_at)
        with self.assertRaises(ValueError):resample(self.a,"daily")
        with self.assertRaises(ValueError):resample(b,"weekly")
    def test_regular_cross_source_after_explicit_alignment(self):
        s1=Series("A","FRED","percent","daily",(Point("2026-01-01",3.5),Point("2026-01-02",3.6)),
                  "FRED_realtime_vintage")
        s2=Series("B","NYFED","percent","daily",(Point("2026-01-01",3.55),Point("2026-01-02",3.6)),
                  "effective_date")
        with self.assertRaisesRegex(ValueError,"conventions"):
            execute("subtract",s1,s2)
        out=execute("subtract",align_date_convention(s1),align_date_convention(s2))
        self.assertEqual(out.points[0].value,Decimal("-0.05"))
if __name__=="__main__":unittest.main()
