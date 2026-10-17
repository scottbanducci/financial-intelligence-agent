"""Source adapters that normalize heterogeneous datasets to one Series contract.

No provider-specific behavior leaks into the mathematical operator registry.
Network fetching is intentionally separate from parsing and disabled by default.
For FFIEC, this reads *already downloaded* tab-delimited public Call Reports:
the government bulk download workflow is not an unauthenticated JSON API.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from financial_intelligence_agent.engine.operators import Point, Series, day, dec, MAX_POINTS

FIELD = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")


class Adapter(Protocol):
    def parse(self, payload: Any) -> Series: ...


def _field(value: str) -> str:
    if not isinstance(value,str) or not FIELD.fullmatch(value):
        raise ValueError("Fields must be simple allowlisted identifiers")
    return value


def _extract(rows: Iterable[Mapping[str,Any]], *, key: str, source: str, unit: str, frequency: str,
             date_field: str, value_field: str, available_field: str | None = None,
             revision_field: str | None = None, convention: str = "observation_date") -> Series:
    fields = (date_field,value_field) + ((available_field,) if available_field else ()) + ((revision_field,) if revision_field else ())
    for field in fields: _field(field)
    points = []
    for row in rows:
        if len(points) == MAX_POINTS:
            raise ValueError("Exceeds maximum batch size")
        if date_field not in row or value_field not in row:
            raise ValueError("Source record missing mapped date/value")
        raw = row.get(available_field) if available_field else None
        revision = row.get(revision_field,0) if revision_field else 0
        points.append(Point(day(row[date_field]),dec(row[value_field]),day(raw) if raw else None,int(revision)))
    return Series(key,source,unit,frequency,tuple(points),convention)


class CsvSeriesAdapter:
    def __init__(self, *, key: str, source: str, unit: str, frequency: str,
                 date_field: str, value_field: str, available_field: str | None = None,
                 delimiter: str = ","):
        if delimiter not in {",","\t",";"}:
            raise ValueError("Unsupported delimiter")
        self.mapping = dict(key=key,source=source,unit=unit,frequency=frequency,
                            date_field=date_field,value_field=value_field,available_field=available_field)
        self.delimiter = delimiter

    def parse(self,payload: str | bytes) -> Series:
        text = payload.decode("utf-8-sig") if isinstance(payload,bytes) else payload
        reader = csv.DictReader(io.StringIO(text,newline=""), delimiter=self.delimiter)
        if reader.fieldnames is None or len(reader.fieldnames)!=len(set(reader.fieldnames)):
            raise ValueError("Missing or duplicated CSV header")
        rows = list(reader)
        if any(None in row for row in rows):
            raise ValueError("Malformed delimited record")
        return _extract(rows,**self.mapping)


class FredAdapter:
    """FRED /fred/series/observations JSON. ALFRED real-time ranges retained.

    realtime_start is a date of vintage validity, not a precise release timestamp.
    The API must be queried with an intentional realtime period or vintage_dates;
    blindly using current-vintage responses is not an historical reconstruction.
    """
    def __init__(self, series_id: str, unit: str, frequency: str):
        self.series_id=series_id; self.unit=unit; self.frequency=frequency

    def parse(self,payload: Mapping[str,Any]) -> Series:
        rows=payload.get("observations")
        if not isinstance(rows,list):
            raise ValueError("FRED JSON missing observations")
        return _extract(rows,key=self.series_id,source="FRED",unit=self.unit,
                        frequency=self.frequency,date_field="date",value_field="value",
                        available_field="realtime_start",convention="FRED_realtime_vintage")

    def url(self, api_key: str, *, realtime_start: str, realtime_end: str) -> str:
        if not api_key or len(api_key)>128:
            raise ValueError("FRED API key required")
        start,end=day(realtime_start),day(realtime_end)
        if start>end:
            raise ValueError("Invalid FRED vintage interval")
        return "https://api.stlouisfed.org/fred/series/observations?"+urlencode({
            "series_id":self.series_id,"api_key":api_key,"file_type":"json",
            "realtime_start":start.isoformat(),"realtime_end":end.isoformat(),
            "limit":10000,
        })


class NYFedAdapter:
    """Map a documented NY Fed JSON response with explicit record path/columns.

    NY Fed endpoint schemas differ: no field-name assumptions or URL scraping.
    """
    def __init__(self, *, key: str, unit: str, frequency: str,
                 records_path: tuple[str,...], date_field: str, value_field: str,
                 available_field: str | None = None):
        if not records_path or len(records_path)>6:
            raise ValueError("Record path required")
        for part in records_path: _field(part)
        self.path=records_path
        self.mapping=dict(key=key,source="NYFED",unit=unit,frequency=frequency,
                          date_field=date_field,value_field=value_field,available_field=available_field)

    def parse(self,payload: Mapping[str,Any]) -> Series:
        block=payload
        for part in self.path:
            if not isinstance(block,dict) or part not in block:
                raise ValueError("NY Fed JSON shape does not match mapping")
            block=block[part]
        if not isinstance(block,list) or any(not isinstance(x,dict) for x in block):
            raise ValueError("Expected mapped NY Fed records list")
        return _extract(block,**self.mapping)


class FFIECAdapter:
    """Parse one institution/line item from supplied FFIEC bulk tabular download.

    Report field codes and reporting period must be specified from the actual
    FFIEC file metadata; bank-level values are never silently aggregated.
    """
    def __init__(self, *, field: str, institution_field: str, institution_id: str,
                 date_field: str, unit: str, frequency: str = "quarterly"):
        self.field=_field(field)
        self.institution_field=_field(institution_field)
        self.institution_id=str(institution_id)
        self.date_field=_field(date_field)
        self.unit=unit; self.frequency=frequency

    def parse(self,payload: str | bytes) -> Series:
        text=payload.decode("utf-8-sig") if isinstance(payload,bytes) else payload
        reader=csv.DictReader(io.StringIO(text,newline=""),delimiter="\t")
        if not reader.fieldnames or len(set(reader.fieldnames))!=len(reader.fieldnames):
            raise ValueError("Invalid FFIEC bulk header")
        required={self.field,self.date_field,self.institution_field}
        if required-set(reader.fieldnames):
            raise ValueError("FFIEC column mapping not present")
        rows=[row for row in reader if row[self.institution_field]==self.institution_id]
        if not rows:
            raise ValueError("No rows for specified institution")
        return _extract(rows,key="FFIEC."+self.field,source="FFIEC",unit=self.unit,
                        frequency=self.frequency,date_field=self.date_field,
                        value_field=self.field,convention="report_period_end")


def fetch_json(url: str, *, allowed_hostname: str, timeout_seconds: float = 10.0,
               max_bytes: int = 5_000_000) -> dict[str,Any]:
    """Optional outbound fetch with strict HTTPS host and bounded response."""
    from urllib.parse import urlsplit
    parsed=urlsplit(url)
    if parsed.scheme!="https" or parsed.hostname!=allowed_hostname or parsed.username or parsed.password or parsed.port:
        raise ValueError("Blocked source URL")
    if not 0 < timeout_seconds <= 30 or not 0 < max_bytes <= 5_000_000:
        raise ValueError("Invalid fetch limits")
    req=Request(url,headers={"User-Agent":"financial-intelligence-agent/0.2","Accept":"application/json"})
    # Reject all redirects, which could otherwise evade initial host checking.
    from urllib.request import build_opener, HTTPRedirectHandler
    class RejectRedirect(HTTPRedirectHandler):
        def redirect_request(self,req,fp,code,msg,headers,newurl):
            raise ValueError("Cross-origin or unexpected redirect denied")
    opener=build_opener(RejectRedirect)
    with opener.open(req,timeout=timeout_seconds) as resp:
        raw=resp.read(max_bytes+1)
    if len(raw)>max_bytes:
        raise ValueError("Response size limit exceeded")
    result=json.loads(raw)
    if not isinstance(result,dict):
        raise ValueError("Expected JSON object")
    return result
