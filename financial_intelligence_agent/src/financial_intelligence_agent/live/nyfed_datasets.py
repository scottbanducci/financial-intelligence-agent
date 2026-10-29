"""Analyst-facing NY Fed dataset contracts, not endpoint templates.

Fixed server-controlled routes. All public GET requests use JSON and date
bounds only; repo is additionally queried with operationTypes (defense in
depth), then filtered by actual record type because publisher filters have
sometimes been ignored. No Guide Sheets, market share, securities lending,
or SOMA datasets are indexed.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime,timezone,date
from decimal import Decimal
from typing import Any,Callable
from urllib.parse import urlencode
from collections import defaultdict
import hashlib

from financial_intelligence_agent.engine.operators import Point,Series,day,dec
from financial_intelligence_agent.live.providers import Descriptor,_get,validate_dates,NY_BASE

@dataclass(frozen=True)
class NYDataset:
    id:str
    label:str
    family:str
    endpoint:str
    description:str
    metric:str
    unit:str
    group_field:str
    mode:str
    operation_filter:str|None=None

DATASETS=(
    NYDataset("NYFED_DATASET:AMBS","Agency MBS operations","Agency MBS",
              "ambs/all/results/summary/search.json",
              "Agency MBS outright purchases, sales, dollar rolls and coupon swaps: daily accepted par amounts by operation type",
              "totalAmtAcceptedPar","USD","operationType","operations"),
    NYDataset("NYFED_DATASET:FXS","Central bank liquidity swaps","Liquidity Swaps",
              "fxs/all/search.json",
              "Central bank liquidity swap operations: daily amounts grouped by currency; currencies are not combined",
              "amount","source currency","currency","swaps"),
    NYDataset("NYFED_DATASET:RATES","NY Fed reference rates and volumes","Reference Rates",
              "rates/all/search.json",
              "SOFR, EFFR, OBFR, TGCR and BGCR: line rates (%) and bar transaction volume ($bn)",
              "percentRate","percent","type","rates"),
    NYDataset("NYFED_DATASET:RP_ALL","Repo and reverse repo operations","Repo Operations",
              "rp/results/search.json",
              "Daily accepted repo and reverse repo operation amounts, separate line per operation type",
              "totalAmtAccepted","USD","operationType","repo"),
    NYDataset("NYFED_DATASET:RP_REPO","Repo operations","Repo Operations",
              "rp/results/search.json",
              "Daily accepted repo totals and breakdown by eligible security type",
              "totalAmtAccepted","USD","securityType","repo","Repo"),
    NYDataset("NYFED_DATASET:RP_RRP","Reverse repo operations","Repo Operations",
              "rp/results/search.json",
              "Daily reverse repo accepted amounts and security type breakdown, where reported",
              "totalAmtAccepted","USD","securityType","repo","Reverse Repo"),
    NYDataset("NYFED_DATASET:TSY","Treasury securities operations","Treasury Operations",
              "tsy/all/results/summary/search.json",
              "Daily Treasury purchase (P) and sale (S) accepted par amounts",
              "totalParAmtAccepted","USD","operationDirection","treasury"),
)
BY_ID={x.id:x for x in DATASETS}
RATES=("SOFR","EFFR","OBFR","TGCR","BGCR")


def dataset_descriptors()->list[Descriptor]:
    return [Descriptor(x.id,x.label,"NYFED_DATASET",x.unit,"daily",
              x.family+" — "+x.description,NY_BASE+"/"+x.endpoint,"operation_date"
              if x.mode!="rates" else "effective_date") for x in DATASETS]


def request_url(dataset:NYDataset,start:str,end:str)->str:
    a,b=validate_dates(start,end)
    if (b-a).days>365*3:
        raise ValueError("NY Fed request window limited to three years per operation query")
    params={"startDate":a.isoformat(),"endDate":b.isoformat()}
    if dataset.operation_filter is not None:
        params["operationTypes"]=dataset.operation_filter
    return NY_BASE+"/"+dataset.endpoint+"?"+urlencode(params)


def _walk_arrays(node:Any,prefix:str="",depth:int=0)->list[tuple[str,list[dict]]]:
    if depth>5:return []
    if isinstance(node,list):
        return [(prefix,node)] if node and all(isinstance(x,dict) for x in node) else []
    if isinstance(node,dict):
        result=[]
        for k,val in node.items():
            if isinstance(k,str):
                result.extend(_walk_arrays(val,prefix+"."+k if prefix else k,depth+1))
        return result
    return []


def _rows(payload:dict,mode:str)->tuple[str,list[dict]]:
    if not isinstance(payload,dict):
        raise ValueError("NY Fed response must be JSON object")
    paths=_walk_arrays(payload)
    priority={
        "repo":("repo.operations",),
        "rates":("refRates",),
        "treasury":("treasury.auctions","treasury.operations"),
        "operations":("ambs.operations","agencyMBS.operations","agencyMbs.operations",
                      "agencyMBS.auctions","ambs.auctions"),
        "swaps":("fxSwaps.operations","fxswaps.operations","fxs.operations",
                 "fxSwaps.swaps","fxswaps.swaps","fxs.swaps"),
    }[mode]
    for expected in priority:
        for path,rows in paths:
            if path.lower()==expected.lower():return path,rows
    # If vendor changed its wrapper, reject rather than summing unrelated
    # data from multiple arrays. Only accept a single unambiguous array
    # containing schema-specific observation keys.
    def recognized(rows:list[dict])->bool:
        record=rows[0]
        if mode=="rates":return "effectiveDate" in record and "percentRate" in record
        if mode=="repo":return "operationDate" in record and "totalAmtAccepted" in record
        if mode=="treasury":return "operationDate" in record and "operationDirection" in record
        if mode=="operations":return "operationDate" in record and "operationType" in record
        return "currency" in record and any(x in record for x in ("operationDate","tradeDate","date"))
    valid=[(path,rows) for path,rows in paths if recognized(rows)]
    if len(valid)!=1:raise ValueError("NY Fed response record structure is unknown/ambiguous")
    return valid[0]


def _field(row:dict,names:tuple[str,...])->Any:
    for n in names:
        if n in row and row[n] not in (None,""):return row[n]
    return None


def _date(row:dict,mode:str)->date:
    val=_field(row,("effectiveDate",)) if mode=="rates" else _field(
        row,("operationDate","tradeDate","date","operation_date"))
    if val is None:raise ValueError("Operation date missing")
    observed=day(val)
    if observed is None:raise ValueError("Unrecognized publisher date")
    return observed


def _value(row:dict,fields:tuple[str,...])->Decimal:
    val=_field(row,fields)
    numeric=dec(val) if val is not None else None
    if numeric is None:
        raise ValueError("Missing/non-numeric published operation amount")
    return numeric


def _series(key:str,mode:str,unit:str,amounts:dict[date,Decimal],lineage:str)->Series:
    if not amounts:raise ValueError("No numeric observations to chart")
    return Series(key,"NYFED",unit,"daily",
        tuple(Point(d,v) for d,v in sorted(amounts.items())),
        "effective_date" if mode=="rates" else "operation_date",(lineage,))


@dataclass(frozen=True)
class DatasetResult:
    definition:NYDataset
    retrieved_at:str
    source_url:str
    detail:tuple[dict,...]
    daily:tuple[dict,...]
    series:dict[str,Series]
    units:dict[str,str]
    warnings:tuple[str,...]
    volume:dict[str,Series]


def fetch_dataset(dataset_id:str,start:str,end:str,*,
                  fetch:Callable[...,dict]|None=None)->DatasetResult:
    if dataset_id not in BY_ID:raise ValueError("NY Fed dataset not indexed")
    d=BY_ID[dataset_id]
    uri=request_url(d,start,end)
    payload=_get(uri,"markets.newyorkfed.org",fetch)
    path,raw=_rows(payload,d.mode)
    if len(raw)>10000:raise ValueError("NY Fed result exceeds safe row count; shorten date range")
    a,b=validate_dates(start,end)
    rows=[]
    for row in raw:
        observed=_date(row,d.mode)
        if not a<=observed<=b:continue
        if d.operation_filter is not None and str(row.get("operationType","")).casefold()!=d.operation_filter.casefold():
            continue
        rows.append(row)
    if not rows:raise ValueError("No publisher observations for selected date range")
    warnings=[
        "The chart is derived from current published responses, not a historical release-time vintage.",
        "Detailed rows reproduce the NY Fed API response, including its reported fields.",
    ]
    series:dict[str,Series]={}; units={}; volumes={}
    daily=[]
    if d.mode=="rates":
        by_type:dict[str,dict[date,Decimal]]=defaultdict(dict)
        by_volume:dict[str,dict[date,Decimal]]=defaultdict(dict)
        for row in rows:
            typ=str(row.get("type","")).upper()
            if typ not in RATES:continue
            observed=_date(row,d.mode)
            by_type[typ][observed]=_value(row,("percentRate",))
            amount=dec(row.get("volumeInBillions"))
            if amount is not None:by_volume[typ][observed]=amount
        for typ,values in sorted(by_type.items()):
            series[typ]=_series("NYRATE_"+typ,"rates","percent",values,uri)
            units[typ]="percent"
            if by_volume[typ]:
                volumes[typ]=_series("NYRATEVOL_"+typ,"rates","Billions of USD",by_volume[typ],uri)
            for dt,v in sorted(values.items()):
                daily.append({"observation_date":dt.isoformat(),"series":typ,
                              "rate_percent":str(v),
                              "volume_billions":str(by_volume[typ].get(dt,""))})
        if not series:raise ValueError("No recognized published reference rate records")
        warnings.append("Rates are percentages; volume is reported in billions USD on a separate axis.")
    else:
        grouped:dict[str,dict[date,Decimal]]=defaultdict(lambda:defaultdict(Decimal))
        security_breakdown:dict[str,dict[date,Decimal]]=defaultdict(lambda:defaultdict(Decimal))
        have_breakdown=False
        excluded_missing=0
        for row in rows:
            observed=_date(row,d.mode)
            if d.mode=="repo":
                typ=str(row["operationType"])
                group=typ if d.operation_filter is None else "Total "+d.operation_filter
                amount=_field(row,("totalAmtAccepted",))
                if dec(amount) is None:
                    excluded_missing+=1
                    continue
                value=dec(amount)
                # NY Fed repo.operations[].details[] attributes auction
                # accepted amounts to individual security classes. The
                # operation-level total must NOT be credited in full to
                # every class. Validate complete reconciliation before adding
                # the security split for this operation.
                if d.operation_filter is not None:
                    details=row.get("details")
                    if isinstance(details,list) and details:
                        allocations=[]
                        for part in details:
                            if not isinstance(part,dict) or not str(part.get("securityType","")).strip():
                                allocations=[];break
                            numeric=dec(part.get("amtAccepted"))
                            if numeric is None:allocations=[];break
                            allocations.append((str(part["securityType"]),numeric))
                        if allocations and sum((v for _,v in allocations),Decimal(0))==value:
                            for security,allocated in allocations:
                                security_breakdown[security][observed]+=allocated
                            have_breakdown=True
                    elif isinstance(row.get("securityType"),str) and row["securityType"].strip():
                        security_breakdown[row["securityType"]][observed]+=value
                        have_breakdown=True
                unit="USD"
            elif d.mode=="treasury":
                typ=str(row.get("operationDirection","")).upper()
                if typ not in ("P","S"):raise ValueError("Unrecognized Treasury operationDirection")
                group="Purchases" if typ=="P" else "Sales"
                amount=_field(row,("totalParAmtAccepted",))
                if dec(amount) is None:
                    excluded_missing+=1
                    continue
                value=dec(amount)
                unit="USD"
            elif d.mode=="operations":
                group=str(row.get("operationType","")).strip()
                if not group:raise ValueError("AMBS operationType missing")
                amount=_field(row,("totalAmtAcceptedPar","totalParAmtAccepted","totalAmtAccepted"))
                if dec(amount) is None:
                    excluded_missing+=1
                    continue
                value=dec(amount)
                unit="USD"
            else:
                group=str(row.get("currency","")).strip().upper()
                if not group:raise ValueError("Liquidity swap currency missing")
                amount=_field(row,("amount","totalAmtAccepted","totalAmount","amountAccepted","usdAmount"))
                if dec(amount) is None:
                    excluded_missing+=1
                    continue
                value=dec(amount)
                unit=group
            grouped[group][observed]+=value
        if excluded_missing:
            warnings.append(f"{excluded_missing} operation records lack accepted amounts and were excluded from sums; original rows remain in the detail table.")
        if d.mode=="repo" and d.operation_filter is not None and have_breakdown:
            for name,points in sorted(security_breakdown.items()):
                grouped["Security: "+name]=points
            warnings.append("Security-type lines are included only where an operation reports a directly attributable securityType; they need not sum to the total if some operations lack that field.")
        if d.mode=="repo" and d.operation_filter is not None and not have_breakdown:
            warnings.append("No reconciled per-security accepted amounts were available; totals only, without a fabricated security breakdown.")
        if d.mode=="swaps":warnings.append("Currencies remain separate. Never sum or equate volumes in different currencies.")
        if d.mode=="operations" and len(grouped)>1:
            totals:dict[date,Decimal]=defaultdict(Decimal)
            for values in grouped.values():
                for observed,value in values.items():totals[observed]+=value
            grouped["All operation types"]=totals
        for label,amounts in sorted(grouped.items()):
            digest=hashlib.sha256((d.id+":"+label).encode()).hexdigest()[:12]
            series[label]=_series("NY_"+digest,d.mode,unit if d.mode!="swaps" else label,amounts,uri)
            units[label]=unit if d.mode!="swaps" else label
            for observed,value in sorted(amounts.items()):
                daily.append({"observation_date":observed.isoformat(),"series":label,
                              "value":str(value),"unit":units[label]})
    return DatasetResult(d,datetime.now(timezone.utc).isoformat(),uri,
                         tuple(rows),tuple(daily),series,units,tuple(warnings),volumes)
