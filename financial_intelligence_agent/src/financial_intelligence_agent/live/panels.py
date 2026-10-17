"""Institution-level FDIC cross-sectional analytics.

Unlike category breadth in aggregate H.8, this estimates contraction breadth
across distinct reported bank identifiers matched year over year. The covered
population, exclusions and survivorship limitations are always returned.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date,datetime,timezone
from decimal import Decimal
from urllib.parse import urlencode
from typing import Any,Callable
import re

from financial_intelligence_agent.engine.operators import Series,Point,dec,day
from financial_intelligence_agent.engine.adapters import fetch_json
from financial_intelligence_agent.live.providers import validate_dates
from financial_intelligence_agent.live.fdic import FDIC_URL

PAGE=1000
MAX_RECORDS=40000

@dataclass(frozen=True)
class BankDatum:
    cert: int
    date: date
    value: Decimal|None
    assets: Decimal|None

@dataclass(frozen=True)
class Panel:
    field: str
    records: tuple[BankDatum,...]
    source: str="FDIC"

    def __post_init__(self):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,19}",self.field):
            raise ValueError("Invalid panel field")
        if not self.records or len(self.records)>MAX_RECORDS:
            raise ValueError("Panel requires 1..40000 records")
        keys=[(v.cert,v.date) for v in self.records]
        if len(keys)!=len(set(keys)):
            raise ValueError("Duplicate institution and report date")


def fetch_bank_panel(field:str,start:str,end:str,*,fetch:Callable[...,dict]|None=None)->Panel:
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,19}",field):
        raise ValueError("Invalid financial field")
    a,b=validate_dates(start,end)
    reader=fetch or fetch_json
    items=[]
    total=None
    for offset in range(0,MAX_RECORDS,PAGE):
        params={
            "filters":f"REPDTE:[{a.isoformat()} TO {b.isoformat()}]",
            "fields":",".join(dict.fromkeys(["CERT","REPDTE",field,"ASSET"])),
            "sort_by":"REPDTE","sort_order":"ASC",
            "limit":PAGE,"offset":offset,"format":"json",
        }
        url=FDIC_URL+"?"+urlencode(params)
        try:
            payload=reader(url,allowed_hostname="api.fdic.gov",timeout_seconds=15,max_bytes=5_000_000)
        except Exception:
            raise ValueError("FDIC panel request failed; no partial population returned") from None
        if not isinstance(payload.get("meta"),dict) or not isinstance(payload.get("data"),list):
            raise ValueError("Incomplete FDIC panel response")
        page_total=int(payload["meta"].get("total",-1))
        if page_total<0 or page_total>MAX_RECORDS:
            raise ValueError("FDIC panel size exceeds 40,000 rows; narrow date range")
        if total is None:total=page_total
        elif total!=page_total:
            raise ValueError("FDIC source changed across pagination; retry with snapshot")
        rows=payload["data"]
        if not rows and offset<total:
            raise ValueError("Unexpected empty FDIC page")
        for item in rows:
            value=item.get("data",item) if isinstance(item,dict) else {}
            if not isinstance(value,dict) or value.get("CERT") is None or value.get("REPDTE") is None:
                raise ValueError("Missing institution/date in FDIC page")
            d=day(value["REPDTE"])
            cert=int(value["CERT"])
            if cert<=0 or d is None:raise ValueError("Invalid FDIC identity")
            items.append(BankDatum(cert,d,dec(value.get(field)),dec(value.get("ASSET"))))
        if offset+len(rows)>=total:break
    if total is None or len(items)!=total:
        raise ValueError("Incomplete paginated FDIC result; never publish partial bank breadth")
    return Panel(field,tuple(items))


def _year_ago(d:date)->date:
    try:return d.replace(year=d.year-1)
    except ValueError:return d.replace(year=d.year-1,day=28)


def contraction_breadth(panel:Panel)->dict[str,Any]:
    """Share of banks with annual decline among those with two valid reports.

    Denominator is the matched institution population for each quarter.
    Banks entering/leaving the data or mergers can bias the trend and are
    explicitly excluded, not zero-filled.
    """
    lookup={(r.cert,r.date):r for r in panel.records}
    dates=sorted({r.date for r in panel.records})
    rows=[]; points=[]
    for d in dates:
        current=[x for x in panel.records if x.date==d]
        pairs=[]
        for r in current:
            prior=lookup.get((r.cert,_year_ago(d)))
            if prior and r.value is not None and prior.value is not None and prior.value!=0:
                pairs.append((r,prior))
        if not pairs:continue
        falling=sum(r.value<prior.value for r,prior in pairs)
        share=Decimal(falling)*100/len(pairs)
        growths=sorted((r.value/prior.value-1)*100 for r,prior in pairs)
        mid=len(growths)//2
        median=growths[mid] if len(growths)%2 else (growths[mid-1]+growths[mid])/2
        weighted=[(r,prior) for r,prior in pairs if r.assets is not None and r.assets>0]
        weighted_share=(sum(r.assets for r,prior in weighted if r.value<prior.value)
                       /sum(r.assets for r,prior in weighted)*100) if weighted else None
        rows.append({
            "observation_date":d.isoformat(),"banks_reported":len(current),
            "banks_with_yoy_pair":len(pairs),
            "banks_declining":falling,"share_declining_pct":float(share),
            "median_yoy_pct":float(median),
            "asset_weighted_share_declining_pct":float(weighted_share) if weighted_share is not None else None,
        })
        points.append(Point(d,share))
    if not points:raise ValueError("Insufficient matching year-ago bank reports")
    series=Series("BANK_DECLINE_BREADTH_"+panel.field,"FDIC","percent","quarterly",tuple(points),
                  "report_period_end")
    return {
        "metric":"Matched-bank year-over-year contraction breadth",
        "field":panel.field,
        "series":series,"table":rows,
        "limitations":[
            "Banks are matched by FDIC certificate; mergers and discontinuities require separate lineage checks.",
            "Only banks with valid current and prior year observations enter the denominator.",
            "Asset-weighted breadth weights by current-period assets, not market capitalization.",
            "Latest available revised regulatory records are not a point-in-time historical vintage.",
            "A share of declining banks is not the same as aggregate H.8 loan-category contraction.",
        ],
    }
