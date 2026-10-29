"""Capture-time minutes shadow: no future games and no production pick changes."""
import csv,datetime,math
from pathlib import Path
ROOT=Path(__file__).resolve().parent
def canonical(s):
 import re
 return re.sub(r"[^a-z0-9]","",str(s or "").lower().replace("jr.",""))
def number(v):
 try:
  n=float(v)
  return n if math.isfinite(n) else None
 except (ValueError,TypeError):return None
def load_history(before):
 result={}
 for file in ("propedge_historical_2025_26_minutes.csv","nba_2026_27.csv"):
  path=ROOT/"data"/file
  if not path.exists():continue
  with path.open(encoding="utf-8-sig",newline="") as f:
   for r in csv.DictReader(f):
    day=str(r.get("Date") or "")
    if len(day)!=8 or not day.isdigit() or day>=before:continue
    key=(canonical(r.get("Player")),day,str(r.get("Game ID") or ""))
    result[key]=r
 groups={}
 for (name,day,eid),r in result.items():
  groups.setdefault(name,[]).append((day,eid,r))
 for rows in groups.values():rows.sort(key=lambda x:(x[0],x[1]))
 return groups
def candidate(signal,groups):
 name=canonical(signal.get("player"))
 market=str(signal.get("market") or "")
 if market not in ("PTS","REB","AST","3PM","PRA"):return None
 baseline=number(signal.get("projection"))
 if baseline is None or baseline<0:return None
 valid=[]
 for day,eid,r in groups.get(name,[]):
  minutes=number(r.get("MIN"))
  if minutes is None or not 1<=minutes<=60:continue
  if market=="PRA":
   parts=[number(r.get(k)) for k in ("PTS","REB","AST")]
   stat=sum(parts) if all(v is not None for v in parts) else None
  else:stat=number(r.get(market))
  if stat is not None and stat>=0:valid.append((minutes,stat))
 valid=valid[-20:]
 if len(valid)<3:return None
 last5=valid[-5:];last10=valid[-10:]
 projected_minutes=max(8,min(42,.7*sum(m for m,v in last5)/len(last5)+.3*sum(m for m,v in valid)/len(valid)))
 rate_recent=sum(v/m for m,v in last10)/len(last10)
 rate_long=sum(v/m for m,v in valid)/len(valid)
 minute_based=projected_minutes*(.65*rate_recent+.35*rate_long)
 recent=sum(v for m,v in last10)/len(last10)
 adjusted_recent=.75*recent+.25*minute_based
 raw=.8*baseline+.2*adjusted_recent
 cap=max(1,baseline*.15)
 projection=max(0,min(baseline+cap,max(baseline-cap,raw)))
 return {"shadow_minutes_projection":round(projection,5),"shadow_projected_minutes":round(projected_minutes,3),"shadow_minutes_samples":len(valid),"shadow_model":"minutes_v1_capture_time"}
