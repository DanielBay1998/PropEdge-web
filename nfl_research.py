"""NFL season-aware research. No future games or made-up injuries/odds."""
import datetime,math,statistics
from collections import defaultdict
from nfl_model import history,canon,FIELDS
from nfl import espn
def profile(player,market=None):
 rows=[r for r in history() if r["key"]==canon(player)]
 if not rows:return {"player":player,"found":False}
 current=max(r["date"].year if r["date"].month>=7 else r["date"].year-1 for r in rows)
 season=[r for r in rows if (r["date"].year if r["date"].month>=7 else r["date"].year-1)==current]
 out={"player":rows[-1]["player"],"team":rows[-1]["team"],"found":True,"season":current,"games":len(season),"player_id":rows[-1].get("player_id"),"markets":{}}
 for m in FIELDS:
  vals=[r["stats"][m] for r in season if m in r["stats"]]
  last=[r["stats"][m] for r in season[-5:] if m in r["stats"]]
  out["markets"][m]={"season_avg":round(sum(vals)/len(vals),2) if vals else None,"last5_avg":round(sum(last)/len(last),2) if last else None,"games":len(vals)}
 out["recent"]=[{"date":r["date"].isoformat(),"event_id":r["event_id"],"stats":r["stats"]} for r in season[-10:]]
 return out
def opponent_allowance(team,market,season=2026):
 # ESPN historical event IDs and participating teams are resolved separately by the scoreboard;
 # do not claim a defense rating without a confirmed opponent/team mapping.
 return None
def athlete_photo(player_id):
 if not player_id:return None
 return "https://a.espncdn.com/i/headshots/nfl/players/full/"+str(player_id)+".png"
def team_directory():
 data=espn("teams?limit=40")
 out={}
 for sport in data.get("sports",[]):
  for league in sport.get("leagues",[]):
   for entry in league.get("teams",[]):
    t=entry.get("team") or {}
    name=t.get("displayName")
    if name:out[canon(name)]={"name":name,"logo":t.get("logos",[{}])[0].get("href") if t.get("logos") else None,"abbreviation":t.get("abbreviation")}
 return out
