"""Capture NFL pregame model signals and grade only verified final results."""
import json,datetime,urllib.request,os
from pathlib import Path
from nfl import espn,parse_boxscore
from nfl_model import canon
ROOT=Path(__file__).resolve().parent
FILE=ROOT/"data"/"nfl_model_tracking.json"
def load():
 try:return json.loads(FILE.read_text(encoding="utf-8")) if FILE.exists() else []
 except (ValueError,OSError):return []
def save(rows):
 FILE.parent.mkdir(exist_ok=True)
 FILE.write_text(json.dumps(rows,indent=2)+"\n",encoding="utf-8")
def matchgame(name):
 return canon(name).replace("los angeles","la").replace("new york","ny").replace("san francisco","sf").replace("tampa bay","tb")
def run():
 rows=load()
 now=datetime.datetime.now(datetime.timezone.utc)
 known={r.get("id") for r in rows}
 # Snapshot only qualifying, validated, pregame NFL picks. Never snapshot research-only lines.
 url=os.getenv("PROPEDGE_NFL_BOARD_URL","https://propedge-custom.onrender.com/api/nfl/board")
 try:
  with urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"PropEdge-NFL-Tracker/1.0"}),timeout=25) as res:board=json.load(res)
  for p in board.get("top_picks") or []:
   if not p.get("model_validated") or p.get("projection") is None:continue
   if p.get("commence_time"):
    try:
     start=datetime.datetime.fromisoformat(p["commence_time"].replace("Z","+00:00"))
     if start<=now:continue
    except (TypeError,ValueError):continue
   else:continue
   key="|".join(str(p.get(k,"")) for k in ("event_id","player","market","side","line"))
   if key in known:continue
   rows.append({"id":key,"captured_at":now.isoformat(),"game":p.get("game"),"player":p.get("player"),"market":p.get("market"),"side":p.get("side"),"line":p.get("line"),"projection":p.get("projection"),"odds":p.get("odds"),"book":p.get("book"),"status":"pending","actual":None,"graded_at":None})
   known.add(key)
 except Exception as e:print("NFL snapshot unavailable",type(e).__name__,flush=True)
 pending=[r for r in rows if r.get("status")=="pending"]
 days=set()
 for r in pending:
  try:
   d=datetime.datetime.fromisoformat(r["captured_at"].replace("Z","+00:00")).date()
   for offset in (0,1,2):
    x=d+datetime.timedelta(days=offset)
    if x<=now.date() and (now.date()-x).days<=10:days.add(x.strftime("%Y%m%d"))
  except (ValueError,KeyError,TypeError):pass
 final={}
 for day in sorted(days):
  try:events=espn("scoreboard?dates="+day+"&limit=40").get("events",[])
  except Exception:continue
  for e in events:
   if not ((e.get("status") or {}).get("type") or {}).get("completed"):continue
   comp=(e.get("competitions") or [{}])[0]
   teams=comp.get("competitors") or []
   away=next((x for x in teams if x.get("homeAway")=="away"),{})
   home=next((x for x in teams if x.get("homeAway")=="home"),{})
   if not away or not home:continue
   game=matchgame((away.get("team") or {}).get("displayName"))+" @ "+matchgame((home.get("team") or {}).get("displayName"))
   final[game]=str(e.get("id"))
 box={}
 graded=0
 for r in pending:
  game=matchgame(r.get("game") or "")
  eid=final.get(game)
  if not eid:continue
  if eid not in box:
   try:box[eid]=parse_boxscore(eid)
   except Exception:box[eid]=[]
  player=canon(r.get("player"))
  matching=[p for p in box[eid] if canon(p.get("player"))==player]
  if len(matching)!=1:continue
  actual=matching[0].get("stats",{}).get(r.get("market"))
  if actual is None:continue
  try:
   line=float(r["line"]);actual=float(actual)
   r["status"]="push" if actual==line else ("win" if (actual>line)==(r["side"]=="OVER") else "loss")
   r["actual"]=actual;r["graded_at"]=now.isoformat();graded+=1
  except (ValueError,TypeError,KeyError):continue
 save(rows)
 print("NFL tracking",len(rows),"total",len(pending),"pending before",graded,"graded now",flush=True)
if __name__=="__main__":run()
