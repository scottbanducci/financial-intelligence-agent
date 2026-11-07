"""Read-only Databricks Gold analytical series adapter.

Does not accept SQL from an LLM/browser. Catalog is configured by operator,
table/column come from a checked allowlist, and dates are parameterized.
A SQL warehouse must be explicitly configured and has potential compute cost.
"""
from __future__ import annotations
import os
import re
from dataclasses import dataclass
from datetime import date,datetime,timezone
from typing import Any,Callable

from financial_intelligence_agent.engine.operators import Series,Point,dec,day
from financial_intelligence_agent.live.providers import Descriptor,FetchResult,validate_dates

CATALOG_ID=re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
GOLD_FIELDS={
 "fed_balance_sheet_weekly":{
  "total_assets":"Millions of USD","treasuries_held":"Millions of USD",
  "mbs_held":"Millions of USD","reserve_balances":"Millions of USD",
  "tga_balance":"Millions of USD","on_rrp":"Millions of USD"},
 "bank_balance_sheet_weekly":{
  "bank_credit":"Billions of USD","ci_loans":"Billions of USD",
  "deposits":"Billions of USD","real_estate_loans":"Billions of USD",
  "consumer_loans":"Billions of USD","cash_assets":"Billions of USD",
  "nondep_fi_loans":"Billions of USD","all_other_loans":"Billions of USD"},
 "rates_daily":{
  "sofr":"percent","effr":"percent","iorb":"percent","dtb3":"percent",
  "sofr_iorb_spread_bps":"basis_points","sofr_effr_spread_bps":"basis_points",
  "effr_iorb_spread_bps":"basis_points","dtb3_effr_spread_bps":"basis_points"},
}
TABLE_DESCRIPTION={
 "fed_balance_sheet_weekly":"Federal Reserve H.4.1",
 "bank_balance_sheet_weekly":"Commercial-bank H.8",
 "rates_daily":"Overnight funding rates"
}
MAX_ROWS=10000

def descriptors(catalog:str)->list[Descriptor]:
    if not CATALOG_ID.fullmatch(catalog):
        raise ValueError("Invalid configured Unity Catalog identifier")
    result=[]
    for table,fields in GOLD_FIELDS.items():
        for field,unit in fields.items():
            result.append(Descriptor("GOLD:"+table+":"+field,field.replace("_"," ").title(),
                "DATABRICKS",unit,"daily" if table=="rates_daily" else "weekly",
                TABLE_DESCRIPTION[table],f"{catalog}.gold.{table}",
                "weekly_average" if field=="tga_balance" else "observation_date"))
    return result

def _connect():
    from databricks import sql
    from databricks.sdk.core import Config
    path=os.getenv("DATABRICKS_HTTP_PATH","")
    if not path.startswith("/sql/") or len(path)>260:
        raise ValueError("Configure DATABRICKS_HTTP_PATH for a SQL warehouse")
    cfg=Config()
    hostname=cfg.host.removeprefix("https://").removeprefix("http://")
    return sql.connect(server_hostname=hostname,http_path=path,
                       credentials_provider=lambda: cfg.authenticate,
                       _use_arrow_native_complex_types=False)

def read_gold(table:str,field:str,start:str,end:str,*,catalog:str|None=None,
              connect:Callable[[],Any]|None=None)->FetchResult:
    if table not in GOLD_FIELDS or field not in GOLD_FIELDS[table]:
        raise ValueError("Gold table/metric not in read-only allowlist")
    catalog=catalog or os.getenv("DATABRICKS_CATALOG","fed_monitor")
    if not CATALOG_ID.fullmatch(catalog):
        raise ValueError("Invalid configured catalog")
    a,b=validate_dates(start,end)
    # All interpolated identifiers have now been checked against fixed
    # trusted maps and a conservative identifier pattern.
    statement=(f"SELECT observation_date, {field} FROM {catalog}.gold.{table} "
               f"WHERE observation_date BETWEEN ? AND ? "
               f"ORDER BY observation_date ASC LIMIT {MAX_ROWS+1}")
    conn=None
    try:
        conn=(connect or _connect)()
        with conn.cursor() as cursor:
            cursor.execute(statement,(a.isoformat(),b.isoformat()))
            records=cursor.fetchall()
    except Exception:
        # Prevent SQL secrets, warehouse identifiers, credentials or raw SQL
        # error details from being reflected to a public app.
        raise ValueError("Databricks read failed; check app permissions and warehouse status") from None
    finally:
        if conn is not None:
            try:conn.close()
            except Exception:pass
    if len(records)>MAX_ROWS:
        raise ValueError("Gold table exceeds 10,000 rows; shorten requested range")
    pts=[]
    for row in records:
        raw_date,raw_value=row[0],row[1]
        observed=raw_date.date() if isinstance(raw_date,datetime) else day(raw_date)
        if not isinstance(observed,date):raise ValueError("Malformed Gold observation date")
        value=dec(raw_value)
        if value is not None:pts.append(Point(observed,value))
    if not pts:
        raise ValueError("Gold table has no values in selected date interval")
    unit=GOLD_FIELDS[table][field]
    convention="weekly_average" if field=="tga_balance" else "observation_date"
    series=Series(f"GOLD_{table}_{field}","DATABRICKS",unit,
                  "daily" if table=="rates_daily" else "weekly",tuple(pts),convention)
    desc=next(x for x in descriptors(catalog) if x.id=="GOLD:"+table+":"+field)
    return FetchResult(series,desc,datetime.now(timezone.utc).isoformat(),
                       series.fingerprint(),start,end,
                       "Gold observations are the latest available persisted values; "
                       "Delta table versions, original publication dates and source vintages "
                       "are not represented in this read-only adapter")
