"""FDIC BankFind financials for bank-level Call Report analysis.

FDIC and FFIEC are distinct custodians/outputs; the API gives FDIC-reported
bank financial data, not a replacement for raw FFIEC CDR filing archives.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import datetime,timezone
from urllib.parse import urlencode
from typing import Callable

from financial_intelligence_agent.engine.operators import Series,Point,day,dec
from financial_intelligence_agent.engine.adapters import fetch_json
from financial_intelligence_agent.live.providers import Descriptor,FetchResult,validate_dates,MAX_OBSERVATIONS

FDIC_URL="https://api.fdic.gov/banks/financials"
# Financial fields are maintained by FDIC; these are common examples, not a
# complete taxonomy of >2000 published fields.
FIELDS={
    "ASSET":"Total assets",
    "DEP":"Total deposits",
    "LNLSGR":"Gross loans and leases",
    "LNLSNET":"Net loans and leases",
    "EQ":"Total equity capital",
    "NETINC":"Net income",
    "ROA":"Return on assets",
    "ROE":"Return on equity",
    "NCLNLSR":"Noncurrent loans and leases ratio",
    "NALTOT":"Nonaccrual loans",
    "IDT1CER":"Tier 1 capital ratio",
}
PERCENT_FIELDS={"ROA","ROE","NCLNLSR","IDT1CER"}


def bank_financial(cert:int,field:str,start:str,end:str,*,fetch:Callable[...,dict]|None=None)->FetchResult:
    if type(cert) is not int or cert<=0 or cert>999999:
        raise ValueError("FDIC certificate must be an integer between 1 and 999999")
    if not re.fullmatch("[A-Z][A-Z0-9_]{1,19}",field):
        raise ValueError("Invalid FDIC financial field code")
    a,b=validate_dates(start,end)
    params={
        "filters":f"CERT:{cert} AND REPDTE:[{a.isoformat()} TO {b.isoformat()}]",
        "fields":"CERT,REPDTE,"+field,
        "sort_by":"REPDTE","sort_order":"ASC",
        "limit":MAX_OBSERVATIONS,"format":"json",
    }
    url=FDIC_URL+"?"+urlencode(params)
    reader=fetch or fetch_json
    payload=reader(url,allowed_hostname="api.fdic.gov",timeout_seconds=15,max_bytes=5_000_000)
    records=payload.get("data")
    if not isinstance(records,list):
        raise ValueError("Malformed FDIC BankFind response")
    metadata=payload.get("meta",{})
    if isinstance(metadata,dict) and int(metadata.get("total",len(records)))>MAX_OBSERVATIONS:
        raise ValueError("FDIC financial result exceeds page size: narrow date range")
    pts=[]
    for raw in records:
        row=raw.get("data",raw) if isinstance(raw,dict) else {}
        if not isinstance(row,dict):raise ValueError("Malformed FDIC row")
        if str(row.get("CERT"))!=str(cert):continue
        d=day(row.get("REPDTE"))
        if d is None or not a<=d<=b:continue
        value=dec(row.get(field))
        if value is not None:
            pts.append(Point(d,value))
    if not pts:
        raise ValueError("No FDIC observations for certificate/field/period")
    unit="percent" if field in PERCENT_FIELDS else "USD thousands (verify field definition)"
    key=f"FDIC_{cert}_{field}"
    series=Series(key,"FDIC",unit,"quarterly",tuple(pts),"report_period_end")
    desc=Descriptor("FDIC:"+str(cert)+":"+field,FIELDS.get(field,field)+" · cert "+str(cert),
                    "FDIC",unit,"quarterly","Bank-level financial reports",
                    "https://api.fdic.gov/banks/docs","report_period_end")
    return FetchResult(series,desc,datetime.now(timezone.utc).isoformat(),
                       series.fingerprint(),start,end,
                       "Bank data reflect reported periods; release dates, mergers and revisions are not yet modeled")
