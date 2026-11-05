"""Admin-domain-constrained explorer for additional NY Fed Markets API families.

The explorer supports documented JSON endpoints under fixed public Markets
API roots. It never accepts arbitrary hosts, methods, request bodies, headers,
or secret query parameters. Analysts must explicitly select date/value fields
and aggregation policy; no field labels/units are inferred from arbitrary JSON.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date,datetime,timezone
from decimal import Decimal
from typing import Any, Callable, Mapping
from urllib.parse import urlencode
import re

from financial_intelligence_agent.engine.operators import Series,Point,dec,day
from financial_intelligence_agent.engine.adapters import fetch_json

NY_BASE="https://markets.newyorkfed.org/api"
ROOTS={"rp","seclending","soma","pd","rates","tsy","ambs","cb","cbliquidity"}
SEGMENT=re.compile(r"^[A-Za-z0-9_-]{1,60}$")
LIMIT=10000


@dataclass(frozen=True)
class PublicDataset:
    endpoint:str
    fetched_at:str
    payload:dict
    candidate_paths:tuple[str,...]
    warning:str = "Unmapped public JSON. Verify source schema, units and definitions."


def validate_endpoint(path:str)->str:
    if not isinstance(path,str) or len(path)>240 or not path.endswith(".json"):
        raise ValueError("Supply a documented Markets API relative .json path")
    path=path.strip("/")
    segments=path.removesuffix(".json").split("/")
    if len(segments)<2 or len(segments)>8 or segments[0] not in ROOTS:
        raise ValueError("NY Fed API root/path not allowlisted")
    if any(not SEGMENT.fullmatch(part) for part in segments):
        raise ValueError("Invalid NY Fed JSON endpoint path")
    return path


def _candidate_paths(payload:dict)->tuple[str,...]:
    found=[]
    def walk(value,prefix="",depth=0):
        if depth>6 or len(found)>100:return
        if isinstance(value,list) and value and all(isinstance(v,dict) for v in value):
            found.append(prefix)
        elif isinstance(value,dict):
            for k,v in value.items():
                if SEGMENT.fullmatch(str(k)):
                    walk(v,prefix+"."+k if prefix else k,depth+1)
    walk(payload)
    return tuple(found)


def discover(endpoint:str,*,start:str|None=None,end:str|None=None,
             fetch:Callable[...,dict]|None=None)->PublicDataset:
    endpoint=validate_endpoint(endpoint)
    params={}
    if start or end:
        if not start or not end:
            raise ValueError("Provide both dates or neither")
        a,b=day(start),day(end)
        if a is None or b is None or a>b or (b-a).days>365*20:
            raise ValueError("Invalid discovery date range")
        params={"startDate":a.isoformat(),"endDate":b.isoformat()}
    url=NY_BASE+"/"+endpoint+("?" + urlencode(params) if params else "")
    reader=fetch or fetch_json
    try:
        payload=reader(url,allowed_hostname="markets.newyorkfed.org",
                       timeout_seconds=15,max_bytes=5_000_000)
    except Exception:
        raise ValueError("NY Fed public endpoint unavailable or incompatible") from None
    if not isinstance(payload,dict):
        raise ValueError("Expected JSON object")
    return PublicDataset(endpoint,datetime.now(timezone.utc).isoformat(),
                         payload,_candidate_paths(payload))


def records_from_path(data:PublicDataset,path:str)->list[dict]:
    if path not in data.candidate_paths:
        raise ValueError("Record path must be discovered from publisher response")
    block=data.payload
    for part in path.split("."):
        if not isinstance(block,dict) or part not in block:
            raise ValueError("Invalid mapped JSON path")
        block=block[part]
    if not isinstance(block,list) or any(not isinstance(x,dict) for x in block):
        raise ValueError("Selected path is not a list of records")
    if len(block)>LIMIT:
        raise ValueError("More than 10,000 rows: narrow the endpoint/date range")
    return block


def map_series(data:PublicDataset,*,path:str,key:str,date_field:str,value_field:str,
               unit:str,frequency:str,aggregation:str="reject",
               filter_field:str|None=None,filter_value:str|None=None)->Series:
    records=records_from_path(data,path)
    if not SEGMENT.fullmatch(date_field) or not SEGMENT.fullmatch(value_field):
        raise ValueError("Column names must match publisher JSON fields")
    if not SEGMENT.fullmatch(key) or not unit.strip() or len(unit)>90:
        raise ValueError("Provide a valid key and explicit source unit")
    if aggregation not in {"reject","sum","mean","last"}:
        raise ValueError("Unsupported same-day aggregation policy")
    if filter_field is not None and not SEGMENT.fullmatch(filter_field):
        raise ValueError("Invalid filter field")
    by_date:dict[date,list[Decimal]]={}
    for row in records:
        if filter_field is not None and str(row.get(filter_field))!=str(filter_value):
            continue
        if date_field not in row or value_field not in row:
            raise ValueError("Selected fields are absent in at least one record")
        try:
            observed=day(row[date_field])
            value=dec(row[value_field])
        except Exception:
            raise ValueError("Publisher field cannot be parsed as ISO date/numeric value") from None
        if observed is None or value is None:
            continue
        by_date.setdefault(observed,[]).append(value)
    if not by_date:
        raise ValueError("No complete numerical observations after filtering")
    points=[]
    for observed,values in sorted(by_date.items()):
        if len(values)>1 and aggregation=="reject":
            raise ValueError("Multiple records on the same date; select explicit aggregation/filter")
        if aggregation=="sum":v=sum(values)
        elif aggregation=="mean":v=sum(values)/len(values)
        else:v=values[-1]
        points.append(Point(observed,v))
    return Series(key,"NYFED",unit,frequency,tuple(points),
                  "unverified_generic_publisher_date",
                  ("NYFED_endpoint:"+data.endpoint,
                   "record_path:"+path,"fields:"+date_field+":"+value_field,
                   "aggregation:"+aggregation))
