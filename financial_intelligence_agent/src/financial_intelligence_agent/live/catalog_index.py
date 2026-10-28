"""Persistent, startup-warmed publisher metadata index.

FRED has no single 'list every series' API call. Its official category tree
and paginated category/series methods can enumerate the catalog, but doing so
synchronously would freeze the application and consume many requests. This
service crawls incrementally in a single background worker, persists progress
in SQLite, and serves searches locally. It NEVER indexes observation values
or credentials. No publication claims about an incomplete crawl.

NY Fed's published OpenAPI YAML describes endpoint families, not all distinct
statistical time series. We index the GET endpoints and separately ingest the
primary dealer timeseries metadata (individual keyids). Operations remain
record-level datasets; they are not falsely presented as individual series.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from datetime import datetime, timezone

from financial_intelligence_agent.live.providers import (
    Descriptor, catalog as seed_catalog, dealer_catalog, FRED_PRESETS, _get, NY_BASE,
)

DB_DEFAULT = Path(__file__).resolve().parents[3] / ".cache" / "publisher_catalog.sqlite3"
NY_SPEC_URL = "https://markets.newyorkfed.org/static/docs/markets-api.yml"
FRED_ROOT = "https://api.stlouisfed.org/fred/"
MAX_FRED_REQUESTS = 30000
MAX_FRED_SERIES = 1_000_000
SLEEP_SECONDS = 0.15
_LOCK = threading.RLock()
_WORKERS: dict[str, threading.Thread] = {}
_STOP = threading.Event()


def get_connection(path: str | Path = DB_DEFAULT) -> sqlite3.Connection:
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(str(path),timeout=25,check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=25000")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS entries(
      id TEXT PRIMARY KEY, label TEXT NOT NULL, provider TEXT NOT NULL,
      unit TEXT NOT NULL, frequency TEXT NOT NULL, category TEXT NOT NULL,
      source_url TEXT NOT NULL, convention TEXT NOT NULL,
      kind TEXT NOT NULL, endpoint TEXT NOT NULL DEFAULT '',
      updated_utc TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS entries_provider_idx ON entries(provider);
    CREATE TABLE IF NOT EXISTS crawl_state(name TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS fred_categories(
      category_id INTEGER PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending');
    """)
    return db


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def put_entry(db:sqlite3.Connection,d:Descriptor,*,kind:str="series",endpoint:str="")->None:
    db.execute("""INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(id) DO UPDATE SET label=excluded.label, unit=excluded.unit,
      frequency=excluded.frequency, category=excluded.category,
      source_url=excluded.source_url, convention=excluded.convention,
      kind=excluded.kind, endpoint=excluded.endpoint, updated_utc=excluded.updated_utc""",
       (d.id,d.label,d.provider,d.unit,d.frequency,d.category,
        d.source_url,d.convention,kind,endpoint,_stamp()))


def seed(db:sqlite3.Connection)->None:
    for d in seed_catalog():
        put_entry(db,d)
    db.execute("INSERT OR IGNORE INTO fred_categories(category_id,status) VALUES(0,'pending')")
    # A small documented fallback is immediately searchable even when the
    # NY Fed schema service is temporarily unavailable at app startup.
    base = (
        ("rp/results/search.json","Repo / reverse repo operation results","Repo and reverse repo operations"),
        ("pd/list/timeseries.json","Primary dealer statistical series catalog","Primary Dealer Statistics"),
        ("seclending/all/results/summary/latest.json","Securities lending operations summary","Securities Lending Operations"),
    )
    for endpoint,label,category in base:
        put_entry(db,Descriptor("NY_ENDPOINT:"+endpoint,label,"NY_ENDPOINT",
                  "varies","irregular",category,NY_BASE+"/"+endpoint,
                  "publisher_specification"),kind="endpoint_template",endpoint=endpoint)
    db.commit()


def search(db:sqlite3.Connection,term:str,*,limit:int=100)->list[dict]:
    if not isinstance(term,str) or len(term)>140:
        raise ValueError("Search must be 0..140 characters")
    if type(limit) is not int or not 1<=limit<=250:
        raise ValueError("Search page limit must be 1..250")
    term=term.strip().lower()
    if term:
        # Literal substring matching; no user-supplied SQL grammar or LIKE
        # wildcards. ID-first ordering brings exact series keys to the top.
        rows=db.execute("""SELECT id,label,provider,unit,frequency,category,
                   source_url,convention,kind,endpoint FROM entries
             WHERE instr(lower(id),?)>0 OR instr(lower(label),?)>0
                OR instr(lower(category),?)>0
             ORDER BY CASE WHEN lower(id)=? THEN 0
                  WHEN lower(id) LIKE ? THEN 1 ELSE 2 END, label
             LIMIT ?""",
             (term,term,term,term,term+"%",limit)).fetchall()
    else:
        rows=db.execute("""SELECT id,label,provider,unit,frequency,category,
                   source_url,convention,kind,endpoint FROM entries
             ORDER BY CASE provider WHEN 'NYFED_PD' THEN 1
                       WHEN 'FRED' THEN 0 ELSE 2 END, label LIMIT ?""",(limit,)).fetchall()
    keys=("id","label","provider","unit","frequency","category","source_url","convention","kind","endpoint")
    return [dict(zip(keys,r)) for r in rows]


def status(db:sqlite3.Connection)->dict:
    counts=dict(db.execute("SELECT provider,COUNT(*) FROM entries GROUP BY provider").fetchall())
    state=dict(db.execute("SELECT name,value FROM crawl_state").fetchall())
    progress=dict(db.execute("SELECT status,COUNT(*) FROM fred_categories GROUP BY status").fetchall())
    return {"counts":counts,"fred_categories":progress,"state":state}


def _state(db:sqlite3.Connection,key:str,value:str)->None:
    db.execute("INSERT INTO crawl_state(name,value) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",(key,value))


def _safe_api(url:str,fetch:Callable[...,dict]|None=None)->dict:
    return _get(url,"api.stlouisfed.org",fetch)


def _fred_descriptor(row:Mapping[str,Any])->Descriptor|None:
    ident=row.get("id","")
    if not isinstance(ident,str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,60}",ident):
        return None
    unit=str(row.get("units","source-native"))[:100]
    low=unit.lower()
    if low=="percent":unit="percent"
    elif low in {"billions of dollars","billions of u.s. dollars"}:unit="Billions of USD"
    elif low in {"millions of dollars","millions of u.s. dollars"}:unit="Millions of USD"
    freqs={"D":"daily","W":"weekly","M":"monthly","Q":"quarterly","A":"annual"}
    freq=freqs.get(row.get("frequency_short"),"irregular")
    title=str(row.get("title",ident))[:200]
    return Descriptor("FRED:"+ident,title,"FRED",unit,freq,
                      "FRED category catalog","https://fred.stlouisfed.org/series/"+ident,
                      "FRED_realtime_vintage")


def crawl_fred(db:sqlite3.Connection,api_key:str, *,
              fetch:Callable[...,dict]|None=None,max_requests:int=MAX_FRED_REQUESTS,
              sleep:Callable[[float],Any]=time.sleep)->dict:
    """Resumable exhaustive-by-category FRED traversal, not a keyword sample.

    Completeness is asserted only after all discovered categories and their
    pagination have completed. FRED may list a series in several categories;
    the series ID is the stable deduplication key.
    """
    if not re.fullmatch(r"[a-z0-9]{32}",api_key):
        raise ValueError("Server-side FRED_API_KEY is missing/invalid")
    if type(max_requests) is not int or not 1<=max_requests<=MAX_FRED_REQUESTS:
        raise ValueError("Invalid crawl request budget")
    requests=0
    while requests<max_requests:
        item=db.execute("SELECT category_id FROM fred_categories WHERE status='pending' ORDER BY category_id LIMIT 1").fetchone()
        if item is None:
            _state(db,"fred_complete","true")
            _state(db,"fred_updated",_stamp())
            db.commit()
            break
        cat=int(item[0])
        # In a failed/partial request, category remains pending and no false
        # completed flag is written. Finished pages are upserted idempotently.
        try:
            children=_safe_api(FRED_ROOT+"category/children?"+urlencode({
                "category_id":cat,"api_key":api_key,"file_type":"json"}),fetch)
            requests+=1
            if not isinstance(children.get("categories"),list):
                raise ValueError("Malformed FRED category children")
            for child in children["categories"]:
                child_id=child.get("id")
                if type(child_id) is int and child_id>=0:
                    db.execute("INSERT OR IGNORE INTO fred_categories(category_id,status) VALUES(?,'pending')",(child_id,))
            offset=0
            while requests<max_requests:
                params=urlencode({"category_id":cat,"api_key":api_key,
                                  "file_type":"json","limit":1000,"offset":offset})
                payload=_safe_api(FRED_ROOT+"category/series?"+params,fetch)
                requests+=1
                rows=payload.get("seriess")
                if not isinstance(rows,list):
                    raise ValueError("Malformed FRED category series response")
                for row in rows:
                    descriptor=_fred_descriptor(row)
                    if descriptor:
                        put_entry(db,descriptor)
                count=int(payload.get("count",-1))
                if count<0:raise ValueError("Missing FRED pagination count")
                offset+=len(rows)
                db.commit()
                if offset>=count:break
                if not rows:raise ValueError("FRED pagination made no progress")
                if sleep:sleep(SLEEP_SECONDS)
            if offset>=count:
                db.execute("UPDATE fred_categories SET status='done' WHERE category_id=?",(cat,))
                db.commit()
            if sleep:sleep(SLEEP_SECONDS)
        except Exception:
            _state(db,"fred_error","A category request failed; crawl can resume after restart")
            _state(db,"fred_updated",_stamp())
            db.commit()
            break
        if db.execute("SELECT COUNT(*) FROM entries WHERE provider='FRED'").fetchone()[0]>MAX_FRED_SERIES:
            _state(db,"fred_error","Catalog exceeded configured maximum series count")
            db.commit()
            break
    _state(db,"fred_updated",_stamp())
    db.commit()
    return status(db)


def _get_swagger(url:str=NY_SPEC_URL)->dict:
    # URL is fixed by this module; no user-supplied address.
    import yaml
    req=Request(url,headers={"User-Agent":"liquidity-intelligence/0.4"})
    with urlopen(req,timeout=20) as response:
        raw=response.read(5_000_001)
    if len(raw)>5_000_000:raise ValueError("NY Fed spec exceeds 5 MB limit")
    result=yaml.safe_load(raw)
    if not isinstance(result,dict) or not isinstance(result.get("paths"),dict):
        raise ValueError("Invalid NY Fed API specification")
    return result


def ny_spec_entries(spec:Mapping[str,Any])->list[tuple[Descriptor,str]]:
    """Index all published GET paths as endpoint templates, not fake series."""
    paths=spec.get("paths",{})
    output=[]
    if not isinstance(paths,dict):raise ValueError("Swagger paths missing")
    for path,methods in paths.items():
        if not isinstance(path,str) or not isinstance(methods,dict) or "get" not in methods:continue
        operation=methods["get"]
        if not isinstance(operation,dict):continue
        label=str(operation.get("summary") or operation.get("operationId") or path)[:180]
        # Retain templates in metadata but do not generate network requests
        # from unresolved {parameter} segments.
        endpoint=path.removeprefix("/api/").lstrip("/")
        # Swagger may express a format placeholder instead of a literal
        # extension; index the documented JSON representation when available.
        for suffix in (".{fileType}",".{format}",".{file_type}",".{fType}"):
            if endpoint.endswith(suffix):
                endpoint=endpoint[:-len(suffix)]+".json"
        if not endpoint.startswith(("rp/","pd/","rates/","seclending/","soma/","tsy/","ambs/","cb/","cbliquidity/")):
            continue
        if not endpoint.endswith(".json"):continue
        tag=(operation.get("tags") or ["New York Fed Markets"])[0]
        ident="NY_ENDPOINT:"+endpoint
        desc=Descriptor(ident,label,"NY_ENDPOINT","varies","irregular",
                        str(tag),NY_BASE+"/"+endpoint,"publisher_specification")
        output.append((desc,endpoint))
    return output


def warm_ny(db:sqlite3.Connection,*,dealers:Callable[[],list[Descriptor]]=dealer_catalog,
            spec_loader:Callable[[],dict]=_get_swagger)->dict:
    try:
        for descriptor in dealers():
            put_entry(db,descriptor,kind="series",endpoint=descriptor.source_url)
        _state(db,"ny_dealers_updated",_stamp())
        db.commit()
    except Exception:
        _state(db,"ny_dealers_error","Dealer metadata unavailable; using existing cached catalog")
        db.commit()
    try:
        for descriptor,path in ny_spec_entries(spec_loader()):
            put_entry(db,descriptor,kind="endpoint_template",endpoint=path)
        _state(db,"ny_spec_updated",_stamp())
        db.commit()
    except Exception:
        _state(db,"ny_spec_error","Publisher specification unavailable; using cached endpoints")
        db.commit()
    return status(db)


def bootstrap(path:str|Path=DB_DEFAULT, *,enable_network:bool=True)->dict:
    """Seed synchronously, start metadata warming once per process.

    Does not block browser first paint. The working index is available
    immediately and becomes richer as startup crawlers finish.
    """
    path=Path(path).resolve()
    with _LOCK:
        db=get_connection(path)
        try: seed(db)
        finally:db.close()
        if enable_network and str(path) not in _WORKERS:
            def work():
                c=get_connection(path)
                try:
                    warm_ny(c)
                    key=os.getenv("FRED_API_KEY","")
                    if key:
                        crawl_fred(c,key)
                    else:
                        _state(c,"fred_error","FRED_API_KEY not configured; FRED full crawl not started")
                        c.commit()
                finally:c.close()
            thread=threading.Thread(target=work,name="publisher-metadata-warm",daemon=True)
            _WORKERS[str(path)]=thread
            thread.start()
        return {"db_path":str(path),"worker_running":_WORKERS.get(str(path),None) is not None}


def lookup(db:sqlite3.Connection,key:str)->dict|None:
    rows=db.execute("""SELECT id,label,provider,unit,frequency,category,source_url,convention,kind,endpoint
                       FROM entries WHERE id=?""",(key,)).fetchall()
    cols=("id","label","provider","unit","frequency","category","source_url","convention","kind","endpoint")
    return dict(zip(cols,rows[0])) if rows else None
