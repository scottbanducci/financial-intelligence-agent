"""Searchable publisher *datasets*, warmed at startup and persisted in SQLite.

FRED is enumerated by release -> release/series (NOT categories), with page
checkpoints and series/release memberships. NY Fed exposes curated dataset
contracts, plus dynamically published primary-dealer keyids. No NY_ENDPOINT
templates are presented as if they were datasets.

A cold full FRED crawl takes many HTTP calls and runs in a daemon thread;
never claim completeness until every release and its pages are checked.
"""
from __future__ import annotations
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlencode
from typing import Any,Callable,Mapping

from financial_intelligence_agent.live.providers import (
    Descriptor,catalog as seed_catalog,dealer_catalog,_get,
)
from financial_intelligence_agent.live.nyfed_datasets import dataset_descriptors

DB_DEFAULT=Path(__file__).resolve().parents[3]/".cache"/"publisher_catalog.sqlite3"
FRED_ROOT="https://api.stlouisfed.org/fred/"
MAX_FRED_REQUESTS=30000
MAX_FRED_SERIES=1_000_000
SLEEP_SECONDS=0.15
_LOCK=threading.RLock()
_WORKERS:dict[str,threading.Thread]={}


def get_connection(path:str|Path=DB_DEFAULT)->sqlite3.Connection:
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(str(path),timeout=30,check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS entries (
      id TEXT PRIMARY KEY,label TEXT NOT NULL,provider TEXT NOT NULL,
      unit TEXT NOT NULL,frequency TEXT NOT NULL,category TEXT NOT NULL,
      source_url TEXT NOT NULL,convention TEXT NOT NULL,
      kind TEXT NOT NULL,endpoint TEXT NOT NULL DEFAULT '',
      updated_utc TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS entries_provider_idx ON entries(provider);
    CREATE TABLE IF NOT EXISTS crawl_state(name TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS fred_releases (
      release_id INTEGER PRIMARY KEY,name TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending',
      next_offset INTEGER NOT NULL DEFAULT 0,
      expected_count INTEGER NOT NULL DEFAULT -1);
    CREATE TABLE IF NOT EXISTS fred_release_members (
      release_id INTEGER NOT NULL,
      series_id TEXT NOT NULL,
      PRIMARY KEY(release_id,series_id));
    CREATE INDEX IF NOT EXISTS fred_release_members_sid
      ON fred_release_members(series_id);
    """)
    return db


def _stamp()->str:return datetime.now(timezone.utc).isoformat()


def put_entry(db:sqlite3.Connection,d:Descriptor,*,kind:str="series",endpoint:str="")->None:
    db.execute("""INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(id) DO UPDATE SET label=excluded.label,provider=excluded.provider,
      unit=excluded.unit,frequency=excluded.frequency,category=excluded.category,
      source_url=excluded.source_url,convention=excluded.convention,
      kind=excluded.kind,endpoint=excluded.endpoint,updated_utc=excluded.updated_utc""",
       (d.id,d.label,d.provider,d.unit,d.frequency,d.category,
        d.source_url,d.convention,kind,endpoint,_stamp()))


def seed(db:sqlite3.Connection)->None:
    # Migrate the pre-existing endpoint-driven index in place.
    db.execute("DELETE FROM entries WHERE provider IN ('NY_ENDPOINT','NYFED')")
    for d in seed_catalog():
        # Curated fallback rows should never overwrite fresher metadata
        # previously fetched from the official release/series endpoint.
        if d.provider=="FRED" and db.execute(
            "SELECT 1 FROM entries WHERE id=?",(d.id,)).fetchone() is None:
            put_entry(db,d)
    for d in dataset_descriptors():
        put_entry(db,d,kind="dataset",endpoint=d.source_url)
    db.commit()


def status(db:sqlite3.Connection)->dict:
    counts=dict(db.execute("SELECT provider,COUNT(*) FROM entries GROUP BY provider"))
    states=dict(db.execute("SELECT name,value FROM crawl_state"))
    progress=dict(db.execute("SELECT status,COUNT(*) FROM fred_releases GROUP BY status"))
    return {"counts":counts,"fred_releases":progress,"state":states}


def _state(db:sqlite3.Connection,key:str,val:str)->None:
    db.execute("""INSERT INTO crawl_state VALUES(?,?)
    ON CONFLICT(name) DO UPDATE SET value=excluded.value""",(key,val))


def _fred_descriptor(item:Mapping[str,Any])->Descriptor|None:
    sid=item.get("id","")
    if not isinstance(sid,str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,60}",sid):return None
    unit=str(item.get("units","source-native"))[:100]
    if unit.lower() in ("billions of dollars","billions of u.s. dollars"):unit="Billions of USD"
    elif unit.lower() in ("millions of dollars","millions of u.s. dollars"):unit="Millions of USD"
    elif unit.lower()=="percent":unit="percent"
    frequency={"D":"daily","W":"weekly","M":"monthly","Q":"quarterly","A":"annual"}.get(
        item.get("frequency_short"),"irregular")
    return Descriptor("FRED:"+sid,str(item.get("title",sid))[:200],
                      "FRED",unit,frequency,"FRED release series",
                      "https://fred.stlouisfed.org/series/"+sid,"FRED_realtime_vintage")


def _api(url:str,fetch:Callable[...,dict]|None)->dict:
    return _get(url,"api.stlouisfed.org",fetch)


def _get_page(api_path:str,params:dict,fetch:Callable[...,dict]|None)->dict:
    return _api(FRED_ROOT+api_path+"?"+urlencode(params),fetch)


def crawl_fred(db:sqlite3.Connection,api_key:str, *,
              fetch:Callable[...,dict]|None=None,max_requests:int=MAX_FRED_REQUESTS,
              sleep:Callable[[float],Any]=time.sleep)->dict:
    """Fetch ALL release metadata, then every release's paginated series.

    Offset is checkpointed after each successful page. The same series may
    belong to multiple releases; record every membership, deduplicate series.
    A failed request retains the pending release and never marks completion.
    """
    if not re.fullmatch(r"[a-z0-9]{32}",api_key):
        raise ValueError("FRED_API_KEY must be a server-side 32-character key")
    if type(max_requests) is not int or not 1<=max_requests<=MAX_FRED_REQUESTS:
        raise ValueError("Invalid crawl request limit")
    calls=0
    common={"api_key":api_key,"file_type":"json","limit":1000}
    if dict(db.execute("SELECT name,value FROM crawl_state")).get("fred_releases_loaded")!="true":
        try:
            offset=0
            while calls<max_requests:
                payload=_get_page("releases",{**common,"offset":offset},fetch)
                calls+=1
                records=payload.get("releases")
                if not isinstance(records,list):raise ValueError("Malformed FRED releases response")
                count=int(payload.get("count",-1))
                if count<0:raise ValueError("Missing release count")
                for release in records:
                    rid=release.get("id")
                    if type(rid) is int and rid>=0:
                        db.execute("""INSERT INTO fred_releases(release_id,name)
                                      VALUES(?,?) ON CONFLICT(release_id)
                                      DO UPDATE SET name=excluded.name""",
                                   (rid,str(release.get("name",rid))[:200]))
                offset+=len(records)
                db.commit()
                if offset>=count:
                    _state(db,"fred_releases_loaded","true")
                    db.commit()
                    break
                if not records:raise ValueError("Releases pagination stalled")
                if sleep:sleep(SLEEP_SECONDS)
        except Exception:
            _state(db,"fred_error","Release enumeration interrupted; retry on next app restart")
            db.commit()
            return status(db)
    if dict(db.execute("SELECT name,value FROM crawl_state")).get("fred_releases_loaded")!="true":
        return status(db)
    while calls<max_requests:
        entry=db.execute("""SELECT release_id,next_offset
          FROM fred_releases WHERE status='pending' ORDER BY release_id LIMIT 1""").fetchone()
        if entry is None:
            _state(db,"fred_complete","true")
            _state(db,"fred_updated",_stamp())
            _state(db,"fred_error","")
            db.commit()
            break
        rid,offset=entry
        try:
            response=_get_page("release/series",{
                **common,"release_id":rid,"offset":offset},fetch)
            calls+=1
            records=response.get("seriess")
            if not isinstance(records,list):raise ValueError("Malformed release/series response")
            count=int(response.get("count",-1))
            if count<0:raise ValueError("Missing release/series count")
            for item in records:
                descriptor=_fred_descriptor(item)
                if descriptor is None:continue
                put_entry(db,descriptor)
                db.execute("""INSERT OR IGNORE INTO fred_release_members
                               VALUES (?,?)""",(rid,descriptor.id))
            new_offset=offset+len(records)
            if new_offset>=count:
                db.execute("""UPDATE fred_releases SET status='done',
                              next_offset=?,expected_count=? WHERE release_id=?""",
                           (new_offset,count,rid))
            else:
                if not records:raise ValueError("Release page made no progress")
                db.execute("""UPDATE fred_releases SET next_offset=?,expected_count=?
                              WHERE release_id=?""",(new_offset,count,rid))
            db.commit()
            if db.execute("SELECT COUNT(*) FROM entries WHERE provider='FRED'").fetchone()[0]>MAX_FRED_SERIES:
                raise ValueError("FRED index row limit reached")
        except Exception:
            _state(db,"fred_error","Release series ingestion interrupted; retry on next app restart")
            db.commit()
            break
        if sleep:sleep(SLEEP_SECONDS)
    _state(db,"fred_updated",_stamp())
    db.commit()
    return status(db)


def search(db:sqlite3.Connection,term:str,*,provider:str|None=None,
           release_id:int|None=None,limit:int=150)->list[dict]:
    """Local provider-scoped, release-aware search of named datasets."""
    if not isinstance(term,str) or len(term)>140:raise ValueError("Search exceeds 140 characters")
    if provider is not None and provider not in {"FRED","NYFED_DATASET","NYFED_PD"}:
        raise ValueError("Unknown search provider")
    if type(limit) is not int or not 1<=limit<=250:raise ValueError("Limit must be 1..250")
    words=term.casefold().strip().split()
    sql="""SELECT DISTINCT e.id,e.label,e.provider,e.unit,e.frequency,e.category,
                   e.source_url,e.convention,e.kind,e.endpoint FROM entries e
                   LEFT JOIN fred_release_members m ON m.series_id=e.id
                   LEFT JOIN fred_releases r ON r.release_id=m.release_id
                   WHERE 1=1"""
    args=[]
    if provider is not None:
        sql+=" AND e.provider=?";args.append(provider)
    if release_id is not None:
        sql+=" AND m.release_id=?";args.append(release_id)
    for word in words:
        sql+=" AND (instr(lower(e.id),?)>0 OR instr(lower(e.label),?)>0 OR instr(lower(e.category),?)>0 OR instr(lower(coalesce(r.name,'')),?)>0)"
        args.extend([word]*4)
    sql+=" ORDER BY e.label LIMIT ?"
    args.append(limit)
    result=db.execute(sql,args).fetchall()
    keys=("id","label","provider","unit","frequency","category","source_url","convention","kind","endpoint")
    return [dict(zip(keys,row)) for row in result]


def release_list(db:sqlite3.Connection,*,term:str="")->list[dict]:
    rows=db.execute("""SELECT r.release_id,r.name,r.status,r.next_offset,
      COUNT(m.series_id) AS member_count FROM fred_releases r
      LEFT JOIN fred_release_members m ON m.release_id=r.release_id
      WHERE instr(lower(r.name),?)>0 GROUP BY r.release_id ORDER BY r.name""",
      (term.casefold(),)).fetchall()
    return [{"id":a,"name":b,"status":c,"offset":d,"series":e} for a,b,c,d,e in rows]


def warm_ny(db:sqlite3.Connection, *,
            dealers:Callable[[],list[Descriptor]]=dealer_catalog)->dict:
    try:
        found=dealers()
        if not found:raise ValueError("No dealer metadata returned")
        for descriptor in found:
            put_entry(db,descriptor,kind="series",endpoint=descriptor.source_url)
        _state(db,"ny_dealers_updated",_stamp())
        _state(db,"ny_dealers_error","")
        db.commit()
    except Exception:
        _state(db,"ny_dealers_error","Dealer metadata fetch unavailable; cached series retained")
        db.commit()
    return status(db)


def bootstrap(path:str|Path=DB_DEFAULT,*,enable_network:bool=True)->dict:
    path=Path(path).resolve()
    with _LOCK:
        db=get_connection(path)
        try:
            seed(db)
            states=dict(db.execute("SELECT name,value FROM crawl_state"))
            if states.get("fred_complete")=="true" and states.get("fred_updated"):
                try:
                    stale=(datetime.now(timezone.utc)-datetime.fromisoformat(states["fred_updated"])).total_seconds()>7*86400
                except ValueError:stale=True
                if stale:
                    db.execute("UPDATE fred_releases SET status='pending',next_offset=0,expected_count=-1")
                    db.execute("DELETE FROM crawl_state WHERE name IN ('fred_complete','fred_releases_loaded')")
                    db.commit()
        finally:db.close()
        existing=_WORKERS.get(str(path))
        if enable_network and (existing is None or not existing.is_alive()):
            def worker():
                c=get_connection(path)
                try:
                    warm_ny(c)
                    if os.getenv("FRED_API_KEY"):
                        crawl_fred(c,os.environ["FRED_API_KEY"])
                    else:
                        _state(c,"fred_error","FRED_API_KEY not set; release crawl not started")
                        c.commit()
                finally:c.close()
            task=threading.Thread(target=worker,name="publisher-release-metadata",daemon=True)
            _WORKERS[str(path)]=task
            task.start()
        return {"db_path":str(path),"worker_running":bool(enable_network)}


def lookup(db:sqlite3.Connection,key:str)->dict|None:
    rows=search(db,"",limit=250)
    r=db.execute("""SELECT id,label,provider,unit,frequency,category,source_url,
                    convention,kind,endpoint FROM entries WHERE id=?""",(key,)).fetchone()
    if not r:return None
    return dict(zip(("id","label","provider","unit","frequency","category",
                     "source_url","convention","kind","endpoint"),r))
