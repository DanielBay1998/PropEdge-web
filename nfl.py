"""Independent NFL research endpoints. Never reuse NBA models or records."""
import os, json, time, threading, urllib.request, datetime, math
from collections import defaultdict
from pathlib import Path
from fastapi import APIRouter, HTTPException

router=APIRouter(prefix="/api/nfl",tags=["NFL"])
ROOT=Path(__file__).resolve().parent
CACHE={}
LOCK=threading.RLock()
MARKETS={"player_pass_yds":"PASS_YDS","player_rush_yds":"RUSH_YDS","player_reception_yds":"REC_YDS","player_receptions":"REC","player_pass_tds":"PASS_TD","player_rush_tds":"RUSH_TD","player_anytime_td":"ANY_TD"}
LABELS={"PASS_YDS":"Passing yards","RUSH_YDS":"Rushing yards","REC_YDS":"Receiving yards","REC":"Receptions","PASS_TD":"Passing TDs","RUSH_TD":"Rushing TDs","ANY_TD":"Anytime TD"}
def cached(key,ttl,fn):
 with LOCK:
  old=CACHE.get(key)
  if old and time.monotonic()<old[0]:return old[1]
 value=fn()
 with LOCK:CACHE[key]=(time.monotonic()+ttl,value)
 return value
def espn(path):
 url="https://site.api.espn.com/apis/site/v2/sports/football/nfl/"+path
 with urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"PropEdge-NFL/1.0","Accept":"application/json"}),timeout=12) as r:return json.load(r)
def schedule(day):
 data=espn("scoreboard?dates="+day+"&limit=40")
 games=[]
 for event in data.get("events",[]):
  comp=(event.get("competitions") or [{}])[0]
  competitors=comp.get("competitors") or []
  away=next((x for x in competitors if x.get("homeAway")=="away"),{})
  home=next((x for x in competitors if x.get("homeAway")=="home"),{})
  if not away or not home:continue
  def team(x):
   t=x.get("team") or {}
   return {"name":t.get("displayName"),"abbreviation":t.get("abbreviation"),"score":x.get("score"),"logo":t.get("logo")}
  status=(event.get("status") or {}).get("type") or {}
  games.append({"id":str(event.get("id")),"date":event.get("date"),"away":team(away),"home":team(home),"status":status.get("shortDetail") or status.get("description"),"state":status.get("state"),"completed":bool(status.get("completed"))})
 return games
@router.get("/games")
def games(date:str=""):
 from zoneinfo import ZoneInfo
 today=datetime.datetime.now(ZoneInfo("America/New_York")).date()
 if date:
  try:d=datetime.datetime.strptime(date,"%Y%m%d").date()
  except ValueError:raise HTTPException(400,"Use YYYYMMDD")
  if abs((d-today).days)>14:raise HTTPException(400,"Date outside 14-day window")
 else:d=today
 try:return {"date":d.strftime("%Y%m%d"),"games":cached("games"+d.isoformat(),120,lambda:schedule(d.strftime("%Y%m%d"))),"source":"ESPN"}
 except Exception:return {"date":d.strftime("%Y%m%d"),"games":[],"error":"NFL schedule temporarily unavailable"}
def parse_boxscore(event_id):
 data=espn("summary?event="+str(event_id))
 players={}
 for group in (data.get("boxscore") or {}).get("players") or []:
  team=(group.get("team") or {}).get("displayName") or ""
  for section in group.get("statistics") or []:
   labels=[str(x).lower() for x in section.get("labels") or []]
   name=str(section.get("name") or "").lower()
   for athlete in section.get("athletes") or []:
    if athlete.get("didNotPlay"):continue
    person=athlete.get("athlete") or {}
    pid=str(person.get("id") or "")
    if not pid:continue
    rec=players.setdefault(pid,{"id":pid,"player":person.get("displayName"),"team":team,"stats":{}})
    values=athlete.get("stats") or []
    if len(values)!=len(labels):continue
    for label,val in zip(labels,values):
     try:n=float(val)
     except (ValueError,TypeError):continue
     if not math.isfinite(n) or n<0:continue
     if name.startswith("pass") and label in ("yds","yards"):rec["stats"]["PASS_YDS"]=n
     elif name.startswith("pass") and label in ("td","tds"):rec["stats"]["PASS_TD"]=n
     elif name.startswith("rush") and label in ("yds","yards"):rec["stats"]["RUSH_YDS"]=n
     elif name.startswith("rush") and label in ("td","tds"):rec["stats"]["RUSH_TD"]=n
     elif name.startswith("receiv") and label in ("yds","yards"):rec["stats"]["REC_YDS"]=n
     elif name.startswith("receiv") and label in ("rec","receptions"):rec["stats"]["REC"]=n
 return list(players.values())
@router.get("/game/{event_id}/stats")
def game_stats(event_id:str):
 if not event_id.isdigit():raise HTTPException(400,"Invalid game ID")
 try:return {"event_id":event_id,"players":cached("stats"+event_id,600,lambda:parse_boxscore(event_id))}
 except Exception:return {"event_id":event_id,"players":[],"error":"Verified boxscore unavailable"}
def odds_board():
 key=os.getenv("PROPLINE_API_KEY","").strip()
 base={"props":[],"top_picks":[],"as_of":datetime.datetime.now(datetime.timezone.utc).isoformat(),"verified":False,"model_ready":False}
 if not key:
  base["error"]="PropLine key unavailable; no live NFL lines."
  return base
 try:
  from propline import PropLine
  client=PropLine(key)
  events=client.get_events("americanfootball_nfl")
 except Exception as e:
  base["error"]="NFL provider events unavailable: "+type(e).__name__
  return base
 if not isinstance(events,list):
  base["error"]="NFL provider returned unexpected event format"
  return base
 now=datetime.datetime.now(datetime.timezone.utc)
 upcoming=[]
 for e in events:
  if not isinstance(e,dict) or not e.get("id"):continue
  try:dt=datetime.datetime.fromisoformat(str(e.get("commence_time") or e.get("start_time")).replace("Z","+00:00"))
  except (TypeError,ValueError):continue
  if dt.tzinfo is None:continue
  if now<dt<now+datetime.timedelta(days=7):upcoming.append(e)
 for event in sorted(upcoming,key=lambda x:str(x.get("commence_time") or ""))[:16]:
  try:response=client.get_odds("americanfootball_nfl",event_id=event["id"],markets=list(MARKETS))
  except Exception:continue
  if not isinstance(response,dict):continue
  quotes=defaultdict(lambda:{"books":set(),"prices":{}})
  for book in response.get("bookmakers") or []:
   bk=str(book.get("key") or "")
   if not bk or bk in ("prizepicks","underdog","pick6"):continue
   for market in book.get("markets") or []:
    code=MARKETS.get(market.get("key"))
    if not code or market.get("line_type")!="main":continue
    if str(market.get("period") or "").lower() not in ("","full","full_game","game"):continue
    for o in market.get("outcomes") or []:
     side=str(o.get("name") or "").upper()
     if side not in ("OVER","UNDER"):continue
     try:line=float(o.get("point"))
     except (TypeError,ValueError):continue
     if not math.isfinite(line) or line<0:continue
     player=str(o.get("description") or "").strip()
     if not player:continue
     q=quotes[(player.casefold(),code,side,line)]
     q["books"].add(bk);q["prices"][bk]=o.get("price")
  for (player,code,side,line),q in quotes.items():
   if len(q["books"])<2:continue
   base["props"].append({"event_id":str(event["id"]),"game":str(event.get("away_team") or "")+" @ "+str(event.get("home_team") or ""),"player":player.title(),"market":code,"side":side,"line":line,"books":sorted(q["books"]),"prices":q["prices"],"projection":None,"edge":None,"status":"verified line; model pending"})
 base["verified"]=bool(base["props"])
 if not base["props"]:base["error"]="No independently confirmed NFL main lines available. NFL picks remain unavailable."
 return base
@router.get("/board")
def board():
 return cached("nfl_board",600,odds_board)
@router.get("/performance")
def performance():
 file=ROOT/"data"/"nfl_model_tracking.json"
 try:rows=json.loads(file.read_text()) if file.exists() else []
 except (OSError,ValueError):rows=[]
 done=[r for r in rows if r.get("status") in ("win","loss","push")]
 w=sum(r["status"]=="win" for r in done);l=sum(r["status"]=="loss" for r in done)
 return {"wins":w,"losses":l,"pushes":len(done)-w-l,"pending":sum(r.get("status")=="pending" for r in rows),"captured":len(rows),"hit_rate":round(w/(w+l)*100,1) if w+l else None,"recent":rows[-40:],"note":"NFL-only verified pregame predictions; no NFL model picks are published until projection validation."}
\n@router.get("/historical-model")\ndef historical_model():\n from nfl_model import summary\n return summary()\n