"""Versioned publication of validated provider snapshots.

Usage (from bundle root):
  PYTHONPATH=src python -m financial_intelligence_agent.live.refresh \
      --source NYFED:SOFR --source NYFED:EFFR \
      --days 365 --out /path/to/shared-volume

No credentials are serialized. The published LATEST manifest is atomic on
a single filesystem. This is a server-side ingest building block, not proof
of a Databricks production deployment or true vintage-correctness.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import tempfile
from datetime import date,datetime,timedelta,timezone
from dataclasses import asdict
from pathlib import Path
from typing import Callable,Mapping

from financial_intelligence_agent.engine.operators import Point,Series
from financial_intelligence_agent.engine.snapshots import manifest,verify,publish_pointer
from financial_intelligence_agent.live.providers import FetchResult,fetch_series,validate_dates

MAX_SERIES=40

def _payload(r:FetchResult)->dict:
    s=r.series
    return {
        "format":"liquidity-series/v1",
        "descriptor":asdict(r.descriptor),
        "key":s.key,"source":s.source,"unit":s.unit,
        "frequency":s.frequency,"convention":s.convention,
        "retrieved_at":r.retrieved_at,"source_url":r.descriptor.source_url,
        "requested_range":[r.requested_start,r.requested_end],
        "snapshot_sha256":r.snapshot_id,
        "warning":r.warning,
        "points":[{
            "date":p.date.isoformat(),
            "value":str(p.value) if p.value is not None else None,
            "available_at":p.available_at.isoformat() if p.available_at else None,
            "revision":p.revision,
        } for p in s.points],
    }

def publish(root:str|Path,ids:list[str],start:str,end:str,
            *,fetch:Callable[[str,str,str],FetchResult]=fetch_series,
            clock:Callable[[],datetime]|None=None)->dict:
    if not isinstance(ids,list) or not 1<=len(ids)<=MAX_SERIES or len(ids)!=len(set(ids)):
        raise ValueError("Supply 1..40 unique provider IDs")
    validate_dates(start,end)
    root=Path(root).resolve()
    root.mkdir(parents=True,exist_ok=True)
    now=(clock or (lambda:datetime.now(timezone.utc)))()
    if not isinstance(now,datetime) or now.tzinfo is None:
        raise ValueError("Publication clock must have timezone")
    run_id=now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")+"-"+hashlib.sha256(
        "|".join(ids).encode()).hexdigest()[:10]
    path=root/"snapshots"/run_id
    path.mkdir(parents=True,exist_ok=False)
    logical={}
    try:
        for i,series_id in enumerate(ids):
            r=fetch(series_id,start,end)
            if len(r.series.points)<1:raise ValueError("Empty series "+series_id)
            # IDs are not path components. All filenames are fixed ordinal.
            name=f"series-{i:03d}.json"
            raw=json.dumps(_payload(r),sort_keys=True,separators=(",",":")).encode("utf-8")
            with (path/name).open("xb") as stream:
                stream.write(raw)
                stream.flush();os.fsync(stream.fileno())
            logical[series_id]="snapshots/"+run_id+"/"+name
        published=manifest(root,logical,captured_at=now,source_run_id=run_id)
        verify(root,published)
        # Only after ALL source requests, artifact writes and checksums succeed.
        pointer=publish_pointer(root,published)
        return {"manifest":published,"pointer":str(pointer),"run_id":run_id}
    except Exception:
        # Failed runs remain unreferenced; no previously published LATEST
        # pointer is touched. A janitor can clean stale orphan directories.
        raise

def read_published(root:str|Path)->tuple[dict,dict]:
    """Validate all content bytes before using any source in a serving query."""
    root=Path(root)
    m=json.loads((root/"LATEST.json").read_text(encoding="utf-8"))
    verify(root,m)
    rows={}
    for key,asset in m["assets"].items():
        path=(root/asset["path"]).resolve()
        data=json.loads(path.read_text(encoding="utf-8"))
        if data.get("format")!="liquidity-series/v1":
            raise ValueError("Unknown source snapshot schema")
        rows[key]=data
    return m,rows

def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser(description="Publish verified live source snapshots")
    parser.add_argument("--source",action="append",required=True,
                        help="e.g. NYFED:SOFR, FRED:WALCL, NYFED_PD:PDPOSGST-TOT")
    parser.add_argument("--days",type=int,default=365)
    parser.add_argument("--out",required=True,help="Existing writable volume/directory")
    args=parser.parse_args(argv)
    if not 1<=args.days<=3650:
        parser.error("--days must be between 1 and 3650")
    today=date.today()
    result=publish(args.out,args.source,(today-timedelta(days=args.days)).isoformat(),
                   today.isoformat())
    print(json.dumps({"run_id":result["run_id"],
                      "asset_count":len(result["manifest"]["assets"]),
                      "manifest_sha256":result["manifest"]["manifest_sha256"]},indent=2))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
