import sys
import unittest
from pathlib import Path
from datetime import date
from decimal import Decimal
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from financial_intelligence_agent.engine.operators import Point,Series,execute,execute_plan,OPERATOR_SPECS

def source(key,values,unit="percent",avail=True):
    return Series(key,"FRED",unit,"daily",tuple(Point(date(2026,1,i+1),v,date(2026,1,i+2) if avail else None) for i,v in enumerate(values)))

class Operators(unittest.TestCase):
    def test_registry_breadth(self):
        self.assertGreaterEqual(len(OPERATOR_SPECS),45)
        self.assertEqual(sum(x.arity==2 for x in OPERATOR_SPECS.values()),11)
    def test_unit_and_mixed_frequency_validation(self):
        a,b=source("a",[1,2,3]),source("b",[2,2,2],"USD")
        with self.assertRaisesRegex(ValueError,"units"):
            execute("subtract",a,b)
    def test_alignment_and_math(self):
        a=source("a",[1,2,3,4])
        b=source("b",[2,2,2,2])
        self.assertEqual(execute("subtract",a,b).points[-1].value,Decimal("2"))
        self.assertEqual(execute("spread_basis_points",a,b).points[-1].value,Decimal("200"))
        self.assertEqual(execute("rolling_mean",a,window=3).points[-1].value,Decimal("3"))
        self.assertEqual(execute("first_difference",a).points[0].value,None)
        self.assertEqual(execute("percent_change",a).points[1].value,Decimal("100"))
    def test_pti_lookahead_blocked(self):
        a=source("a",[1,2,3])
        self.assertEqual(len(a.select("2026-01-03").points),2)
        no_pub=source("b",[1,2],avail=False)
        with self.assertRaisesRegex(ValueError,"No observations"):
            execute("identity",no_pub,as_of="2026-01-03")
    def test_missing_no_infinite(self):
        a=source("a",[1,None,0,4])
        b=source("b",[0,1,2,2])
        self.assertIsNone(execute("ratio",a,b).points[0].value)
        self.assertIsNone(execute("rolling_mean",a,window=2).points[2].value)
        self.assertIsNone(execute("percent_change",a).points[3].value)
    def test_plan_restricted(self):
        a,b=source("a",[1,2,3,4]),source("b",[2,2,2,2])
        out=execute_plan({"a":a,"b":b},[
            {"id":"spread","operator":"subtract","inputs":["a","b"]},
            {"id":"smooth","operator":"rolling_mean","inputs":["spread"],"window":2}
        ])
        self.assertEqual(out["smooth"].points[-1].value,Decimal("1.5"))
        with self.assertRaises(ValueError):
            execute_plan({"a":a},[{"id":"evil","operator":"__import__","inputs":["a"]}])
        with self.assertRaises(ValueError):
            execute_plan({"a":a},[{"id":"new","operator":"identity","inputs":["a"],"sql":"DROP TABLE"}])
    def test_boundaries(self):
        a=source("a",[1,2,3])
        with self.assertRaises(ValueError):
            execute("rolling_mean",a,window=True)
        with self.assertRaises(ValueError):
            execute("rolling_mean",a,window=1001)
        with self.assertRaises(ValueError):
            execute_plan({"a":a},[{}]*41)
    def test_reproducible_fingerprint(self):
        a=source("a",[1,2,3])
        self.assertEqual(a.fingerprint(),source("a",[1,2,3]).fingerprint())

if __name__=="__main__":
    unittest.main()
