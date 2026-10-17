"""Non-mutating external API contract probe (no FRED key required).

For human review/CI advisory health checks. Failures do not silently
switch to fixtures. The output reports only status and response *shape*,
never user credentials or detailed HTTP URLs.
"""
import json
import sys
from datetime import date,timedelta
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from financial_intelligence_agent.live.providers import ny_rate,dealer_catalog,dealer
from financial_intelligence_agent.live.fdic import bank_financial
from financial_intelligence_agent.engine.adapters import fetch_json

def check(name,fn):
    try:
        result=fn()
        return {"name":name,"ok":True,"details":result}
    except Exception as exc:
        return {"name":name,"ok":False,"error_type":type(exc).__name__,
                "message":str(exc)[:240]}

def main():
    today=date.today()
    start=(today-timedelta(days=14)).isoformat()
    end=today.isoformat()
    checks=[
        check("NY Fed SOFR live observations",lambda:
            {"count":len(ny_rate("SOFR",start,end).series.points)}),
        check("NY Fed dealer metadata",lambda:
            {"count":len(dealer_catalog())}),
        check("NY Fed dealer JSON envelope",lambda: raw_dealer_shape()),
        check("NY Fed repo operations JSON explorer",lambda: probe_repo_shape()),
        check("NY Fed real dealer time series",lambda:{"count":len(dealer("PDPOSGST-TOT",
                    (today-timedelta(days=365)).isoformat(),end).series.points)}),
        check("FDIC bank financials",lambda:
            {"count":len(bank_financial(14,"ASSET",
                            (today-timedelta(days=550)).isoformat(),end).series.points)}),
    ]
    print(json.dumps({"probes":checks},indent=2))
    return 0 if all(x["ok"] for x in checks) else 1

def raw_dealer_shape():
    from financial_intelligence_agent.live.providers import NY_BASE
    value=fetch_json(NY_BASE+"/pd/get/PDPOSGST-TOT.json",
                     allowed_hostname="markets.newyorkfed.org",
                     timeout_seconds=15,max_bytes=5_000_000)
    pd=value.get("pd",{})
    return {
        "outer_keys":list(value)[:15],
        "pd_keys":list(pd)[:20] if isinstance(pd,dict) else [],
        "nested_type":{k:type(v).__name__ for k,v in pd.items()} if isinstance(pd,dict) else {},
        "sample_row_keys":{k:list(v[0])[:15] for k,v in pd.items()
             if isinstance(v,list) and v and isinstance(v[0],dict)} if isinstance(pd,dict) else {},
    }

if __name__=="__main__":
    raise SystemExit(main())


def probe_repo_shape():
    from financial_intelligence_agent.live.nyfed_explorer import discover,records_from_path
    from datetime import date,timedelta
    today=date.today()
    sample=discover("rp/results/search.json",
                    start=(today-timedelta(days=30)).isoformat(),
                    end=today.isoformat())
    details={}
    for path in sample.candidate_paths[:6]:
        rows=records_from_path(sample,path)
        details[path]={"count":len(rows),"fields":list(rows[0])[:18] if rows else []}
    return details
