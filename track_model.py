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
 if board.get("live") and board.get("verified") and not board.get("error"):
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
 # Only settle when the current-season game log contains the corresponding
 # completed game ID. Never match by player/date alone.
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
   row=by_game.get((r["event_id"],canonical(r["player"])))
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
