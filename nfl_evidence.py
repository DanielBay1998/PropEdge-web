"""NFL model diagnostics based on independently graded historical records."""
import json, math
from pathlib import Path
FILE=Path(__file__).resolve().parent/"data"/"nfl_model_tracking.json"
def performance_samples(market):
 try:rows=json.loads(FILE.read_text())
 except (OSError,ValueError):return []
 return [r for r in rows if r.get("market")==market and r.get("status") in ("win","loss") and r.get("projection") is not None]
def evidence(market):
 rows=performance_samples(market)
 n=len(rows)
 if n<100:return {"ready":False,"samples":n,"minimum":100}
 wins=sum(r["status"]=="win" for r in rows)
 p=(wins+0.5)/(n+1)
 se=math.sqrt(p*(1-p)/(n+1))
 return {"ready":True,"samples":n,"historical_hit_rate":round(p,4),"approx_interval":[round(max(0,p-1.96*se),4),round(min(1,p+1.96*se),4)],"note":"Historical tracked picks only; not calibrated to a particular line or price"}
