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
 try:
  if not date:
   week=[]
   for offset in range(0,8):
    day=today+datetime.timedelta(days=offset)
    week.extend(cached("games"+day.isoformat(),120,lambda day=day:schedule(day.strftime("%Y%m%d"))))
   return {"date":d.strftime("%Y%m%d"),"games":week,"source":"ESPN","range":"next 8 days"}
  return {"date":d.strftime("%Y%m%d"),"games":cached("games"+d.isoformat(),120,lambda:schedule(d.strftime("%Y%m%d"))),"source":"ESPN"}
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
 from nfl_model import summary, canon
 from nfl_research import athlete_photo
 from nfl_features import team_context,adjusted,features
 from nfl_model import history
 from nfl_context import records,for_prop,injury_block
 context_rows=records()
 history_rows=history()
 player_history_index=defaultdict(list)
 for player_row in history_rows:player_history_index[player_row["key"]].append(player_row)
 feature_index=team_context(history_rows)
 model=summary()
 projections=model["projections"]
 validation=model["validation"]
 key=os.getenv("PROPLINE_API_KEY","").strip()
 base={"props":[],"top_picks":[],"as_of":datetime.datetime.now(datetime.timezone.utc).isoformat(),"verified":False,"model_ready":False,"model":{"history_rows":model["history_rows"],"validation":validation,"as_of":model["as_of"]}}
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
 if isinstance(events,dict):events=events.get("events") or events.get("data") or []
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
    if not code or market.get("line_type") not in (None,"main"):continue
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
   info=projections.get(canon(player))
   projection=(info or {}).get("markets",{}).get(code)
   opponent=next((t for t in (str(event.get("away_team") or ""),str(event.get("home_team") or "")) if t and t!=(info or {}).get("team")),None)
   advanced={}
   if info and code in ("PASS_YDS","RUSH_YDS","REC_YDS","REC","PASS_TD","RUSH_TD"):
    player_history=player_history_index.get(canon(player),[])
    kickoff_date=datetime.datetime.fromisoformat(str(event.get("commence_time") or event.get("start_time")).replace("Z","+00:00")).date()
    projected,advanced=adjusted(player_history,code,opponent,kickoff_date,feature_index)
    if validation.get(code,{}).get("model")=="opponent_adjusted" and projected is not None and advanced.get("defense_games",0)>=4:projection=projected
   context=for_prop(context_rows,str(event["id"]),(info or {}).get("player_id"),(info or {}).get("team"),event.get("commence_time") or event.get("start_time"))
   audit=validation.get(code,{})
   validated=bool(projection is not None and info["samples"]>=6 and audit.get("improved") and not injury_block(context))
   edge=round((projection-line) if side=="OVER" else (line-projection),2) if projection is not None else None
   record={"event_id":str(event["id"]),"commence_time":event.get("commence_time") or event.get("start_time"),"game":str(event.get("away_team") or "")+" @ "+str(event.get("home_team") or ""),"player":(info or {}).get("player") or player.title(),"market":code,"side":side,"line":line,"books":sorted(q["books"]),"prices":q["prices"],"projection":projection,"advanced_metrics":advanced,"verified_context":context,"opponent":opponent,"edge":edge,"sample_games":(info or {}).get("samples",0),"season_games":(info or {}).get("season_games",0),"player_id":(info or {}).get("player_id"),"player_photo":athlete_photo((info or {}).get("player_id")),"model_validated":validated,"status":"historical model validated" if validated else "provisional research"}
   base["props"].append(record)
   if validated and edge is not None and edge>0 and record.get("commence_time"):
    available=[(bk,price) for bk,price in q["prices"].items() if isinstance(price,(int,float)) and (price>=100 or price<=-100)]
    if available:
     try:
      kickoff=datetime.datetime.fromisoformat(str(record["commence_time"]).replace("Z","+00:00"))
      if kickoff<=datetime.datetime.now(datetime.timezone.utc):continue
     except (ValueError,TypeError):continue
     best=max(available,key=lambda bp:bp[1])
     base["top_picks"].append({**record,"book":best[0],"odds":best[1]})
 base["verified"]=bool(base["props"])
 base["top_picks"].sort(key=lambda p:p["edge"],reverse=True)
 base["model_ready"]=bool(base["top_picks"])
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

@router.get("/historical-model")
def historical_model():
 from nfl_model import summary
 return summary()

@router.get("/player/{player_name}")
def player_research(player_name:str):
 from nfl_research import profile,athlete_photo
 if len(player_name)>100:raise HTTPException(400,"Invalid player name")
 result=profile(player_name)
 if result.get("found"):result["photo"]=athlete_photo(result.get("player_id"))
 return result

@router.get("/teams")
def nfl_teams():
 from nfl_research import team_directory
 try:return {"teams":cached("nfl_teams",86400,team_directory)}
 except Exception:return {"teams":{},"error":"Team metadata unavailable"}

@router.get("/model-evidence/{market}")
def model_evidence(market:str):
 from nfl_evidence import evidence
 if market not in LABELS:raise HTTPException(400,"Unknown NFL market")
 return evidence(market)

@router.get("/matchup/{opponent}/{market}")
def matchup_report(opponent:str,market:str):
 from nfl_matchups import matchup
 if market not in LABELS:raise HTTPException(400,"Unknown NFL market")
 return matchup(opponent,market)

@router.get("/injuries")
def injury_report():
 def fetch():
  data=espn("injuries")
  if not isinstance(data,dict):return {"available":False,"players":[],"source":"ESPN","note":"No verified injury response"}
  entries=data.get("injuries") or []
  if not isinstance(entries,list):entries=[]
  return {"available":bool(entries),"players":entries[:200],"source":"ESPN","note":"Informational injury reports; no automatic projection adjustment without verified player/status mapping"}
 try:return cached("nfl_injuries",900,fetch)
 except Exception:return {"available":False,"players":[],"source":"ESPN","note":"Verified injury feed unavailable; no injury adjustment applied"}

@router.get("/context/coverage")
def nfl_context_coverage():
 from nfl_context import records,categories
 rows=records()
 fields={name:sum(name in r for r in rows) for group in categories().values() for name in group}
 return {"records":len(rows),"metrics":fields,"categories":categories(),
         "note":"Metrics with zero verified observations are not used in projections; coverage is not predictive validation."}
