import sys
import unittest
from pathlib import Path
from datetime import date,timedelta
from decimal import Decimal

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from financial_intelligence_agent.engine.operators import Point,Series,execute,OPERATOR_SPECS
from financial_intelligence_agent.engine.ml import make_supervised,walk_forward,unsupervised_analysis

def make_source(values,unit="percent",frequency="daily",offset=0,public=True):
    start=date(2025,1,1)+timedelta(days=offset)
    return Series("SER","TEST",unit,frequency,tuple(
        Point(start+timedelta(days=i),v,start+timedelta(days=i+1) if public else None)
        for i,v in enumerate(values)))

class HardeningTests(unittest.TestCase):
    def test_all_48_operators_execute_without_dynamic_code(self):
        a=make_source([float(i+2) for i in range(90)])
        b=make_source([float(i+4) for i in range(90)])
        for op,spec in OPERATOR_SPECS.items():
            with self.subTest(operator=op):
                inputs=(a,b) if spec.arity==2 else (a,)
                result=execute(op,*inputs,window=8)
                self.assertEqual(len(result.points),90)
                if op.startswith("year_over_year_"):
                    # Ninety days cannot contain a prior-year match.
                    self.assertTrue(all(p.value is None for p in result.points),op)
                else:
                    self.assertTrue(any(p.value is not None for p in result.points),op)

    def test_strict_basis_points_unit(self):
        a=make_source([1,2,3],unit="USD")
        with self.assertRaisesRegex(ValueError,"percent"):
            execute("basis_point_change",a)

    def test_year_ago_uses_calendar_dates_not_row_shift(self):
        values=[1,2,3,4,5]
        dates=[date(2025,1,1),date(2025,1,2),date(2025,2,1),date(2026,1,1),date(2026,2,1)]
        s=Series("A","TEST","USD","monthly",tuple(Point(d,v) for d,v in zip(dates,values)))
        actual=execute("year_over_year_change",s)
        self.assertEqual(actual.points[3].value,Decimal(3))
        self.assertEqual(actual.points[4].value,Decimal(2))
        self.assertIsNone(actual.points[2].value)

    def test_availability_taint_not_backdated(self):
        a=make_source([1,2,3,4])
        r=execute("rolling_mean",a,window=2)
        self.assertEqual(r.points[-1].available_at,date(2025,1,5))
        b=make_source([1,2,3,4],public=False)
        self.assertIsNone(execute("rolling_mean",b,window=2).points[-1].available_at)

    def test_purge_uses_actual_target_dates(self):
        days=[date(2025,1,1)+timedelta(days=i) for i in range(110)]
        feats=Series("X","TEST","value","daily",tuple(Point(d,i if i%3 else None) for i,d in enumerate(days)))
        tgt=Series("Y","TEST","value","daily",tuple(Point(d,i) for i,d in enumerate(days)))
        ds=make_supervised({"X":feats},tgt,horizon=4)
        r=walk_forward(ds,algorithm="historical_mean",initial_train=30,test_size=4,folds=3)
        for fold in r["splits"]:
            self.assertLess(fold["train_last_label_date"],fold["test_start"])

    def test_unsupervised_algorithm_rejects_unregistered(self):
        days=[date(2025,1,1)+timedelta(days=i) for i in range(45)]
        s=Series("X","TEST","unit","daily",tuple(Point(d,i) for i,d in enumerate(days)))
        ds=make_supervised({"X":s},s,horizon=1)
        with self.assertRaisesRegex(ValueError,"Unregistered"):
            unsupervised_analysis(ds,algorithm="arbitrary_package")

if __name__=="__main__":
    unittest.main()
