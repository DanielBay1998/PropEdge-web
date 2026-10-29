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
def candidate(rows,market,kind):
 values=[r["stats"][market] for r in rows if market in r["stats"]]
 if len(values)<4:return None
 if kind=="career_mean":return sum(values)/len(values)
 if kind=="season_mean":
  year=season_of(rows[-1]["date"])
  season=[r["stats"][market] for r in rows if season_of(r["date"])==year and market in r["stats"]]
  return sum(season)/len(season) if len(season)>=4 else sum(values)/len(values)
 if kind=="recent_mean":return sum(values[-5:])/len(values[-5:])
 if kind=="weighted_recent":return predict(rows,market)
 return None
def evaluate(rows):
 groups=defaultdict(list)
 for row in rows:groups[row["key"]].append(row)
 tests=defaultdict(list)
 for games in groups.values():
  for i,g in enumerate(games):
   past=[p for p in games[:i] if p["date"]<g["date"]]
   for market,actual in g["stats"].items():
    if len([p for p in past if market in p["stats"]])<4:continue
    predictions={k:candidate(past,market,k) for k in ("career_mean","season_mean","recent_mean","weighted_recent")}
    if all(v is not None for v in predictions.values()):tests[market].append((g["date"],actual,predictions))
 summary={}
 for market,records in tests.items():
  records.sort(key=lambda r:r[0])
  cut=int(len(records)*0.6)
  training,holdout=records[:cut],records[cut:]
  if len(training)<100 or len(holdout)<100:
   summary[market]={"samples":len(records),"holdout_samples":len(holdout),"improved":False,"reason":"Insufficient chronological holdout samples"}
   continue
  choices=("career_mean","season_mean","recent_mean","weighted_recent")
  selected=min(choices,key=lambda k:sum(abs(r[2][k]-r[1]) for r in training)/len(training))
  mae=lambda data,k:sum(abs(r[2][k]-r[1]) for r in data)/len(data)
  score=mae(holdout,selected);baseline_score=mae(holdout,"career_mean")
  summary[market]={"samples":len(records),"holdout_samples":len(holdout),"model":selected,"model_mae":round(score,3),"baseline_mae":round(baseline_score,3),"improved":bool(score+0.001<baseline_score),"validation":"Chronological 60% model selection, later 40% untouched holdout"}
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
def projections(rows,validation=None):
 groups=defaultdict(list)
 for row in rows:groups[row["key"]].append(row)
 result={}
 for name,games in groups.items():
  market={m:round(candidate(games,m,(validation or {}).get(m,{}).get("model","career_mean")),2) if candidate(games,m,(validation or {}).get(m,{}).get("model","career_mean")) is not None else None for m in FIELDS}
  if any(v is not None for v in market.values()):
   result[name]={"player":games[-1]["player"],"team":games[-1]["team"],"player_id":games[-1].get("player_id"),"season_games":sum(season_of(g["date"])==season_of(games[-1]["date"]) for g in games),"samples":len(games),"date":games[-1]["date"].isoformat(),"markets":market,
                 "recent":[{"date":g["date"].isoformat(),**g["stats"]} for g in games[-10:]]}
 return result
def summary():
 rows=history();validation=evaluate(rows)
 return {"history_rows":len(rows),"players":len(set(r["key"] for r in rows)),"validation":validation,"projections":projections(rows,validation),"method":"Candidate forecasts (career, season, last-five, weighted recent) chosen using earlier chronological games and evaluated on a later untouched 40% holdout. No probability calibration.","as_of":max((r["date"].isoformat() for r in rows),default=None)}
