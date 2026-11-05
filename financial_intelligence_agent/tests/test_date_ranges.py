"""Coverage-aware Yahoo-style chart windows, including no-data intervals."""
import unittest,sys
from datetime import date,timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from financial_intelligence_agent.live.date_ranges import (
 PRESETS,preset_window,months_before,missing_intervals,
 merge_intervals,batched_missing,Coverage,extend)

class DateWindowTests(unittest.TestCase):
 def test_presets_and_six_month_default(self):
  today=date(2026,10,29)
  self.assertEqual(PRESETS,("5D","1M","3M","6M","YTD","1Y","5Y","10Y","ALL"))
  self.assertEqual(preset_window("6M",today),
                   (date(2026,4,29),today))
  self.assertEqual(preset_window("YTD",today),
                   (date(2026,1,1),today))
  self.assertEqual(preset_window("ALL",today,earliest=date(1990,2,1)),
                   (date(1990,2,1),today))
  self.assertEqual(months_before(date(2024,3,31),1),date(2024,2,29))
  with self.assertRaises(ValueError):preset_window("1W",today)
 def test_shorter_ranges_do_not_requery(self):
  a=date(2026,1,1); b=date(2026,10,29)
  c=Coverage([])
  reads=[]
  def loader(x,y):
   reads.append((x,y));return (x,y)
  extend(c,a,b,loader,lambda old,new:new,batch_days=365)
  self.assertEqual(len(reads),1)
  extend(c,date(2026,4,1),date(2026,6,1),loader,lambda old,new:new)
  self.assertEqual(len(reads),1)
 def test_only_missing_earlier_history(self):
  a=date(2026,3,1);b=date(2026,10,29)
  covered=[(a,b)]
  spans=batched_missing(date(2025,3,1),b,covered,max_days=500)
  self.assertEqual(spans,[(date(2025,3,1),date(2026,2,28))])
 def test_no_data_periods_are_covered_without_result(self):
  c=Coverage([])
  reads=[]
  def loader(a,b):
   reads.append((a,b))
   return None
  extend(c,date(2020,1,1),date(2020,1,2),loader,lambda o,n:n)
  extend(c,date(2020,1,1),date(2020,1,2),loader,lambda o,n:n)
  self.assertEqual(len(reads),1)
  self.assertIsNone(c.result)
 def test_merged_unordered_intervals(self):
  a=date(2026,10,1)
  self.assertEqual(merge_intervals([(a+timedelta(days=3),a+timedelta(days=4)),
               (a,a+timedelta(days=2))]),[(a,a+timedelta(days=4))])
 def test_failed_requests_not_marked(self):
  c=Coverage([])
  def boom(a,b):raise RuntimeError("publisher offline")
  with self.assertRaises(RuntimeError):
   extend(c,date(2026,1,1),date(2026,1,5),boom,lambda o,n:n)
  self.assertEqual(c.intervals,[])
 def test_bounded_fetch(self):
  c=Coverage([])
  with self.assertRaisesRegex(ValueError,"API calls"):
   extend(c,date(2020,1,1),date(2020,2,1),lambda a,b:None,
          lambda o,n:n,batch_days=1,max_calls=10)

if __name__=="__main__":unittest.main()
