"""Persist pregame qualifying signals and grade only against completed game logs.

Run from GitHub Actions; repository commits preserve history across Render restarts.
Never retroactively modify captured projection, line, or side.
"""
import csv,json,urllib.request,datetime,os
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DATA=ROOT/"data"/"model_tracking.json"
SITE=os.getenv("PROPEDGE_SITE","https://propedge-custom.onrender.com").rstrip("/")
def canonical(s):
 import re
 return re.sub(r"[^a-z0-9]","",str(s or "").lower().replace("jr.",""))
def today():return datetime.datetime.now(datetime.timezone.utc)
def run():
 records=json.loads(DATA.read_text()) if DATA.exists() else []
 with urllib.request.urlopen(SITE+"/api/board",timeout=75) as resp: board=json.load(resp)
 if board.get("live") and not board.get("error"):
  for signal in board.get("signals",[]):
   event=str(signal.get("event_id") or "")
   market=str(signal.get("market") or "")
   side=str(signal.get("side") or "")
   line=signal.get("line")
   player=str(signal.get("player") or "")
   if not(event and market and side in ("OVER","UNDER") and player and isinstance(line,(int,float))):continue
   key="|".join([event,canonical(player),market,side,str(line)])
   if any(r["id"]==key for r in records):continue
   records.append({"id":key,"captured_at":today().isoformat(),"event_id":event,
     "player":player,"market":market,"side":side,"line":line,
     "projection":signal.get("projection"),"book":signal.get("book"),
     "odds":signal.get("odds"),"game":signal.get("game"),"status":"pending",
     "actual":None,"graded_at":None})
 # Reconcile provider event IDs to ESPN IDs by a unique, completed matchup.
 scoreboard={}
 try:
  dates={datetime.datetime.fromisoformat(r["captured_at"]).strftime("%Y%m%d") for r in records if r["status"]=="pending"}
  for day in dates:
   for delta in (0,1):
    d=(datetime.datetime.strptime(day,"%Y%m%d")+datetime.timedelta(days=delta)).strftime("%Y%m%d")
    with urllib.request.urlopen("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates="+d,timeout=20) as resp:
     data=json.load(resp)
    for e in data.get("events",[]):
     comp=(e.get("competitions") or [{}])[0]
     teams=comp.get("competitors") or []
     away=next((t for t in teams if t.get("homeAway")=="away"),{})
     home=next((t for t in teams if t.get("homeAway")=="home"),{})
     an=(away.get("team") or {}).get("displayName") or ""
     hn=(home.get("team") or {}).get("displayName") or ""
     if ((e.get("status") or {}).get("type") or {}).get("completed"):
      scoreboard.setdefault(canonical(an)+"@"+canonical(hn),set()).add(str(e.get("id")))
 except Exception as exc:print("Scoreboard reconciliation unavailable:",type(exc).__name__)
 logs=ROOT/"data"/"nba_2026_27.csv"
 if logs.exists():
  with logs.open(encoding="utf-8-sig",newline="") as handle:
   rows=list(csv.DictReader(handle))
  by_game={}
  for row in rows:
   eid=str(row.get("Game ID") or "").strip()
   player=canonical(row.get("Player"))
   if eid and player:by_game[(eid,player)]=row
  for r in records:
   if r["status"]!="pending":continue
   teams=str(r.get("game") or "").split(" @ ")
   match=canonical(teams[0])+"@"+canonical(teams[1]) if len(teams)==2 else ""
   ids=scoreboard.get(match,set())
   if len(ids)!=1:continue
   row=by_game.get((next(iter(ids)),canonical(r["player"])))
   if not row:continue
   try:
    actual=sum(float(row[k]) for k in ("PTS","REB","AST")) if r["market"]=="PRA" else float(row[r["market"]])
    if actual<0:continue
    r["actual"]=actual
    r["status"]="push" if actual==float(r["line"]) else ("win" if (actual>float(r["line"]))==(r["side"]=="OVER") else "loss")
    r["graded_at"]=today().isoformat()
   except (ValueError,KeyError,TypeError):pass
 DATA.parent.mkdir(parents=True,exist_ok=True)
 DATA.write_text(json.dumps(records,indent=2,ensure_ascii=False)+"\n")
 print(f"Tracking {len(records)} snapshots; {sum(r['status']!='pending' for r in records)} graded")
if __name__=="__main__":run()
