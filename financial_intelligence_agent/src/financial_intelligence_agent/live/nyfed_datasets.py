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
from urllib.parse import urlencode,quote
from collections import defaultdict
import hashlib

from financial_intelligence_agent.engine.operators import Point,Series,day,dec
from financial_intelligence_agent.live.nyfed_analytics import ambs_category,net_purchases,swap_facility
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
    NYDataset("NYFED_DATASET:AMBS","Agency Mortgage-Backed Securities (MBS) Operations","Agency MBS",
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
    max_days=730 if dataset.mode=="operations" else 365*3
    if (b-a).days>max_days:
        raise ValueError("Requested NY Fed date range exceeds the supported window for this dataset")
    params={"startDate":a.isoformat(),"endDate":b.isoformat()}
    if dataset.operation_filter is not None:
        params["operationTypes"]=dataset.operation_filter
    return NY_BASE+"/"+dataset.endpoint+"?"+urlencode(params,quote_via=quote)


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
    # An empty valid publisher envelope still establishes that the requested
    # period has no operations. Preserve this as a negative cache interval,
    # especially for 5Y/10Y/ALL queries predating the dataset's inception.
    for expected in priority:
        node=payload
        for part in expected.split("."):
            node=node.get(part) if isinstance(node,dict) else None
            if node is None:break
        if isinstance(node,list) and not node:return expected,node
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
                if d.operation_filter is not None:
                    group=("Repo accepted" if d.operation_filter=="Repo" else
                           "Reverse repo accepted")
                else:
                    group={"repo":"Repo accepted","reverse repo":"Reverse repo accepted"}.get(
                        typ.strip().casefold(),typ+" accepted")
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
                raw_type=str(row.get("operationType","")).strip()
                if not raw_type:raise ValueError("AMBS operationType missing")
                group=ambs_category(raw_type,str(row.get("operationDirection","")))
                # Agency MBS RESULTS/SUMMARY reports accepted PAR. Other
                # monetary fields are not interchangeable measurements.
                amount=_field(row,("totalAmtAcceptedPar",))
                if dec(amount) is None:
                    excluded_missing+=1
                    continue
                value=dec(amount)
                unit="USD"
            else:
                currency=str(row.get("currency","")).strip().upper()
                if not currency:raise ValueError("Liquidity swap currency missing")
                group=swap_facility(row)+" · "+currency
                amount=_field(row,("amount","totalAmtAccepted","totalAmount","amountAccepted","usdAmount"))
                if dec(amount) is None:
                    excluded_missing+=1
                    continue
                value=dec(amount)
                unit=currency
            grouped[group][observed]+=value
        if not grouped:
            # Some historical intervals contain operation records without any
            # accepted amount (e.g. canceled or not-yet-resulted operations).
            # Preserve these records and their dates; they contribute NO chart
            # points, not fabricated zeros. Other batches can still merge.
            warnings.append(
                f"{excluded_missing} NY Fed operation records dated "
                f"{a.isoformat()} through {b.isoformat()} have no usable accepted "
                "amounts. No chart observations were generated for this interval; "
                "the original records remain available in the detail table. "
                "Review the publisher schema/status if amounts were expected.")
            return DatasetResult(d,datetime.now(timezone.utc).isoformat(),uri,
                                 tuple(rows),tuple(),{}, {},tuple(warnings),{})
        if excluded_missing:
            warnings.append(f"{excluded_missing} operation records lack accepted amounts and were excluded from sums; original rows remain in the detail table.")
        if d.mode=="repo" and d.operation_filter is not None and have_breakdown:
            for name,points in sorted(security_breakdown.items()):
                grouped["Security: "+name]=points
            warnings.append("Security-type lines are included only where an operation reports a directly attributable securityType; they need not sum to the total if some operations lack that field.")
        if d.mode=="repo" and d.operation_filter is not None and not have_breakdown:
            warnings.append("No reconciled per-security accepted amounts were available; totals only, without a fabricated security breakdown.")
        if d.mode=="swaps":
            warnings.append("Currency-denominated swap TRANSACTION FLOWS are separate; no FX conversion or summation between currencies. Outstanding balances are not inferred from flows.")
        if d.mode=="operations" and grouped:
            totals:dict[date,Decimal]=defaultdict(Decimal)
            # Only source groups enter gross; computed net is NOT an extra flow.
            for values in grouped.values():
                for observed,value in values.items():totals[observed]+=value
            grouped["Gross activity (accepted par)"]=totals
            buys=grouped.get("Outright purchases",{})
            sells=grouped.get("Outright sales",{})
            if buys or sells:
                grouped["Net outright purchases"]=net_purchases(buys,sells)
            if any(label.startswith("Other: ") for label in grouped):
                warnings.append("Unrecognized MBS operation types remain separate and count toward gross activity, not net outright purchases.")
        if d.mode=="treasury":
            grouped["Net purchases"]=net_purchases(
                grouped.get("Purchases",{}),grouped.get("Sales",{}))
            warnings.append("Net purchases = purchase accepted par minus sale accepted par; operation-date transactions, not settled SOMA holdings.")
        for label,amounts in sorted(grouped.items()):
            digest=hashlib.sha256((d.id+":"+label).encode()).hexdigest()[:12]
            chart_unit=label.rsplit(" · ",1)[-1] if d.mode=="swaps" else unit
            series[label]=_series("NY_"+digest,d.mode,chart_unit,amounts,uri)
            units[label]=chart_unit
            for observed,value in sorted(amounts.items()):
                daily.append({"observation_date":observed.isoformat(),"series":label,
                              "value":str(value),"unit":units[label]})
    return DatasetResult(d,datetime.now(timezone.utc).isoformat(),uri,
                         tuple(rows),tuple(daily),series,units,tuple(warnings),volumes)
