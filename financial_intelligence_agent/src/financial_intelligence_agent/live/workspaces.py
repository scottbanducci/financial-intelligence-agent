"""Portable analyst workspaces with strict schema, integrity and size bounds.

No API keys, tokens, user accounts or server-side session state are exported.
Content SHA-256 is an integrity check, NOT a digital signature or authorship.
"""
from __future__ import annotations
import hashlib
import json
from dataclasses import asdict
from datetime import datetime,timezone
from pathlib import Path
from typing import Any,Mapping
from financial_intelligence_agent.engine.operators import Series,Point,day,dec
from financial_intelligence_agent.live.providers import Descriptor

VERSION="analyst-workspace/v1"
MAX_FILE=9_000_000
MAX_SERIES=80

def _canonical(data)->bytes:
    return json.dumps(data,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode("utf-8")

def export_workspace(items:Mapping[str,Mapping[str,Any]])->bytes:
    if len(items)>MAX_SERIES:raise ValueError("Workspace exceeds series limit")
    records=[]
    for key,entry in sorted(items.items()):
        s=entry["series"];d=entry["descriptor"]
        if key!=s.key:raise ValueError("Inconsistent workspace series key")
        if not isinstance(d,Descriptor):raise ValueError("Descriptor required")
        points=[{"date":p.date.isoformat(),"value":str(p.value) if p.value is not None else None,
                 "available_at":p.available_at.isoformat() if p.available_at else None,
                 "revision":p.revision} for p in s.points]
        records.append({
            "key":key,"source":s.source,"unit":s.unit,"frequency":s.frequency,
            "convention":s.convention,"lineage":list(s.lineage),
            "points":points,"fingerprint":s.fingerprint(),"descriptor":asdict(d),
            "retrieved":entry.get("retrieved",""),"warning":entry.get("warning",""),
        })
    body={"schema":VERSION,"created_at":datetime.now(timezone.utc).isoformat(),
          "records":records,"secrets_included":False}
    body["sha256"] = hashlib.sha256(_canonical(body)).hexdigest()
    raw=json.dumps(body,indent=2,sort_keys=True).encode("utf-8")
    if len(raw)>MAX_FILE:raise ValueError("Workspace export exceeds 9MB")
    return raw

def import_workspace(raw:bytes)->dict:
    if not isinstance(raw,bytes) or len(raw)>MAX_FILE:
        raise ValueError("Invalid workspace archive size")
    data=json.loads(raw)
    if not isinstance(data,dict) or set(data)!={"schema","created_at","records","secrets_included","sha256"}:
        raise ValueError("Invalid workspace manifest schema")
    if data["schema"]!=VERSION or data["secrets_included"] is not False:
        raise ValueError("Unsupported schema or secrets flag")
    checksum=data["sha256"]
    expected=hashlib.sha256(_canonical({k:v for k,v in data.items() if k!="sha256"})).hexdigest()
    if checksum!=expected:raise ValueError("Workspace file integrity check failed")
    items=data["records"]
    if not isinstance(items,list) or len(items)>MAX_SERIES:
        raise ValueError("Too many workspace series")
    result={}
    for item in items:
        if not isinstance(item,dict) or set(item)!={"key","source","unit","frequency",
          "convention","lineage","points","fingerprint","descriptor","retrieved","warning"}:
            raise ValueError("Malformed workspace series")
        if item["key"] in result:raise ValueError("Duplicate workspace key")
        pts=tuple(Point(day(p["date"]),dec(p["value"]),day(p["available_at"]),p["revision"])
                  for p in item["points"])
        s=Series(item["key"],item["source"],item["unit"],item["frequency"],pts,
                 item["convention"],tuple(item["lineage"]))
        if s.fingerprint()!=item["fingerprint"]:
            raise ValueError("Series content has changed since export")
        descriptor=Descriptor(**item["descriptor"])
        result[item["key"]]={"series":s,"descriptor":descriptor,
                             "retrieved":item["retrieved"],"warning":item["warning"]}
    return result
