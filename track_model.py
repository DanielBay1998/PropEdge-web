"""Persist pregame qualifying signals and grade only against completed game logs.

Run from GitHub Actions; repository commits preserve history across Render restarts.
Never retroactively modify captured projection, line, or side.
"""
import csv,json,urllib.request,urllib.error,datetime,os,time
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
 board=None
 for attempt in range(5):
  try:
   req=urllib.request.Request(SITE+"/api/board",headers={"User-Agent":"PropEdge-ModelTracker/1.0","Cache-Control":"no-cache"})
   with urllib.request.urlopen(req,timeout=90) as resp: board=json.load(resp)
   if isinstance(board,dict) and board.get("live") and not board.get("error"):break
   print("Board unavailable or not live; attempt",attempt+1,flush=True)
  except (urllib.error.URLError,TimeoutError,ValueError) as exc:
   print("Board fetch attempt",attempt+1,"failed:",type(exc).__name__,flush=True)
  board=None
  if attempt<4:time.sleep(15*(attempt+1))
 if board and board.get("live") and not board.get("error"):
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
 # Match only completed ESPN games to the exact game-log ID.
 # The original tracker used capture UTC date as game date and swallowed a
 # scoreboard exception for the entire batch. Use the actual log dates and
 # isolate network failures per scoreboard day.
 logs=ROOT/"data"/"nba_2026_27.csv"
 by_game={}
 game_dates=set()
 if logs.exists():
  with logs.open(encoding="utf-8-sig",newline="") as handle:
   for row in csv.DictReader(handle):
    eid=str(row.get("Game ID") or "").strip()
    player=canonical(row.get("Player"))
    if eid and player:by_game[(eid,player)]=row
    if row.get("Date"):game_dates.add(str(row["Date"]).replace("-",""))
 scoreboard={}
 pending=[r for r in records if r["status"]=="pending"]
 capture_days=set()
 for r in pending:
  try:
   captured=datetime.datetime.fromisoformat(r["captured_at"].replace("Z","+00:00"))
   for offset in (-1,0,1,2):
    capture_days.add((captured+datetime.timedelta(days=offset)).strftime("%Y%m%d"))
  except (ValueError,KeyError,TypeError):continue
 # Restrict requests to dates that actually have local game logs.
 days=sorted(capture_days & game_dates)
 for day in days:
  try:
   req=urllib.request.Request("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates="+day,headers={"User-Agent":"PropEdge-ModelTracker/1.0"})
   with urllib.request.urlopen(req,timeout=20) as resp:data=json.load(resp)
   for e in data.get("events",[]):
    if not ((e.get("status") or {}).get("type") or {}).get("completed"):continue
    comp=(e.get("competitions") or [{}])[0]
    teams=comp.get("competitors") or []
    away=next((t for t in teams if t.get("homeAway")=="away"),{})
    home=next((t for t in teams if t.get("homeAway")=="home"),{})
    an=(away.get("team") or {}).get("displayName") or ""
    hn=(home.get("team") or {}).get("displayName") or ""
    key=canonical(an)+"@"+canonical(hn)
    scoreboard.setdefault(key,set()).add(str(e.get("id")))
  except Exception as exc:
   print("Scoreboard unavailable for",day,type(exc).__name__,flush=True)
 graded_now=0
 for r in pending:
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
   graded_now+=1
  except (ValueError,KeyError,TypeError):pass
 print("Grading audit:",len(pending),"pending;",len(days),"scoreboard dates;",len(scoreboard),"completed matchups;",graded_now,"new grades; game-log rows",len(by_game),flush=True)
 DATA.parent.mkdir(parents=True,exist_ok=True)
 DATA.write_text(json.dumps(records,indent=2,ensure_ascii=False)+"\n")
 print(f"Tracking {len(records)} snapshots; {sum(r['status']!='pending' for r in records)} graded")
if __name__=="__main__":run()
