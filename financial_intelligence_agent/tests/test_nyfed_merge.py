"""Regression tests: sparse NY Fed operation types survive batched downloads."""
import sys
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))

from financial_intelligence_agent.live.nyfed_datasets import fetch_dataset
from financial_intelligence_agent.live.studio import _merge_ny_result


def mbs_batch(day, rows):
    """Build a real adapter result from a synthetic NY Fed summary response."""
    body={"ambs":{"operations":[
        {"operationDate":day,"operationType":kind,
         "totalAmtAcceptedPar":str(amount)}
        for kind,amount in rows
    ]}}
    return fetch_dataset(
        "NYFED_DATASET:AMBS",day,day,fetch=lambda url,**kwargs:body)


class BatchedNYFedMergeTests(unittest.TestCase):
    def test_coupon_swap_metadata_survives_when_later_batch_lacks_type(self):
        first=mbs_batch("2026-01-10",[
            ("Coupon Swap",10),("Dollar Roll",20)])
        later=mbs_batch("2026-08-10",[
            ("Dollar Roll",30),("Outright Purchase",40)])
        self.assertIn("Coupon swaps",first.units)
        self.assertNotIn("Coupon swaps",later.units)

        merged=_merge_ny_result(first,later)
        self.assertEqual(set(merged.series),set(merged.units))
        self.assertEqual(merged.units["Coupon swaps"],"USD")
        self.assertEqual(merged.series["Coupon swaps"].points[0].value,Decimal(10))
        self.assertEqual(
            [point.value for point in merged.series["Dollar rolls"].points],
            [Decimal(20),Decimal(30)])
        self.assertEqual(
            [point.value for point in merged.series["Gross activity (accepted par)"].points],
            [Decimal(30),Decimal(70)])
        # This exact lookup used to throw KeyError("Coupon swaps") in _load_nyfed.
        for label in merged.series:
            self.assertEqual(merged.units[label],"USD")
        self.assertEqual(len(merged.detail),4)

    def test_coupon_swap_in_later_batch_also_merges_correctly(self):
        first=mbs_batch("2026-01-10",[("Dollar Roll",20)])
        later=mbs_batch("2026-08-10",[("Coupon Swap",10)])
        merged=_merge_ny_result(first,later)
        self.assertEqual(set(merged.series),set(merged.units))
        self.assertIn("Coupon swaps",merged.units)

    def test_conflicting_units_rejected_instead_of_silent_corruption(self):
        first=mbs_batch("2026-01-10",[("Dollar Roll",20)])
        later=mbs_batch("2026-08-10",[("Dollar Roll",30)])
        conflicting=replace(later,units={**later.units,"Dollar rolls":"EUR"})
        with self.assertRaisesRegex(ValueError,"unit changed"):
            _merge_ny_result(first,conflicting)

    def test_no_numeric_historical_batch_merges_with_valid_recent_data(self):
        body={"ambs":{"operations":[
            {"operationDate":"2025-02-10","operationType":"Coupon Swap",
             "auctionStatus":"Canceled","totalAmtAcceptedPar":None}]}}
        older=fetch_dataset(
            "NYFED_DATASET:AMBS","2025-02-10","2025-02-10",
            fetch=lambda url,**kwargs:body)
        recent=mbs_batch("2026-08-10",[("Outright Purchase",50)])
        for first,last in ((older,recent),(recent,older)):
            merged=_merge_ny_result(first,last)
            self.assertEqual(len(merged.detail),2)
            self.assertEqual(merged.units["Outright purchases"],"USD")
            self.assertEqual(merged.series["Outright purchases"].points[0].value,
                             Decimal(50))
            self.assertTrue(any("no usable accepted" in w for w in merged.warnings))
            self.assertNotIn("Coupon swaps",merged.series)

    def test_all_missing_historical_batches_preserve_details_without_chart(self):
        def no_amount(day):
            body={"ambs":{"operations":[{
                "operationDate":day,"operationType":"Coupon Swap",
                "totalAmtAcceptedPar":None,"auctionStatus":"Canceled"}]}}
            return fetch_dataset(
                "NYFED_DATASET:AMBS",day,day,
                fetch=lambda url,**kwargs:body)
        a,b=no_amount("2025-02-10"),no_amount("2025-07-10")
        merged=_merge_ny_result(a,b)
        self.assertEqual(merged.series,{})
        self.assertEqual(len(merged.detail),2)
        self.assertEqual(len(merged.warnings),4)

    def test_other_operation_types_retained_when_missing_in_new_batch(self):
        first=mbs_batch("2026-01-10",[
            ("Outright Purchase",10),("Dollar Roll",20),("Coupon Swap",5)])
        later=mbs_batch("2026-08-10",[("Outright Sale",7)])
        merged=_merge_ny_result(first,later)
        for name in ("Outright purchases","Dollar rolls","Coupon swaps",
                     "Outright sales","Gross activity (accepted par)"):
            self.assertIn(name,merged.units)
            self.assertIn(name,merged.series)


if __name__=="__main__":
    unittest.main()
