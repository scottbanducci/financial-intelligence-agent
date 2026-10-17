"""Explicit, auditable unit and frequency transformations.

No currency FX translation, accounting-definition translation, or implicit
up-sampling. Publication metadata survives only when every input is known.
"""
from __future__ import annotations
from dataclasses import replace
from datetime import date,timedelta
from decimal import Decimal
from calendar import monthrange
from collections import defaultdict

from financial_intelligence_agent.engine.operators import Series,Point

UNITS={
    "USD":("USD",Decimal("1")),
    "USD thousands":("USD",Decimal("1000")),
    "USD millions":("USD",Decimal("1000000")),
    "USD billions":("USD",Decimal("1000000000")),
    "USD trillions":("USD",Decimal("1000000000000")),
    "Millions of USD":("USD",Decimal("1000000")),
    "Billions of USD":("USD",Decimal("1000000000")),
    "percent":("rate",Decimal("0.01")),
    "basis_points":("rate",Decimal("0.0001")),
    "fraction":("rate",Decimal("1")),
}
ORDER={"daily":0,"weekly":1,"monthly":2,"quarterly":3,"annual":4}
AGGREGATIONS=("mean","last","first","sum","min","max")

def convert_units(s:Series,unit:str)->Series:
    if s.unit not in UNITS or unit not in UNITS:
        raise ValueError("Source and destination must have recognized exact unit definitions")
    origin,mult=UNITS[s.unit];destination,div=UNITS[unit]
    if origin!=destination:
        raise ValueError("Dimension mismatch: cannot convert across currencies or rate and dollar amounts")
    factor=mult/div
    return replace(s,unit=unit,
                   points=tuple(replace(p,value=p.value*factor if p.value is not None else None) for p in s.points),
                   lineage=s.lineage+(s.fingerprint(),"convert_units:"+unit))

def align_date_convention(s:Series)->Series:
    """Explicitly normalize labels to observation-day coordinates.

    An analyst must verify date semantics. This is not a publication-time
    adjustment, and never inserts/changes available_at fields.
    """
    return replace(s,convention="observation_date",
                   lineage=s.lineage+(s.fingerprint(),"explicit_date_coordinate_alignment"))

def period_end(d:date,frequency:str)->date:
    if frequency=="weekly":
        return d+timedelta(days=6-d.weekday())
    if frequency=="monthly":
        return d.replace(day=monthrange(d.year,d.month)[1])
    if frequency=="quarterly":
        endmonth=((d.month-1)//3+1)*3
        return date(d.year,endmonth,monthrange(d.year,endmonth)[1])
    if frequency=="annual":
        return date(d.year,12,31)
    raise ValueError("Unsupported output frequency")

def resample(s:Series,target:str,method:str="last",*,require_full_period:bool=False)->Series:
    if s.frequency not in ORDER or target not in ORDER:
        raise ValueError("Explicit regular observation frequencies required")
    if ORDER[target]<=ORDER[s.frequency]:
        raise ValueError("Only coarsening is supported; no silent upsampling or identity")
    if method not in AGGREGATIONS:
        raise ValueError("Unregistered aggregation")
    buckets=defaultdict(list)
    for p in s.select().points:
        buckets[period_end(p.date,target)].append(p)
    out=[]
    for period,points in sorted(buckets.items()):
        valid=[p for p in points if p.value is not None]
        if not valid:
            value=None
        elif method=="last":value=valid[-1].value
        elif method=="first":value=valid[0].value
        elif method=="sum":value=sum(p.value for p in valid)
        elif method=="mean":value=sum(p.value for p in valid)/len(valid)
        elif method=="min":value=min(p.value for p in valid)
        else:value=max(p.value for p in valid)
        if require_full_period and (not points or points[-1].date<period):
            # This strict completeness check is only reliable for daily
            # observations, so refuse claiming complete from other frequencies.
            if s.frequency!="daily":
                raise ValueError("Cannot verify complete period for non-daily input")
            value=None
        availability=max(p.available_at for p in points) if all(p.available_at is not None for p in points) else None
        # Point contract rejects known publication earlier than its
        # observation period-end (that would imply prepublication). If a
        # transformed period ends after the original release, mark unknown,
        # rather than fabricating release timing.
        if availability and availability<period:availability=None
        out.append(Point(period,value,availability))
    return Series(s.key,s.source,s.unit,target,tuple(out),"period_end",
                  s.lineage+(s.fingerprint(),"resample:"+target+":"+method))
