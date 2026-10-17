import sys,tempfile,unittest,hashlib
from pathlib import Path
from datetime import datetime,timezone,date
from decimal import Decimal
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from financial_intelligence_agent.engine.operators import Series,Point
from financial_intelligence_agent.live.providers import FetchResult,Descriptor
from financial_intelligence_agent.live.refresh import publish,read_published

CLOCK=lambda:datetime(2026,10,16,20,0,tzinfo=timezone.utc)
def fetch(key,start,end):
    s=Series(key.split(":")[1],"NYFED","percent","daily",
             (Point(date(2026,10,15),Decimal("3.88")),))
    d=Descriptor(key,key,"NYFED","percent","daily","rates",
                 "https://markets.newyorkfed.org/api/rates/all/latest.json")
    return FetchResult(s,d,CLOCK().isoformat(),s.fingerprint(),start,end)
class Refresh(unittest.TestCase):
    def test_publish_and_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=publish(tmp,["NYFED:SOFR","NYFED:EFFR"],"2026-10-01","2026-10-16",
                        fetch=fetch,clock=CLOCK)
            self.assertEqual(len(out["manifest"]["assets"]),2)
            manifest,data=read_published(tmp)
            self.assertEqual(data["NYFED:SOFR"]["points"][0]["value"],"3.88")
            self.assertNotIn("api_key",str(data))
    def test_failed_second_source_does_not_advance_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            publish(tmp,["NYFED:SOFR"],"2026-10-01","2026-10-16",fetch=fetch,clock=CLOCK)
            previous=(Path(tmp)/"LATEST.json").read_bytes()
            def bad(id,start,end):
                if id.endswith("EFFR"):raise RuntimeError("Upstream unavailable")
                return fetch(id,start,end)
            with self.assertRaisesRegex(RuntimeError,"unavailable"):
                publish(tmp,["NYFED:SOFR","NYFED:EFFR"],"2026-10-01","2026-10-16",
                        fetch=bad,clock=lambda:datetime(2026,10,16,20,0,1,tzinfo=timezone.utc))
            self.assertEqual((Path(tmp)/"LATEST.json").read_bytes(),previous)
    def test_mutation_breaks_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=publish(tmp,["NYFED:SOFR"],"2026-10-01","2026-10-16",fetch=fetch,clock=CLOCK)
            p=Path(tmp)/result["manifest"]["assets"]["NYFED:SOFR"]["path"]
            p.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError,"integrity mismatch"):
                read_published(tmp)
if __name__=="__main__":unittest.main()
