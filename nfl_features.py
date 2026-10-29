"""Leakage-safe NFL defensive and usage features derived from completed games only."""
import json,math,datetime,statistics
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parent
MAPPING=ROOT/"data"/"nfl_opponents.json"
def season(d):return d.year if d.month>=7 else d.year-1
def mapping():
 try:return json.loads(MAPPING.read_text())
 except (OSError,ValueError):return {}
def team_context(rows):
 """Index completed games by event; do not mix 2025 and 2026 season totals."""
 opponents=mapping();games=defaultdict(list);dates={}
 for r in rows:
  eid=str(r.get("event_id") or "")
  if eid and eid in opponents:
   games[eid].append(r);dates[eid]=r["date"]
 result=defaultdict(list)
 for eid,participants in opponents.items():
  if eid not in games or len(participants)!=2:continue
  date=dates[eid]
  for defense in participants:
   attacking=[r for r in games[eid] if r["team"]!=defense]
   defending=[r for r in games[eid] if r["team"]==defense]
   if not attacking or not defending:continue
   for metric in ("PASS_YDS","RUSH_YDS","REC_YDS","REC","PASS_TD","RUSH_TD"):
    values=[r["stats"][metric] for r in attacking if metric in r["stats"]]
    if values:
     # Per-game team totals, not position-specific; no fabricated snaps/targets.
     result[(defense,metric,season(date))].append((date,eid,sum(values)))
 for key in result:result[key].sort()
 return result
def features(player_rows,market,defense=None,kickoff=None,index=None):
 valid=[r for r in player_rows if market in r["stats"] and (kickoff is None or r["date"]<kickoff)]
 if not valid:return {"available":False,"reason":"No prior player games"}
 values=[r["stats"][market] for r in valid]
 last5=values[-5:];last3=values[-3:]
 recent_dates=[r["date"] for r in valid]
 y=season(kickoff) if kickoff else season(valid[-1]["date"])
 current=[r["stats"][market] for r in valid if season(r["date"])==y]
 prior=[r["stats"][market] for r in valid if season(r["date"])==y-1]
 f={"available":True,"career_games":len(values),"season_games":len(current),
    "career_mean":round(statistics.mean(values),3),"season_mean":round(statistics.mean(current),3) if current else None,
    "prior_season_mean":round(statistics.mean(prior),3) if prior else None,
    "last3_mean":round(statistics.mean(last3),3),"last5_mean":round(statistics.mean(last5),3),
    "last5_median":round(statistics.median(last5),3),"last5_std":round(statistics.pstdev(last5),3) if len(last5)>1 else 0,
    "last5_hit_above_season_mean":round(sum(v>statistics.mean(current or values) for v in last5)/len(last5),3),
    "days_since_game":(kickoff-recent_dates[-1]).days if kickoff else None}
 if defense and index:
  games=[(d,v) for d,eid,v in index.get((defense,market,y),[]) if kickoff is None or d<kickoff]
  if len(games)>=4:
   f.update({"opponent":defense,"defense_games":len(games),"defense_team_allowed_mean":round(statistics.mean(v for d,v in games),3),
             "defense_team_allowed_last3":round(statistics.mean(v for d,v in games[-3:]),3),
             "defense_team_allowed_std":round(statistics.pstdev(v for d,v in games),3),
             "defense_context":"team aggregate; not position-specific"})
  else:f["defense_context"]="Insufficient prior completed opponent games"
 return f
def opponent_for(row,lookup):
 teams=lookup.get(str(row.get("event_id") or ""),[])
 return next((t for t in teams if t!=row["team"]),None)
def adjusted(player_rows,market,defense,kickoff,index):
 """Shrink player baseline toward prior-season and contextual opponent tendency.
 Defense factor only when at least four pre-kickoff completed games are mapped.
 No adjustment from injuries, weather or unverified inputs."""
 f=features(player_rows,market,defense,kickoff,index)
 if not f["available"] or f["career_games"]<4:return None,f
 base=f["season_mean"] if f["season_games"]>=4 else f["career_mean"]
 if f["season_games"]<4 and f["prior_season_mean"] is not None:
  n=f["season_games"];base=(n*(f["season_mean"] or 0)+4*f["prior_season_mean"])/(n+4)
 if f.get("defense_games",0)<4:return round(base,2),f
 # Defense is raw team totals; compare to same-season league mean from prior games.
 y=season(kickoff)
 league=[v for (team,m,sy),games in index.items() if m==market and sy==y for d,eid,v in games if d<kickoff]
 if len(league)<60:return round(base,2),f
 league_avg=statistics.mean(league)
 if league_avg<=0:return round(base,2),f
 ratio=f["defense_team_allowed_mean"]/league_avg
 # conservative cap and weight to avoid over-interpreting aggregate defensive data
 multiplier=1+0.2*(max(0.75,min(1.25,ratio))-1)
 f["league_team_allowed_mean"]=round(league_avg,3)
 f["defense_adjustment_pct"]=round((multiplier-1)*100,2)
 return round(base*multiplier,2),f
