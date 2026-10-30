"""Operation-level NY Fed Markets API chart and underlying transaction detail.

An operation is *not* a statistical series. The correct chartable representation
is one observation per business date and type, with explicitly additive monetary
fields summed and the original operation records retained below the chart.
No means of operations or rates are silently summed. Filter operation type on
the client: some Markets API filtering parameters do not filter as advertised.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Mapping

from financial_intelligence_agent.engine.operators import Point,Series,day,dec
from financial_intelligence_agent.live.nyfed_explorer import PublicDataset,records_from_path

DATE_KEYS=("operationDate","operation_date","date")
TYPE_KEYS=("operationType","type","operation")
ADDITIVE_METRICS={
    # Repo operation result API monetary amounts are reported as USD amounts,
    # e.g. 200000000 represents $200 million, NOT 200m * millions.
    "totalAmtAccepted":"USD",
    "totalAmtSubmitted":"USD",
}
MAX_OPERATION_ROWS=10000


@dataclass(frozen=True)
class OperationTable:
    endpoint: str
    metric: str
    unit: str
    detail: tuple[dict[str,Any],...]
    daily: tuple[dict[str,Any],...]
    series: dict[str,Series]
    field_notes: tuple[str,...]


def _field(row:Mapping[str,Any],candidates:tuple[str,...])->str|None:
    return next((k for k in candidates if k in row),None)


def aggregate_operations(dataset:PublicDataset, *,
                         record_path:str="repo.operations",
                         metric:str="totalAmtAccepted",
                         operation_type:str|None=None) ->OperationTable:
    if metric not in ADDITIVE_METRICS:
        raise ValueError("Only independently additive monetary amount fields may be summed")
    rows=records_from_path(dataset,record_path)
    if not rows or len(rows)>MAX_OPERATION_ROWS:
        raise ValueError("Need 1..10000 operation records")
    groups:dict[tuple[date,str],list[Decimal]]={}
    detail=[]
    for source in rows:
        if not isinstance(source,dict):raise ValueError("Operation row not a JSON object")
        date_key=_field(source,DATE_KEYS)
        typ_key=_field(source,TYPE_KEYS)
        if date_key is None or typ_key is None:
            raise ValueError("Operation date/type must be explicitly present")
        observed=day(source[date_key])
        operation=str(source[typ_key]).strip()
        if not operation or observed is None:raise ValueError("Invalid operation identity")
        if operation_type is not None and operation.casefold()!=operation_type.casefold():
            continue
        value=dec(source.get(metric))
        if value is None:
            # Do not treat missing operation amounts as zero and distort sums.
            raise ValueError("Missing/invalid monetary amount in operation detail")
        groups.setdefault((observed,operation),[]).append(value)
        detail.append(dict(source))
    if not detail:
        raise ValueError("No operation records for this type/date interval")
    daily=[]
    series_by_type:dict[str,list[Point]]={}
    for (observed,typ),amounts in sorted(groups.items()):
        total=sum(amounts,Decimal(0))
        daily.append({
            "observation_date":observed.isoformat(),
            "operation_type":typ,
            "metric":metric,
            "unit":ADDITIVE_METRICS[metric],
            "total":str(total),
            "operations":len(amounts),
        })
        series_by_type.setdefault(typ,[]).append(Point(observed,total))
    series={}
    for typ,points in series_by_type.items():
        # Series keys may not contain spaces; include a deterministic suffix.
        import hashlib
        suffix=hashlib.sha256(typ.encode()).hexdigest()[:8]
        key="NYOP_"+metric+"_"+suffix
        series[typ]=Series(key,"NYFED",ADDITIVE_METRICS[metric],"daily",
                           tuple(points),"operation_date",
                           (dataset.endpoint,record_path,metric,typ,"daily_sum"))
    return OperationTable(
        dataset.endpoint,metric,ADDITIVE_METRICS[metric],tuple(detail),
        tuple(daily),series,(
            "Daily values equal the SUM of reported amounts for a given operation type; no cross-type double-counting.",
            "The detail table preserves all original publisher operation records for audit and CSV export.",
            "No rate, price, percentage, or non-additive field is ever summed.",
            "Operation dates are not independent verification of publication time.",
        ))


def metric_options(dataset:PublicDataset,record_path:str)->tuple[str,...]:
    rows=records_from_path(dataset,record_path)
    if not rows:return ()
    return tuple(name for name in ADDITIVE_METRICS
                 if all(name in row and row[name] is not None for row in rows))


def operation_types(dataset:PublicDataset,record_path:str)->tuple[str,...]:
    result=set()
    for item in records_from_path(dataset,record_path):
        k=_field(item,TYPE_KEYS)
        if k and item.get(k):result.add(str(item[k]).strip())
    return tuple(sorted(result))
