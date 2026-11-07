"""Regression tests for FRED empty-range -> complete-history behavior."""
import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from financial_intelligence_agent.live.fred_fallback import (
    fetch_with_full_history_fallback,
)


def result(*dates):
    return SimpleNamespace(series=SimpleNamespace(
        points=[SimpleNamespace(date=date.fromisoformat(d)) for d in dates]))


class FullHistoryFallbackTests(unittest.TestCase):
    def setUp(self):
        self.end=date(2026,10,30)
        self.start=date(2026,4,30)
        self.full_start=date(1950,1,1)

    def test_nonempty_requested_window_does_not_query_history(self):
        calls=[]
        def load(key,a,b):
            calls.append((a,b))
            return result("2026-07-15")
        def history(key):
            self.fail("metadata request should not run for nonempty window")
        found,a,b,used=fetch_with_full_history_fallback(
            "FRED:TEST",self.start,self.end,load=load,
            history_start=history,today=self.end)
        self.assertFalse(used)
        self.assertEqual((a,b),(self.start,self.end))
        self.assertEqual(len(calls),1)
        self.assertEqual(len(found.series.points),1)

    def test_empty_recent_window_loads_full_history(self):
        calls=[]
        def load(key,a,b):
            calls.append((key,a,b))
            return None if a==self.start else result("1950-01-03","1975-06-01")
        found,a,b,used=fetch_with_full_history_fallback(
            "FRED:OLD",self.start,self.end,
            load=load,history_start=lambda key:self.full_start,today=self.end)
        self.assertTrue(used)
        self.assertEqual((a,b),(self.full_start,self.end))
        self.assertEqual(calls,[("FRED:OLD",self.start,self.end),
                                ("FRED:OLD",self.full_start,self.end)])
        self.assertEqual(len(found.series.points),2)

    def test_cached_result_outside_requested_window_triggers_fallback(self):
        calls=[]
        def load(key,a,b):
            calls.append((a,b))
            return result("1960-01-01") if a==self.start else result("1960-01-01","1970-01-01")
        _,a,b,used=fetch_with_full_history_fallback(
            "FRED:CACHED",self.start,self.end,
            load=load,history_start=lambda key:self.full_start,today=self.end)
        self.assertTrue(used)
        self.assertEqual(len(calls),2)
        self.assertEqual(a,self.full_start)

    def test_no_numeric_observations_anywhere_raises_clear_error(self):
        with self.assertRaisesRegex(ValueError,"no numeric observations"):
            fetch_with_full_history_fallback(
                "FRED:EMPTY",self.start,self.end,
                load=lambda key,a,b:None,
                history_start=lambda key:self.full_start,today=self.end)

    def test_publisher_errors_not_swallowed_or_retried_as_empty(self):
        def fail(key,a,b):
            raise ValueError("Publisher request failed")
        with self.assertRaisesRegex(ValueError,"Publisher request failed"):
            fetch_with_full_history_fallback(
                "FRED:ERROR",self.start,self.end,
                load=fail,history_start=lambda key:self.fail("not empty"),
                today=self.end)

    def test_future_dated_metadata_does_not_fetch_invalid_range(self):
        calls=[]
        def load(key,a,b):
            calls.append((a,b))
            return None
        with self.assertRaisesRegex(ValueError,"through today"):
            fetch_with_full_history_fallback(
                "FRED:FUTURE",self.start,self.end,
                load=load,history_start=lambda key:date(2026,12,31),
                today=self.end)
        self.assertEqual(len(calls),1)


if __name__=="__main__":
    unittest.main()
