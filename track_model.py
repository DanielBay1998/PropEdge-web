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
 grade_only=os.getenv("PROPEDGE_GRADE_ONLY")=="1"
 if os.getenv("PROPEDGE_CAPTURE_IF_DUE")=="1":
  timestamps=[]
  for r in records:
   try:timestamps.append(datetime.datetime.fromisoformat(str(r["captured_at"]).replace("Z","+00:00")))
   except (ValueError,KeyError,TypeError):pass
  grade_only=bool(timestamps and (today()-max(timestamps)).total_seconds()<1800)
 if grade_only:print("Grade-only mode: skipping live odds board request",flush=True)
 # Scheduled sync must always reach grading even when the live odds board is slow.
 attempts=1 if os.getenv("PROPEDGE_CAPTURE_IF_DUE")=="1" else 5
 for attempt in range(0 if grade_only else attempts):
  try:
   req=urllib.request.Request(SITE+"/api/board",headers={"User-Agent":"PropEdge-ModelTracker/1.0","Cache-Control":"no-cache"})
   with urllib.request.urlopen(req,timeout=18 if attempts==1 else 90) as resp: board=json.load(resp)
   if isinstance(board,dict) and board.get("live") and not board.get("error"):break
   print("Board unavailable or not live; attempt",attempt+1,flush=True)
  except (urllib.error.URLError,TimeoutError,ValueError) as exc:
   print("Board fetch attempt",attempt+1,"failed:",type(exc).__name__,flush=True)
  board=None
  if attempt<attempts-1:time.sleep(15*(attempt+1))
 if board and board.get("live") and not board.get("error"):
  for signal in board.get("signals",[]):
   event=str(signal.get("event_id") or "")
   market=str(signal.get("market") or "")
   side=str(signal.get("side") or "")
   line=signal.get("line")
   player=str(signal.get("player") or "")
   if not(event and market and side in ("OVER","UNDER") and player and isinstance(line,(int,float))):continue
   # Never capture an unverified event/player identity. The live board's
   # identity guard uses current-season team evidence, not historical rosters.
   if signal.get("identity_verified_by_current_season_logs") is not True:
    print("Capture rejected: unverified player/team identity",player,event,flush=True)
    continue
   matchup=str(signal.get("game") or "").split(" @ ")
   latest=canonical(signal.get("identity_latest_team"))
   if len(matchup)!=2 or not latest or latest not in {canonical(t) for t in matchup}:
    print("Capture rejected: mismatched player/team identity",player,event,flush=True)
    continue
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
 local_games={}
 game_dates=set()
 if logs.exists():
  with logs.open(encoding="utf-8-sig",newline="") as handle:
   for row in csv.DictReader(handle):
    eid=str(row.get("Game ID") or "").strip()
    player=canonical(row.get("Player"))
    if eid and player:
     by_game[(eid,player)]=row
     info=local_games.setdefault(eid,{"date":str(row.get("Date") or "").replace("-",""),"teams":set(),"players":set()})
     if row.get("Team"):info["teams"].add(canonical(row["Team"]))
     info["players"].add(player)
    if row.get("Date"):game_dates.add(str(row["Date"]).replace("-",""))
 scoreboard={}
 scoreboard_days_ok=set()
 pending=[r for r in records if r["status"]=="pending"]
 capture_days=set()
 for r in pending:
  try:
   captured=datetime.datetime.fromisoformat(r["captured_at"].replace("Z","+00:00"))
   for offset in range(-1,8):
    capture_days.add((captured+datetime.timedelta(days=offset)).strftime("%Y%m%d"))
  except (ValueError,KeyError,TypeError):continue
 # Restrict requests to dates that actually have local game logs.
 days=sorted(capture_days & game_dates)
 for day in days:
  try:
   req=urllib.request.Request("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates="+day,headers={"User-Agent":"PropEdge-ModelTracker/1.0"})
   with urllib.request.urlopen(req,timeout=20) as resp:data=json.load(resp)
   scoreboard_days_ok.add(day)
   for e in data.get("events",[]):
    if not ((e.get("status") or {}).get("type") or {}).get("completed"):continue
    comp=(e.get("competitions") or [{}])[0]
    teams=comp.get("competitors") or []
    away=next((t for t in teams if t.get("homeAway")=="away"),{})
    home=next((t for t in teams if t.get("homeAway")=="home"),{})
    an=(away.get("team") or {}).get("displayName") or ""
    hn=(home.get("team") or {}).get("displayName") or ""
    key=canonical(an)+"@"+canonical(hn)
    scoreboard.setdefault(key,[]).append((day,str(e.get("id"))))
  except Exception as exc:
   print("Scoreboard unavailable for",day,type(exc).__name__,flush=True)
 # Final boxscore CSV is populated only by the completed-game sync. When ESPN
 # scoreboard is unavailable, use those finalized game IDs as a fallback.
 for eid,info in local_games.items():
  if len(info["teams"])!=2 or info["date"] not in capture_days:continue
  for r in pending:
   teams=str(r.get("game") or "").split(" @ ")
   if len(teams)!=2:continue
   pair={canonical(teams[0]),canonical(teams[1])}
   if pair!=info["teams"]:continue
   key=canonical(teams[0])+"@"+canonical(teams[1])
   candidate=(info["date"],eid)
   if candidate not in scoreboard.setdefault(key,[]):scoreboard[key].append(candidate)
 graded_now=0
 unmatched_games={}
 missing_players={}
 for r in pending:
  teams=str(r.get("game") or "").split(" @ ")
  match=canonical(teams[0])+"@"+canonical(teams[1]) if len(teams)==2 else ""
  # A matchup may occur on multiple dates. Only use a completed game
  # within the capture window, and require a unique player boxscore.
  try:
   capture_date=datetime.datetime.fromisoformat(r["captured_at"].replace("Z","+00:00")).date()
  except (ValueError,KeyError,TypeError):
   unmatched_games["invalid capture date"]=unmatched_games.get("invalid capture date",0)+1
   continue
  eligible=[(day,eid) for day,eid in scoreboard.get(match,[])
            if -1 <= (datetime.datetime.strptime(day,"%Y%m%d").date()-capture_date).days <= 7]
  player_key=canonical(r["player"])
  matching=[by_game[(eid,player_key)] for day,eid in eligible if (eid,player_key) in by_game]
  if len(matching)>1:
   unmatched_games["ambiguous completed game: "+match]=unmatched_games.get("ambiguous completed game: "+match,0)+1
   continue
  if not eligible:
   unmatched_games[match]=unmatched_games.get(match,0)+1
   continue
  row=matching[0] if len(matching)==1 else None
  if row and canonical(row.get("Team")) not in {canonical(t) for t in teams}:
   missing_players["team mismatch: "+r["player"]]=missing_players.get("team mismatch: "+r["player"],0)+1
   continue
  if not row:
   missing_players[r["player"]]=missing_players.get(r["player"],0)+1
   continue
  try:
   actual=sum(float(row[k]) for k in ("PTS","REB","AST")) if r["market"]=="PRA" else float(row[r["market"]])
   if actual<0:continue
   r["actual"]=actual
   r["status"]="push" if actual==float(r["line"]) else ("win" if (actual>float(r["line"]))==(r["side"]=="OVER") else "loss")
   r["graded_at"]=today().isoformat()
   graded_now+=1
  except (ValueError,KeyError,TypeError):pass
 # Preserve unresolved predictions, but flag demonstrably incorrect identities.
 # A player's boxscore on a different completed game on the capture date
 # is evidence of an incorrect event assignment, not a losing wager.
 invalid_assignments={}
 invalid_games={}
 for r in pending:
  if r["status"]!="pending":continue
  try: capture_date=datetime.datetime.fromisoformat(r["captured_at"].replace("Z","+00:00")).date()
  except (ValueError,KeyError,TypeError):continue
  player_key=canonical(r.get("player"))
  recorded={canonical(t) for t in str(r.get("game") or "").split(" @ ")}
  if len(recorded)!=2:continue
  other=[]
  for (eid,p),row in by_game.items():
   if p!=player_key:continue
   try: game_date=datetime.datetime.strptime(str(row.get("Date") or "").replace("-",""),"%Y%m%d").date()
   except ValueError:continue
   if -1 <= (game_date-capture_date).days <= 7 and canonical(row.get("Team")) not in recorded:
    other.append((eid,str(row.get("Team") or ""),str(row.get("Date") or "")))
  # If all capture-window scoreboards were fetched successfully and no
  # completed game has the recorded matchup, quarantine stale records
  # for manual event-ID reconciliation rather than inventing a grade.
  eligible_days={(capture_date+datetime.timedelta(days=i)).strftime("%Y%m%d") for i in range(-1,8)}
  game_key="@".join(canonical(t) for t in str(r.get("game") or "").split(" @ "))
  completed_match=any(day in eligible_days for day,eid in scoreboard.get(game_key,[]))
  stale=(today().date()-capture_date).days>8
  fully_checked=bool(eligible_days & scoreboard_days_ok) and all(
   d in scoreboard_days_ok for d in eligible_days if d<=today().strftime("%Y%m%d"))
  if stale and fully_checked and not completed_match:
   invalid_games[r["id"]]=r.get("game")
   r["validation_status"]="unmatched_completed_schedule"
   r["validation_reason"]="No completed ESPN fixture for recorded matchup in fully checked capture window; verify source event ID"
  elif other:
   r["validation_status"]="invalid_matchup_review"
   r["validation_reason"]="Player appears in completed boxscore for a different team in capture window; verify game identity before grading"
   r["validation_evidence"]=[{"game_id":eid,"team":team,"date":date} for eid,team,date in other[:5]]
   invalid_assignments[r["id"]]=r["validation_evidence"]
  elif not (stale and fully_checked and not completed_match):
   r.pop("validation_status",None)
   r.pop("validation_reason",None)
   r.pop("validation_evidence",None)
 print("Schedule audit:",len(invalid_games),"stale predictions without matching completed fixture",flush=True)
 print("Identity audit:",len(invalid_assignments),"pending predictions flagged for review; no automatic grades assigned",flush=True)
 print("Pending matchup audit:",json.dumps(unmatched_games,sort_keys=True),"missing player rows:",json.dumps(missing_players,sort_keys=True),flush=True)
 print("Grading audit:",len(pending),"pending;",len(days),"scoreboard dates;",len(scoreboard),"completed matchups;",graded_now,"new grades; game-log rows",len(by_game),flush=True)
 DATA.parent.mkdir(parents=True,exist_ok=True)
 DATA.write_text(json.dumps(records,indent=2,ensure_ascii=False)+"\n")
 print(f"Tracking {len(records)} snapshots; {sum(r['status']!='pending' for r in records)} graded")
if __name__=="__main__":run()
