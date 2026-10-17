"""Read-only, bounded live public-data adapters.

Credentials are read exclusively server-side from environment variables.
Provider data is normalized into the immutable engine.Series contract.
No arbitrary URL supplied by the browser or an LLM is fetched.
"""
from __future__ import annotations
import csv
import io
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable
from urllib.parse import urlencode

from financial_intelligence_agent.engine.operators import Point, Series, dec, day
from financial_intelligence_agent.engine.adapters import fetch_json

KEY = re.compile(r"^[A-Za-z0-9_-]{1,60}$")
NY_BASE="https://markets.newyorkfed.org/api"
FRED_BASE="https://api.stlouisfed.org/fred/series/observations"
MAX_OBSERVATIONS=10000

# The catalog gives high-signal starting points, not an exhaustive FRED listing.
# FRED IDs are resolved by the official API; nonexistent IDs fail visibly.
FRED_PRESETS={
    "WALCL":("Federal Reserve total assets","Millions of USD","weekly"),
    "WRESBAL":("Reserve balances with Federal Reserve Banks","Millions of USD","weekly"),
    "WTREGEN":("Treasury General Account, weekly average","Millions of USD","weekly"),
    "RRPONTSYD":("ON RRP take-up","Billions of USD","daily"),
    "WSHOSHO":("Treasury securities held by Federal Reserve","Millions of USD","weekly"),
    "WSHOMCB":("Mortgage-backed securities held by Federal Reserve","Millions of USD","weekly"),
    "SOFR":("Secured Overnight Financing Rate","percent","daily"),
    "EFFR":("Effective Federal Funds Rate","percent","daily"),
    "IORB":("Interest on Reserve Balances","percent","daily"),
    "DFF":("Federal Funds Effective Rate","percent","daily"),
    "DTB3":("3-month Treasury bill rate","percent","daily"),
    "DGS2":("2-year Treasury constant maturity yield","percent","daily"),
    "DGS10":("10-year Treasury constant maturity yield","percent","daily"),
    "DGS30":("30-year Treasury constant maturity yield","percent","daily"),
    "T10Y2Y":("10y–2y Treasury yield spread","percent","daily"),
    "T10Y3M":("10y–3m Treasury yield spread","percent","daily"),
    "M2SL":("M2 money stock","Billions of USD","monthly"),
    "BOGMBASE":("Monetary base","Millions of USD","monthly"),
    "TOTRESNS":("Total reserves of depository institutions","Millions of USD","monthly"),
    "CPIAUCSL":("Consumer Price Index","index","monthly"),
    "PCEPI":("PCE price index","index","monthly"),
    "UNRATE":("US unemployment rate","percent","monthly"),
    "PAYEMS":("Total nonfarm payrolls","Thousands of persons","monthly"),
    "INDPRO":("Industrial production index","index","monthly"),
    "HOUST":("New privately owned housing starts","Thousands of units","monthly"),
    "CSUSHPINSA":("Case-Shiller US National Home Price Index","index","monthly"),
    "BAMLH0A0HYM2":("US high-yield option-adjusted spread","percent","daily"),
    "DEXUSEU":("USD per euro","USD/EUR","daily"),
    "DCOILWTICO":("WTI spot crude oil price","USD/barrel","daily"),
    "DCOILBRENTEU":("Brent spot crude oil price","USD/barrel","daily"),
    "BUSLOANS":("Commercial and industrial loans, all banks","Billions of USD","weekly"),
    "TOTALSL":("Total consumer credit outstanding","Millions of USD","monthly"),
}
NY_RATES=("SOFR","EFFR","OBFR","TGCR","BGCR")
NY_GROUP={rate:("secured" if rate in ("SOFR","TGCR","BGCR") else "unsecured") for rate in NY_RATES}
PD_CATALOG="https://markets.newyorkfed.org/api/pd/list/timeseries.json"


@dataclass(frozen=True)
class Descriptor:
    id: str
    label: str
    provider: str
    unit: str
    frequency: str
    category: str
    source_url: str
    convention: str = "observation_date"


@dataclass(frozen=True)
class FetchResult:
    series: Series
    descriptor: Descriptor
    retrieved_at: str
    snapshot_id: str
    requested_start: str
    requested_end: str
    warning: str = ""


def catalog() -> list[Descriptor]:
    result=[]
    for key,(label,unit,freq) in FRED_PRESETS.items():
        result.append(Descriptor("FRED:"+key,label,"FRED",unit,freq,"FRED macro & banking",
                                 "https://fred.stlouisfed.org/series/"+key))
    for rate in NY_RATES:
        path=f"/rates/{NY_GROUP[rate]}/{rate.lower()}/search.json"
        result.append(Descriptor("NYFED:"+rate,rate+" reference rate","NYFED","percent",
                                 "daily","NY Fed reference rates",NY_BASE+path,
                                 "effective_date"))
    return result


def validate_dates(start: str, end: str) -> tuple[date,date]:
    a,b=day(start),day(end)
    if a is None or b is None or a>b:
        raise ValueError("Choose a valid non-reversed date range")
    if (b-a).days>365*40:
        raise ValueError("Date range exceeds 40 years")
    return a,b


def _get(url: str, host: str, fetch: Callable[...,dict]|None) -> dict:
    reader=fetch or fetch_json
    try:
        return reader(url,allowed_hostname=host,timeout_seconds=15,max_bytes=5_000_000)
    except Exception as exc:
        # HTTPError and URL exceptions can embed the FRED API key in the URL.
        # No exception text or exception chaining can reach a public UI/log.
        raise ValueError("Publisher request failed or timed out; check credentials, upstream availability and series ID") from None


def _unique(key: str, provider: str, unit: str, frequency: str,
            convention: str, pts: list[Point]) -> Series:
    if not pts:
        raise ValueError("No numeric observations in the selected period")
    if len(pts)>MAX_OBSERVATIONS:
        raise ValueError("More than 10,000 observations: reduce date range")
    # Date-only view: latest encountered vintage when the response contains
    # several revisions. We deliberately retain the availability date.
    distinct: dict[date,Point]={}
    for pt in pts:
        old=distinct.get(pt.date)
        if old is None or (pt.available_at or date.min,pt.revision) >= (old.available_at or date.min,old.revision):
            distinct[pt.date]=pt
    return Series(key,provider,unit,frequency,tuple(distinct[d] for d in sorted(distinct)),convention)


def fred(key: str,start: str,end: str,*,api_key: str|None=None,
         fetch: Callable[...,dict]|None=None) -> FetchResult:
    if not KEY.fullmatch(key):
        raise ValueError("Invalid FRED series identifier")
    api_key=api_key if api_key is not None else os.getenv("FRED_API_KEY","")
    if not re.fullmatch(r"[a-z0-9]{32}",api_key):
        raise ValueError("FRED_API_KEY must be configured as a server-side 32-character API key")
    a,b=validate_dates(start,end)
    # No realtime period specified: default latest contemporary-vintage view.
    # Do not label this an as-of-known historical backtest.
    params=urlencode({"series_id":key,"api_key":api_key,"file_type":"json",
                      "observation_start":a.isoformat(),"observation_end":b.isoformat(),
                      "limit":MAX_OBSERVATIONS,"sort_order":"asc"})
    payload=_get(FRED_BASE+"?"+params,"api.stlouisfed.org",fetch)
    records=payload.get("observations")
    if not isinstance(records,list):
        raise ValueError("Malformed FRED observations response")
    if int(payload.get("count",len(records)))>MAX_OBSERVATIONS:
        raise ValueError("Series exceeds one API page; use a narrower date range")
    pts=[]
    for row in records:
        value=dec(row.get("value"))
        if value is None: continue
        d=day(row.get("date"))
        if d is None or not a<=d<=b:continue
        # FRED realtime_start indicates vintage validity, not exact intraday
        # publication timing.
        available=day(row.get("realtime_start"))
        pts.append(Point(d,value,available))
    title,unit,freq=FRED_PRESETS.get(key,(key+" (FRED series)","source-native","irregular"))
    desc=Descriptor("FRED:"+key,title,"FRED",unit,freq,"FRED macro & banking",
                    "https://fred.stlouisfed.org/series/"+key,"FRED_realtime_vintage")
    s=_unique(key,"FRED",unit,freq,"FRED_realtime_vintage",pts)
    return FetchResult(s,desc,datetime.now(timezone.utc).isoformat(),s.fingerprint(),start,end,
                       "Current FRED vintage; first release-time correctness is not established")


def ny_rate(rate: str,start: str,end: str,*,fetch: Callable[...,dict]|None=None)->FetchResult:
    rate=rate.upper()
    if rate not in NY_GROUP:raise ValueError("Unsupported NY Fed reference rate")
    a,b=validate_dates(start,end)
    uri=NY_BASE+f"/rates/{NY_GROUP[rate]}/{rate.lower()}/search.json"
    url=uri+"?"+urlencode({"startDate":a.isoformat(),"endDate":b.isoformat()})
    payload=_get(url,"markets.newyorkfed.org",fetch)
    records=payload.get("refRates")
    if not isinstance(records,list):
        raise ValueError("Malformed NY Fed refRates response")
    pts=[]
    for row in records:
        if not isinstance(row,dict) or row.get("type","").upper()!=rate:continue
        d=day(row.get("effectiveDate"))
        if d is None or not a<=d<=b:continue
        value=dec(row.get("percentRate"))
        if value is not None:
            # API has revisionIndicator but not a verifiable publication
            # timestamp. Do not invent available_at based on effectiveDate.
            pts.append(Point(d,value))
    desc=Descriptor("NYFED:"+rate,rate+" reference rate","NYFED","percent","daily",
                    "NY Fed reference rates",uri,"effective_date")
    s=_unique(rate,"NYFED","percent","daily","effective_date",pts)
    return FetchResult(s,desc,datetime.now(timezone.utc).isoformat(),s.fingerprint(),start,end,
                       "Effective dates, not independently validated release timestamps")


def dealer_catalog(*,fetch:Callable[...,dict]|None=None)->list[Descriptor]:
    payload=_get(PD_CATALOG,"markets.newyorkfed.org",fetch)
    records=payload.get("pd",{}).get("timeseries")
    if not isinstance(records,list):
        raise ValueError("NY Fed dealer metadata layout changed")
    found={}
    for item in records:
        key=item.get("keyid","")
        if not KEY.fullmatch(key):continue
        # Descriptions can differ across source's series breaks.
        if key not in found:
            found[key]=Descriptor("NYFED_PD:"+key,item.get("description",key)[:200],
                                  "NYFED_PD","USD millions","weekly","Primary dealer statistics",
                                  NY_BASE+"/pd/get/"+key+".json","as_of")
    return list(found.values())


def _dealer_records(payload:dict)->list[dict]:
    """Accept only explicitly bounded known dealer response envelopes."""
    root=payload.get("pd")
    if not isinstance(root,dict):raise ValueError("Malformed primary-dealer envelope")
    for name in ("timeseries","data","surveys","results"):
        rows=root.get(name)
        if isinstance(rows,list) and all(isinstance(r,dict) for r in rows):
            return rows
    raise ValueError("Primary-dealer records structure changed; inspect source before mapping")


def dealer(key:str,start:str,end:str,*,fetch:Callable[...,dict]|None=None,
           authorized_ids:set[str]|None=None)->FetchResult:
    if not KEY.fullmatch(key):
        raise ValueError("Invalid primary-dealer keyid")
    if authorized_ids is not None and key not in authorized_ids:
        raise ValueError("Dealer series not found in publisher metadata")
    a,b=validate_dates(start,end)
    uri=NY_BASE+"/pd/get/"+key+".json"
    payload=_get(uri,"markets.newyorkfed.org",fetch)
    rows=_dealer_records(payload)
    pts=[]
    for row in rows:
        # Dealer series can include multiple breaks; reject ambiguity.
        if "keyid" in row and row["keyid"]!=key:continue
        dates=[row.get(k) for k in ("asof","asOfDate","date","asofdate") if row.get(k)]
        vals=[row.get(k) for k in ("value","amount") if row.get(k) is not None]
        if len(dates)!=1 or len(vals)!=1:
            raise ValueError("Dealer row schema requires explicit updated adapter mapping")
        d=day(dates[0])
        if d and a<=d<=b and dec(vals[0]) is not None:
            pts.append(Point(d,dec(vals[0])))
    label=key
    desc=Descriptor("NYFED_PD:"+key,label,"NYFED_PD","USD millions","weekly",
                    "Primary dealer statistics",uri,"as_of")
    s=_unique(key,"NYFED_PD","USD millions","weekly","as_of",pts)
    return FetchResult(s,desc,datetime.now(timezone.utc).isoformat(),s.fingerprint(),start,end,
                       "Verify dealer series-break continuity and published unit definition")


def fetch_series(series_id:str,start:str,end:str,*,fetch:Callable[...,dict]|None=None,
                 api_key:str|None=None)->FetchResult:
    if series_id.startswith("FRED:"):
        return fred(series_id[5:],start,end,api_key=api_key,fetch=fetch)
    if series_id.startswith("NYFED:"):
        return ny_rate(series_id[6:],start,end,fetch=fetch)
    if series_id.startswith("NYFED_PD:"):
        return dealer(series_id[9:],start,end,fetch=fetch)
    raise ValueError("Unrecognized provider ID")
