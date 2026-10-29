"""Advisory live NY Fed dataset contract inspection.

Outputs only public schema names and record counts, not credentials or massive
response bodies. Not a required CI gate because NY Fed may be unavailable.
"""
import json,sys
from pathlib import Path
from datetime import date,timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from financial_intelligence_agent.live.nyfed_datasets import DATASETS,_rows,request_url,fetch_dataset
from financial_intelligence_agent.live.providers import _get,NY_BASE

def probe():
    today=date.today()
    start=(today-timedelta(days=180)).isoformat();end=today.isoformat()
    output=[]
    for definition in DATASETS:
        entry={"id":definition.id,"endpoint":definition.endpoint}
        try:
            payload=_get(request_url(definition,start,end),"markets.newyorkfed.org",None)
            entry["top_keys"]=list(payload)[:8]
            try:
                path,records=_rows(payload,definition.mode)
                entry["record_path"]=path
                entry["records"]=len(records)
                entry["sample_keys"]=list(records[0])[:40] if records else []
            except Exception as exc:
                entry["schema_error"]=str(exc)[:150]
            try:
                report=fetch_dataset(definition.id,start,end)
                entry["series"]={k:len(v.points) for k,v in report.series.items()}
                entry["unit"]=report.units
            except Exception as exc:
                entry["mapping_error"]=str(exc)[:180]
        except Exception as exc:entry["fetch_error"]=type(exc).__name__+":"+str(exc)[:130]
        output.append(entry)
    print(json.dumps(output,indent=2,ensure_ascii=True))
if __name__=="__main__":probe()
