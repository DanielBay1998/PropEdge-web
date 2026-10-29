"""NFL-only, walk-forward historical model. Never train on future results."""
import csv,datetime,json,math
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parent
HISTORY=ROOT/"data"/"nfl_gamelogs.csv"
FIELDS=("PASS_YDS","RUSH_YDS","REC_YDS","REC","PASS_TD","RUSH_TD")
def canon(s):return " ".join(str(s or "").lower().replace("’","'").replace(".","").split())
def history():
 if not HISTORY.exists():return []
 with HISTORY.open(encoding="utf-8-sig",newline="") as f:raw=list(csv.DictReader(f))
 out=[]
 for row in raw:
  if not row.get("date") or not row.get("player"):continue
  try:date=datetime.date.fromisoformat(row["date"][:10])
  except ValueError:continue
  stats={}
  for market in FIELDS:
   try:
    v=float(row.get(market,""))
    if math.isfinite(v) and v>=0:stats[market]=v
   except (ValueError,TypeError):pass
  if stats:out.append({"date":date,"event_id":row.get("event_id"),"player_id":row.get("player_id"),"player":row["player"],"key":canon(row["player"]),"team":row.get("team"),"stats":stats})
 return sorted(out,key=lambda r:(r["date"],r["event_id"] or ""))
def predict(rows,market):
 values=[r["stats"][market] for r in rows if market in r["stats"]]
 if len(values)<4:return None
 last=values[-8:]
 weights=list(range(1,len(last)+1))
 return round(sum(v*w for v,w in zip(last,weights))/sum(weights),2)
def baseline(rows,market):
 values=[r["stats"][market] for r in rows if market in r["stats"]]
 return sum(values)/len(values) if len(values)>=4 else None
def evaluate(rows):
 groups=defaultdict(list)
 for row in rows:groups[row["key"]].append(row)
 errors=defaultdict(lambda:{"model":[],"baseline":[]})
 for games in groups.values():
  for i,g in enumerate(games):
   past=[p for p in games[:i] if p["date"]<g["date"]]
   for market,actual in g["stats"].items():
    p=predict(past,market);b=baseline(past,market)
    if p is None or b is None:continue
    errors[market]["model"].append(abs(p-actual))
    errors[market]["baseline"].append(abs(b-actual))
 summary={}
 for m,e in errors.items():
  n=len(e["model"]);mae=sum(e["model"])/n if n else None;bm=sum(e["baseline"])/n if n else None
  summary[m]={"samples":n,"model_mae":round(mae,3) if n else None,"baseline_mae":round(bm,3) if n else None,"improved":bool(n>=100 and mae<bm)}
 return summary
def season_of(day):return day.year if day.month>=7 else day.year-1
def season_predict(games,market):
 current=season_of(games[-1]["date"])
 this=[r for r in games if season_of(r["date"])==current and market in r["stats"]]
 prior=[r for r in games if season_of(r["date"])==current-1 and market in r["stats"]]
 if len(this)>=4:return predict(this,market)
 if len(this)>=2 and len(prior)>=4:
  current_est=predict(this,market) if len(this)>=4 else sum(r["stats"][market] for r in this)/len(this)
  previous=predict(prior,market)
  return round((len(this)*current_est+2*previous)/(len(this)+2),2)
 return predict(games,market)
def projections(rows):
 groups=defaultdict(list)
 for row in rows:groups[row["key"]].append(row)
 result={}
 for name,games in groups.items():
  market={m:season_predict(games,m) for m in FIELDS}
  if any(v is not None for v in market.values()):
   result[name]={"player":games[-1]["player"],"team":games[-1]["team"],"player_id":games[-1].get("player_id"),"season_games":sum(season_of(g["date"])==season_of(games[-1]["date"]) for g in games),"samples":len(games),"date":games[-1]["date"].isoformat(),"markets":market,
                 "recent":[{"date":g["date"].isoformat(),**g["stats"]} for g in games[-10:]]}
 return result
def summary():
 rows=history();validation=evaluate(rows)
 return {"history_rows":len(rows),"players":len(set(r["key"] for r in rows)),"validation":validation,"projections":projections(rows),"method":"Current-season weighted recent games with prior-season shrinkage for small samples; walk-forward evaluation of the base weighted model against prior-game career sample mean. Descriptive only, no probability calibration.","as_of":max((r["date"].isoformat() for r in rows),default=None)}
