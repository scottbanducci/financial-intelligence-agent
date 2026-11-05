"""Financially meaningful NY Fed groupings and Plotly D/W/M/Y controls."""
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))

from financial_intelligence_agent.engine.operators import Point,Series
from financial_intelligence_agent.live.nyfed_analytics import (
    aggregate_observations,ambs_category,net_purchases,
    treasury_maturity_breakdown,repo_contract_outstanding,swap_dimensions,
    swap_contract_outstanding)
from financial_intelligence_agent.live.nyfed_chart_views import nyfed_figure


def data_series(key, values, unit="USD"):
    return Series(key,"NYFED",unit,"daily",
                  tuple(Point(date.fromisoformat(d),Decimal(v))
                        for d,v in values))


class AnalystViewsTests(unittest.TestCase):
    def test_operation_categories_keep_economic_distinctions(self):
        self.assertEqual(ambs_category("Outright Purchase"),"Outright purchases")
        self.assertEqual(ambs_category("Outright","P"),"Outright purchases")
        self.assertEqual(ambs_category("Outright","S"),"Outright sales")
        self.assertEqual(ambs_category("Outright"),"Other: Outright")
        self.assertEqual(ambs_category("Dollar Roll","P"),"Dollar rolls")
        self.assertEqual(ambs_category("Sale"),"Outright sales")
        self.assertEqual(ambs_category("Coupon Swap"),"Coupon swaps")
        self.assertEqual(ambs_category("Dollar Roll"),"Dollar rolls")
        self.assertEqual(ambs_category("Other Type"),"Other: Other Type")
        self.assertEqual(net_purchases(
            {date(2026,1,1):Decimal(120)},{date(2026,1,1):Decimal(50)}),
            {date(2026,1,1):Decimal(70)})

    def test_flow_sum_rate_mean_and_position_last_with_no_filled_dates(self):
        values=data_series("TEST",[
            ("2026-01-30","2"),("2026-01-31","4"),
            ("2026-02-02","10")]).points
        dates,sums=aggregate_observations(values,"M","sum")
        self.assertEqual(dates,[date(2026,1,1),date(2026,2,1)])
        self.assertEqual(sums,[6,10])
        _,means=aggregate_observations(values,"M","mean")
        self.assertEqual(means,[3,10])
        _,last=aggregate_observations(values,"M","last")
        self.assertEqual(last,[4,10])
        dates,weekly=aggregate_observations(values,"W","sum")
        self.assertEqual(dates,[date(2026,1,26),date(2026,2,2)])
        self.assertEqual(weekly,[6,10])
        _,annual=aggregate_observations(values,"Y","sum")
        self.assertEqual(annual,[16])

    def test_treasury_reported_maturity_and_net(self):
        rows=[
            {"operationDirection":"P","totalParAmtAccepted":"70",
             "maturityRangeStart":"2028-01-01","maturityRangeEnd":"2030-01-01"},
            {"operationDirection":"S","totalParAmtAccepted":"20",
             "maturityRangeStart":"2028-01-01","maturityRangeEnd":"2030-01-01"},
            {"operationDirection":"P","totalParAmtAccepted":"8"}]
        got=treasury_maturity_breakdown(rows)
        self.assertEqual(len(got),1)
        self.assertEqual(got[0]["Purchases (accepted par)"],"70")
        self.assertEqual(got[0]["Sales (accepted par)"],"20")
        self.assertEqual(got[0]["Net purchases (accepted par)"],"50")

    def test_repo_outstanding_requires_settlement_and_maturity(self):
        start,end=date(2026,10,1),date(2026,10,5)
        rows=[{"operationType":"Repo","totalAmtAccepted":"100",
               "settlementDate":"2026-10-01","maturityDate":"2026-10-03"},
              {"operationType":"Reverse Repo","totalAmtAccepted":"20",
               "settlementDate":"2026-10-02","maturityDate":"2026-10-04"}]
        values,note=repo_contract_outstanding(rows,start,end)
        self.assertIn("PARTIAL",note)
        self.assertEqual(
            [p.date for p in values["Repo observed-contract outstanding"].points],
            [date(2026,10,1),date(2026,10,2)])
        self.assertEqual(
            [p.value for p in values["Reverse repo observed-contract outstanding"].points],
            [Decimal(20),Decimal(20)])
        empty,note=repo_contract_outstanding([
            {"operationType":"Repo","totalAmtAccepted":"100"}],start,end)
        self.assertEqual(empty,{})
        self.assertIn("unavailable",note)

    def test_swap_dimensions_do_not_combine_currencies(self):
        rows=[
            {"currency":"USD","amount":"20","centralBank":"ECB"},
            {"currency":"EUR","amount":"50","centralBank":"ECB"},
            {"currency":"EUR","amount":"30","centralBank":"ECB"}]
        groups=swap_dimensions(rows)
        self.assertEqual(len(groups),2)
        self.assertEqual({(r["Facility"],r["Currency"]):r[
            "Reported transaction amount (native currency)"] for r in groups},
            {("USD-denominated","USD"):"20",
             ("Non-USD-denominated","EUR"):"80"})

    def test_swap_contract_stock_is_currency_and_counterparty_separated(self):
        start,end=date(2026,10,1),date(2026,10,5)
        records=[
            {"operationType":"USD","currency":"USD","amount":"10",
             "counterparty":"ECB","settlementDate":"2026-10-01",
             "maturityDate":"2026-10-03"},
            {"operationType":"Non-USD","currency":"JPY","amount":"20",
             "counterparty":"BoJ","settlementDate":"2026-10-01",
             "maturityDate":"2026-10-02"}]
        stock,note=swap_contract_outstanding(records,start,end)
        self.assertEqual(set(s.unit for s in stock.values()),{"USD","JPY"})
        self.assertIn("PARTIAL",note)
        self.assertTrue(all("Observed swap outstanding" in label for label in stock))
        self.assertEqual(len(stock),2)
        missing,_=swap_contract_outstanding([
            {"currency":"USD","amount":"10"}],start,end)
        self.assertEqual(missing,{})

    def test_plotly_aggregation_controls_are_vertical_below_legend(self):
        rate=data_series("R",[
            ("2026-10-01","3.8"),("2026-10-02","3.9")],unit="percent")
        vol=data_series("V",[
            ("2026-10-01","10"),("2026-10-02","20")],unit="Billions of USD")
        fig=nyfed_figure({"SOFR":rate,"IORB (Board/FRED)":rate},
                         title="Rates",mode="rates",volume={"SOFR":vol})
        controls=fig.layout.updatemenus[0]
        self.assertEqual(controls.direction,"down")
        self.assertEqual([b.label for b in controls.buttons],["D","W","M","Y"])
        self.assertGreater(controls.x,fig.layout.yaxis2.position or 1)
        self.assertGreater(fig.layout.margin.r,250)
        self.assertEqual(len(fig.data),12)
        self.assertEqual(sum(t.visible is True for t in fig.data),3)
        # The other 9 period traces are initially hidden, not downloaded anew.
        self.assertEqual(sum(t.visible is False for t in fig.data),9)


if __name__=="__main__":
    unittest.main()
