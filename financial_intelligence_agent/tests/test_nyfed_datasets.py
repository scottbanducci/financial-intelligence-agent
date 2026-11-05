import sys,unittest
from pathlib import Path
from decimal import Decimal
from urllib.parse import urlsplit,parse_qs
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from financial_intelligence_agent.live.nyfed_datasets import (
    DATASETS,BY_ID,dataset_descriptors,request_url,fetch_dataset)

def mocked(body,expected):
    def run(url,**kwargs):
        assert urlsplit(url).path.endswith(expected),url
        assert kwargs["allowed_hostname"]=="markets.newyorkfed.org"
        return body
    return run

class NYFedDatasets(unittest.TestCase):
    A,B="2026-10-01","2026-10-03"
    def test_index_only_user_requested_families(self):
        self.assertEqual(len(DATASETS),7)
        self.assertFalse(any("soma" in d.id.lower() or "seclending" in d.id.lower() for d in DATASETS))
        self.assertTrue(all(d.provider=="NYFED_DATASET" for d in dataset_descriptors()))
    def test_ambs_source_max_date_window(self):
        with self.assertRaisesRegex(ValueError,"supported window"):
            request_url(BY_ID["NYFED_DATASET:AMBS"],"2020-01-01","2023-01-01")
    def test_repo_three_distinct_product_views(self):
        url=request_url(BY_ID["NYFED_DATASET:RP_REPO"],self.A,self.B)
        self.assertEqual(parse_qs(urlsplit(url).query)["operationTypes"],["Repo"])
        rrp=request_url(BY_ID["NYFED_DATASET:RP_RRP"],self.A,self.B)
        self.assertIn("operationTypes=Reverse%20Repo",rrp)
        data={"repo":{"operations":[
            {"operationDate":self.A,"operationType":"Repo","totalAmtAccepted":"100","securityType":"tsy"},
            {"operationDate":self.A,"operationType":"Repo","totalAmtAccepted":"25","securityType":"mbs"},
            {"operationDate":self.A,"operationType":"Reverse Repo","totalAmtAccepted":"500"},
            {"operationDate":self.B,"operationType":"Repo","totalAmtAccepted":"50","securityType":"tsy"}]}}
        fetch=mocked(data,"rp/results/search.json")
        combined=fetch_dataset("NYFED_DATASET:RP_ALL",self.A,self.B,fetch=fetch)
        self.assertEqual(combined.series["Repo accepted"].points[0].value,Decimal("125"))
        self.assertEqual(combined.series["Reverse repo accepted"].points[0].value,Decimal("500"))
        repo=fetch_dataset("NYFED_DATASET:RP_REPO",self.A,self.B,fetch=fetch)
        self.assertEqual(repo.series["Repo accepted"].points[0].value,Decimal("125"))
        self.assertEqual(repo.series["Security: tsy"].points[0].value,Decimal("100"))
        self.assertEqual(len(repo.detail),3)
        reverse=fetch_dataset("NYFED_DATASET:RP_RRP",self.A,self.B,fetch=fetch)
        self.assertEqual(len(reverse.detail),1)
        self.assertEqual(reverse.series["Reverse repo accepted"].points[0].value,Decimal("500"))
    def test_nested_repo_security_type_allocations_reconcile(self):
        body={"repo":{"operations":[
            {"operationDate":self.A,"operationType":"Repo",
             "totalAmtAccepted":"120","details":[
                 {"securityType":"tsy","amtAccepted":"80"},
                 {"securityType":"mbs","amtAccepted":"40"}]},
            {"operationDate":self.A,"operationType":"Repo",
             "totalAmtAccepted":"30","details":[
                 {"securityType":"tsy","amtAccepted":"30"}]}]}}
        r=fetch_dataset("NYFED_DATASET:RP_REPO",self.A,self.B,
          fetch=mocked(body,"rp/results/search.json"))
        self.assertEqual(r.series["Repo accepted"].points[0].value,Decimal(150))
        self.assertEqual(r.series["Security: tsy"].points[0].value,Decimal(110))
        self.assertEqual(r.series["Security: mbs"].points[0].value,Decimal(40))
    def test_unreconciled_nested_repo_amounts_omitted_not_doubled(self):
        body={"repo":{"operations":[
            {"operationDate":self.A,"operationType":"Repo",
             "totalAmtAccepted":"120","details":[
                 {"securityType":"tsy","amtAccepted":"80"}]}]}}
        r=fetch_dataset("NYFED_DATASET:RP_REPO",self.A,self.B,
          fetch=mocked(body,"rp/results/search.json"))
        self.assertEqual(set(r.series),{"Repo accepted"})
        self.assertTrue(any("reconciled" in note for note in r.warnings))
    def test_am_bs_aggregate_by_operation_type_and_total(self):
        body={"ambs":{"operations":[
            {"operationDate":self.A,"operationType":"Outright Purchase","totalAmtAcceptedPar":"100"},
            {"operationDate":self.A,"operationType":"Outright Purchase","totalAmtAcceptedPar":"30"},
            {"operationDate":self.A,"operationType":"Dollar Roll","totalAmtAcceptedPar":"70"}]}}
        r=fetch_dataset("NYFED_DATASET:AMBS",self.A,self.B,
             fetch=mocked(body,"ambs/all/results/summary/search.json"))
        self.assertEqual(r.series["Outright purchases"].points[0].value,Decimal(130))
        self.assertEqual(r.series["Gross activity (accepted par)"].points[0].value,Decimal(200))
        self.assertEqual(len(r.detail),3)
    def test_am_bs_total_available_even_for_one_operation_type(self):
        body={"ambs":{"auctions":[
            {"operationDate":self.A,"operationType":"Dollar Roll","totalAmtAcceptedPar":"9"}]}}
        r=fetch_dataset("NYFED_DATASET:AMBS",self.A,self.B,
             fetch=mocked(body,"ambs/all/results/summary/search.json"))
        self.assertEqual(r.series["Gross activity (accepted par)"].points[0].value,Decimal(9))
    def test_treasury_purchases_sales_only(self):
        body={"treasury":{"auctions":[
            {"operationDate":self.A,"operationDirection":"P","totalParAmtAccepted":"45"},
            {"operationDate":self.A,"operationDirection":"S","totalParAmtAccepted":"10"}]}}
        r=fetch_dataset("NYFED_DATASET:TSY",self.A,self.B,
             fetch=mocked(body,"tsy/all/results/summary/search.json"))
        self.assertEqual(r.series["Purchases"].points[0].value,Decimal(45))
        self.assertEqual(r.series["Sales"].points[0].value,Decimal(10))
    def test_currency_separation_no_total(self):
        body={"fxSwaps":{"operations":[
            {"operationDate":self.A,"currency":"EUR","amount":"15"},
            {"operationDate":self.A,"currency":"USD","amount":"35"}]}}
        r=fetch_dataset("NYFED_DATASET:FXS",self.A,self.B,
             fetch=mocked(body,"fxs/all/search.json"))
        self.assertEqual(set(r.series),{"Non-USD-denominated · EUR","USD-denominated · USD"})
        self.assertNotIn("Total",r.series)
        self.assertEqual(r.units["Non-USD-denominated · EUR"],"EUR")
    def test_rates_have_percentage_and_bar_volume(self):
        body={"refRates":[
            {"effectiveDate":self.A,"type":"SOFR","percentRate":"3.7","volumeInBillions":"2940"},
            {"effectiveDate":self.A,"type":"EFFR","percentRate":"3.8","volumeInBillions":"90"}]}
        r=fetch_dataset("NYFED_DATASET:RATES",self.A,self.B,
             fetch=mocked(body,"rates/all/search.json"))
        self.assertEqual(r.series["SOFR"].points[0].value,Decimal("3.7"))
        self.assertEqual(r.volume["SOFR"].points[0].value,Decimal(2940))
        self.assertEqual(len(r.detail),2)
    def test_cancelled_agency_mbs_excluded_not_zero_filled(self):
        data={"ambs":{"auctions":[
          {"operationDate":self.A,"operationType":"Purchase","totalAmtAcceptedPar":"100"},
          {"operationDate":self.A,"operationType":"Purchase","totalAmtAcceptedPar":None,
           "auctionStatus":"Canceled"}]}}
        report=fetch_dataset("NYFED_DATASET:AMBS",self.A,self.B,
              fetch=mocked(data,"ambs/all/results/summary/search.json"))
        self.assertEqual(len(report.detail),2)
        self.assertEqual(report.series["Outright purchases"].points[0].value,Decimal(100))
        self.assertTrue(any("excluded" in warning for warning in report.warnings))
    def test_historical_ambs_records_without_amounts_preserved_not_zeroed(self):
        body={"ambs":{"operations":[
            {"operationDate":self.A,"operationType":"Outright Purchase",
             "totalAmtAcceptedPar":None,"auctionStatus":"Canceled"},
            {"operationDate":self.B,"operationType":"Coupon Swap",
             "auctionStatus":"Pending"}]}}
        report=fetch_dataset("NYFED_DATASET:AMBS",self.A,self.B,
            fetch=mocked(body,"ambs/all/results/summary/search.json"))
        self.assertEqual(report.series,{})
        self.assertEqual(report.units,{})
        self.assertEqual(report.daily,())
        self.assertEqual(len(report.detail),2)
        self.assertTrue(any("2 NY Fed operation records" in w for w in report.warnings))
        self.assertTrue(any(self.A in w and self.B in w for w in report.warnings))

    def test_other_nyfed_modes_do_not_invent_zero_for_missing_amounts(self):
        body={"repo":{"operations":[
            {"operationDate":self.A,"operationType":"Repo",
             "totalAmtAccepted":None}]}}
        report=fetch_dataset("NYFED_DATASET:RP_ALL",self.A,self.B,
            fetch=mocked(body,"rp/results/search.json"))
        self.assertEqual(report.series,{})
        self.assertEqual(len(report.detail),1)
        self.assertEqual(report.daily,())

    def test_missing_measurement_does_not_create_zero(self):
        bad={"treasury":{"auctions":[{"operationDate":self.A,"operationDirection":"P"}]}}
        result=fetch_dataset("NYFED_DATASET:TSY",self.A,self.B,
                             fetch=mocked(bad,"tsy/all/results/summary/search.json"))
        self.assertFalse(result.series)
        self.assertEqual(len(result.detail),1)
        self.assertEqual(result.daily,())
    def test_mbs_strict_par_and_net_direction(self):
        body={"ambs":{"operations":[
            {"operationDate":self.A,"operationType":"Outright Purchase",
             "totalAmtAcceptedPar":"100"},
            {"operationDate":self.A,"operationType":"Outright Sale",
             "totalAmtAcceptedPar":"30"},
            {"operationDate":self.A,"operationType":"Dollar Roll",
             "totalAmtAcceptedPar":"15"},
            {"operationDate":self.A,"operationType":"Coupon Swap",
             "totalAmtAcceptedPar":"5"},
            {"operationDate":self.A,"operationType":"Outright Purchase",
             "totalAmtAccepted":"300"}]}}
        result=fetch_dataset("NYFED_DATASET:AMBS",self.A,self.B,
             fetch=mocked(body,"ambs/all/results/summary/search.json"))
        self.assertEqual(result.series["Net outright purchases"].points[0].value,Decimal(70))
        self.assertEqual(result.series["Gross activity (accepted par)"].points[0].value,Decimal(150))
        self.assertEqual(result.series["Coupon swaps"].points[0].value,Decimal(5))
        self.assertEqual(result.series["Dollar rolls"].points[0].value,Decimal(15))
        self.assertEqual(len(result.detail),5)
        self.assertTrue(any("excluded" in text for text in result.warnings))
    def test_treasury_net_includes_zero_not_double_counted(self):
        body={"treasury":{"auctions":[
            {"operationDate":self.A,"operationDirection":"P","totalParAmtAccepted":"45",
             "maturityRangeStart":"2028-01-01","maturityRangeEnd":"2030-01-01"},
            {"operationDate":self.A,"operationDirection":"S","totalParAmtAccepted":"10",
             "maturityRangeStart":"2028-01-01","maturityRangeEnd":"2030-01-01"}]}}
        result=fetch_dataset("NYFED_DATASET:TSY",self.A,self.B,
             fetch=mocked(body,"tsy/all/results/summary/search.json"))
        self.assertEqual(result.series["Net purchases"].points[0].value,Decimal(35))
if __name__=="__main__":unittest.main()
