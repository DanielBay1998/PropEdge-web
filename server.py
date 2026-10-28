from pathlib import Path
import os,csv,math,time,threading,json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from datetime import datetime,timezone
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

ROOT=Path(__file__).resolve().parent
app=FastAPI(title="PropEdge Live Validated")
app.mount("/static",StaticFiles(directory=str(ROOT/"static")),name="static")
MARKETS={"player_points":"PTS","player_rebounds":"REB","player_assists":"AST","player_threes":"3PM","player_points_rebounds_assists":"PRA"}
_cache={"until":0,"result":None};_lock=threading.Lock()

def number(value):
    try:
        v=float(value)
        return v if math.isfinite(v) else None
    except (TypeError,ValueError):return None

def norm(s):return " ".join(str(s or "").casefold().split())

def projections():
    p=ROOT/"data"/"propedge_shadow_projections.csv"
    if not p.exists():return {}
    with p.open(encoding="utf-8-sig",newline="") as f: rows=list(csv.DictReader(f))
    result={}
    for r in rows:
        if r.get("MinutesModelStatus")!="MODELED":continue
        n=number(r.get("ShadowProjection"))
        if n is None or n<0:continue
        result[(norm(r.get("ResolvedPlayer")),r.get("MarketCode"))]=r
    return result


# Historical statistics are descriptive, not calibrated win probabilities.
# The current-season source has no season-type or minutes field. To avoid
# mixing preseason with regular-season stats, include only dates from
# the first known regular-season week (Oct 20, 2026 onward).
from functools import lru_cache

@lru_cache(maxsize=1)
def load_game_logs():
    logs=defaultdict(list)
    sources=[(ROOT/'data'/'nba_2026_27.csv','current'),
             (ROOT/'data'/'propedge_historical_2025_26_minutes.csv','historical')]
    for path,season in sources:
        if not path.exists():continue
        with path.open(encoding='utf-8-sig',newline='') as handle:
            for row in csv.DictReader(handle):
                player=norm(row.get('Player'))
                date=str(row.get('Date') or '').strip()
                if not player or len(date)!=8 or not date.isdigit():continue
                if season=='current' and date<'20261020':continue
                if season=='historical' and norm(row.get('Season Type'))!='regular':continue
                values={k:number(row.get(k)) for k in ('PTS','REB','AST','3PM','MIN')}
                if any(values[k] is None or values[k]<0 for k in ('PTS','REB','AST','3PM')):continue
                logs[player].append({'date':date,'game_id':str(row.get('Game ID') or ''),
                                     'team':row.get('Team') or '', 'season':season, **values})
    for rows in logs.values():
        # Most recent first; ignore duplicated player/game rows across sources.
        rows.sort(key=lambda r:(r['date'],r['game_id']),reverse=True)
        seen=set();rows[:]=[r for r in rows if not ((r['game_id'],r['date']) in seen or seen.add((r['game_id'],r['date'])))]
    return logs

def add_game_analysis(signal,logs):
    rows=logs.get(norm(signal.get('player')),[])
    code=signal['market'];line=signal['line'];side=signal['side']
    def val(row):
        return sum(row[k] for k in ('PTS','REB','AST')) if code=='PRA' else row.get(code)
    def summarize(items):
        values=[val(r) for r in items if val(r) is not None]
        if not values:return {'games':0,'average':None,'hits':None,'hit_rate':None,'avg_minutes':None}
        hits=sum((v>line if side=='OVER' else v<line) for v in values)
        mins=[r['MIN'] for r in items if r['MIN'] is not None]
        return {'games':len(values),'average':round(sum(values)/len(values),2),
                'hits':hits,'hit_rate':round(100*hits/len(values),1),
                'avg_minutes':round(sum(mins)/len(mins),1) if mins else None}
    current=[r for r in rows if r['season']=='current']
    historical=[r for r in rows if r['season']=='historical']
    # Keep current-season and previous-season samples separate and explicit.
    signal['analysis']={
        'current':summarize(current),
        'previous_season':summarize(historical),
        'last5':summarize(rows[:5]),'last10':summarize(rows[:10]),'last20':summarize(rows[:20]),
        'recent_games':[{'date':r['date'],'season':r['season'],'stat':val(r),'minutes':r['MIN']}
                        for r in rows[:10]],
        'sample_warning':('No regular-season current-year game logs available.' if not current else
                          'Only '+str(len(current))+' current-season regular-season games available.' if len(current)<5 else None),
        'minutes_warning':'Current-season source does not include minutes; displayed minute averages use historical records only.',
        'note':'Historical hit rates are descriptive and not estimated probabilities or EV.'
    }
    return signal

def refresh():
    now=datetime.now(timezone.utc).isoformat()
    base={"signals":[],"live":False,"verified":False,"as_of":now,"players":0,"quotes":0,"error":None}
    key=os.getenv("PROPLINE_API_KEY","").strip()
    if not key:
        base["error"]="PROPLINE_API_KEY not configured in Render environment. No live picks shown."
        return base
    try:
        from propline import PropLine
        client=PropLine(key)
        events=client.get_events("basketball_nba")
        model=projections();base["players"]=len({p for p,m in model})
        candidates=defaultdict(list)
        upcoming=[]
        for event in events:
            eid=event.get("id")
            if not eid:continue
            # Avoid completed and in-progress events; use provider status where available.
            status=norm(event.get("status"))
            if status in {"final","completed","live","in_progress","in progress","closed"}:continue
            commence=event.get("commence_time") or event.get("start_time")
            if commence:
                try:
                    dt=datetime.fromisoformat(str(commence).replace("Z","+00:00"))
                    if dt.tzinfo and dt<=datetime.now(timezone.utc):continue
                except ValueError:continue
            upcoming.append(event)
        # Bound the first-pass refresh to the next 8 eligible games.
        # Avoid requesting odds sequentially for an entire provider event archive.
        upcoming=sorted(upcoming,key=lambda e: str(e.get("commence_time") or e.get("start_time") or "9999"))[:8]
        base["events_checked"]=len(upcoming)
        def fetch(event):
            # Retry only the failed event once, rather than repeating all games.
            last_error=None
            for attempt in range(2):
                try:
                    sdk=PropLine(key)
                    return sdk.get_odds("basketball_nba",event_id=event["id"],markets=list(MARKETS))
                except Exception as exc:
                    last_error=exc
                    if attempt==0:time.sleep(1)
            raise last_error
        successful=0
        failures=[]
        failed_event_ids=[]
        with ThreadPoolExecutor(max_workers=3) as pool:
            tasks={pool.submit(fetch,event):event for event in upcoming}
            for task in as_completed(tasks):
                event=tasks[task]
                eid=event["id"]
                try:
                    odds=task.result()
                    successful+=1
                except Exception as exc:
                    failures.append(type(exc).__name__)
                    failed_event_ids.append(eid)
                    logging.warning("Live odds failed for event %s: %s",eid,type(exc).__name__)
                    continue
                game=f'{event.get("away_team", "Away")} @ {event.get("home_team", "Home")}'
                for book in odds.get("bookmakers",[]):
                    bk=norm(book.get("key"))
                    if not bk:continue
                    for market in book.get("markets",[]):
                        code=MARKETS.get(market.get("key"))
                        if not code or market.get("line_type")!="main":continue
                        period=norm(market.get("period"))
                        if period not in {"","full","full_game","game"}:continue
                        for o in market.get("outcomes",[]):
                            if norm(o.get("name")) not in {"over","under"}:continue
                            if bk=="prizepicks" and norm(o.get("dfs_odds_type"))!="standard":continue
                            if bk=="underdog" and number(o.get("payout_multiplier"))!=1.0:continue
                            if bk=="pick6" and norm(o.get("dfs_odds_type"))!="standard":continue
                            line=number(o.get("point")); player=norm(o.get("description"))
                            if line is None or line<0.5 or not player:continue
                            m=model.get((player,code))
                            if m is None:continue
                            proj=number(m.get("ShadowProjection"))
                            if proj is None:continue
                            side=norm(o.get("name"))
                            gap=proj-line if side=="over" else line-proj
                            if gap<=0:continue
                            candidates[(eid,player,code,side,line)].append((bk,o,game,m,proj,gap))
                            base["quotes"]+=1
        base["events_succeeded"]=successful
        base["events_failed"]=len(failures)
        base["failure_types"]=sorted(set(failures))
        base["failed_event_ids"]=failed_event_ids
        base["coverage"]="partial" if failures else "complete"
        if upcoming and successful==0:
            base["error"]="All live odds requests failed: "+", ".join(sorted(set(failures)))
            return base
        if not upcoming:
            base["error"]="No upcoming NBA games returned by the events feed."
            return base
        signals=[]
        for (eid,player,code,side,line),quotes in candidates.items():
            distinct={q[0] for q in quotes}
            # Two independent books must quote the exact same standard main line.
            # DFS platforms are not counted as independent sportsbook confirmations.
            sportsbooks=distinct-{"prizepicks","underdog","pick6"}
            if len(sportsbooks)<2:continue
            # Require a minimum absolute gap; do not call this EV/probability.
            q=next((q for q in quotes if q[0] in sportsbooks),quotes[0])
            bk,o,game,m,proj,gap=q
            if gap<max(0.5,0.08*max(line,1)):continue
            signals.append({"player":m.get("ResolvedPlayer"),"market":code,"side":side.upper(),"line":line,"projection":round(proj,2),"gap":round(gap,2),"book":bk,"books":sorted(distinct),"book_count":len(sportsbooks),"game":game,"odds":o.get("price"),"history":m.get("HistoricalGames"),"current":m.get("CurrentGames"),"minutes":m.get("ProjectedMinutes"),"source":"live main-line feed","event_id":eid,"verified":True,"coverage":"partial" if failures else "complete"})
        logs=load_game_logs()
        for signal in signals:add_game_analysis(signal,logs)
        signals.sort(key=lambda s:(s["book_count"],s["gap"]/max(s["line"],1)),reverse=True)
        base.update(signals=signals[:200],live=True,verified=(len(failures)==0))
        if failures:
            base["error"]=(f"Partial live feed: {successful} of {len(upcoming)} games succeeded. "
                           "Signals from successful games only; failed games excluded. "
                           "Coverage is incomplete.")
    except Exception as exc:
        # Fail closed: never substitute stale snapshots for live picks.
        base["error"]=f"Live odds unavailable ({type(exc).__name__}). No picks shown. Check Render logs and API limits."
    return base

@app.get("/api/board")
def board():
    with _lock:
        if time.monotonic()>=_cache["until"]:
            _cache["result"]=refresh()
            _cache["until"]=time.monotonic()+(30 if _cache["result"].get("error") else 300)
        return _cache["result"]


@app.get("/api/prizepicks")
def prizepicks_snapshot():
    """Separate research snapshot; never represent it as verified live PrizePicks odds."""
    path=ROOT/"data"/"phase7b_prizepicks_latest.json"
    if not path.exists():
        return {"lines":[],"verified":False,"live":False,"error":"PrizePicks research snapshot not uploaded to data/phase7b_prizepicks_latest.json"}
    try:
        payload=json.loads(path.read_text(encoding="utf-8-sig"))
        captured=datetime.fromisoformat(str(payload.get("generated_utc", "")).replace("Z","+00:00"))
        age=(datetime.now(timezone.utc)-captured).total_seconds()
        if age<0 or age>6*3600:
            return {"lines":[],"verified":False,"live":False,"as_of":payload.get("generated_utc"),"error":"PrizePicks research snapshot is older than 6 hours or has an invalid future timestamp; refresh the source before displaying."}
        lines=[]
        for row in payload.get("lines",[]):
            if not isinstance(row,dict) or row.get("source")!="Prop-Line / PrizePicks":continue
            line=number(row.get("line"));proj=number(row.get("minutes_projection"))
            if line is None or proj is None or line<=0:continue
            tip=row.get("tipoff_utc")
            try:
                if datetime.fromisoformat(str(tip).replace("Z","+00:00"))<=datetime.now(timezone.utc):continue
            except (TypeError,ValueError):continue
            # A matching event/team roster has not yet been independently verified.
            # Publish research comparisons only, not actionable verified picks.
            lines.append({"player":row.get("player"),"team":row.get("team"),"market":row.get("market"),"line":line,
                          "projection":round(proj,2),"direction":row.get("minutes_pick"),"tipoff_utc":tip,
                          "captured_utc":row.get("captured_utc"),"source":row.get("source"),
                          "verified":False,"research_only":True})
        return {"lines":lines,"count":len(lines),"live":False,"verified":False,"as_of":payload.get("generated_utc"),
                "warning":"Research-only Prop-Line / PrizePicks snapshot. Player/team mappings and availability are unverified. Not live odds or verified bets."}
    except (OSError,ValueError,TypeError,KeyError) as exc:
        return {"lines":[],"live":False,"verified":False,"error":f"Could not read PrizePicks snapshot ({type(exc).__name__})"}

@app.get("/healthz")
def health():return {"status":"ok"}

@app.get("/")
def home():return HTMLResponse((ROOT/"static"/"index.html").read_text(encoding="utf-8"))


# Optional player portraits from ESPN NBA team rosters. Failure never affects live picks.
_headshot_cache={"until":0,"headshots":{}}
@app.get("/api/headshots")
def headshots():
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor
    now=time.monotonic()
    if now<_headshot_cache["until"]:return {"headshots":_headshot_cache["headshots"]}
    team_ids=["atl","bos","bkn","cha","chi","cle","dal","den","det","gs","hou","ind","lac","lal","mem","mia","mil","min","no","ny","okc","orl","phi","phx","por","sac","sa","tor","utah","wsh"]
    def get_roster(team):
        url=f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team}/roster"
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"PropEdge/2.0"})
            with urllib.request.urlopen(req,timeout=3) as res: payload=json.load(res)
            athletes=payload.get("athletes",[])
            if isinstance(athletes,dict):athletes=list(athletes.values())
            output={}
            for a in athletes:
                if isinstance(a,dict) and "items" in a: entries=a["items"]
                elif isinstance(a,list):entries=a
                else:entries=[a]
                for p in entries:
                    if not isinstance(p,dict):continue
                    name=p.get("displayName") or p.get("fullName")
                    photo=(p.get("headshot") or {}).get("href")
                    if name and photo and photo.startswith("https://"):output[name.casefold()]=photo
            return output
        except Exception:return {}
    result={}
    with ThreadPoolExecutor(max_workers=12) as pool:
        for chunk in pool.map(get_roster,team_ids):result.update(chunk)
    _headshot_cache.update(until=time.monotonic()+21600,headshots=result)
    return {"headshots":result}


# Complete NBA scoreboard independent of the qualifying prop board.
_schedule_cache={"until":0,"date":None,"result":None}
@app.get("/api/games")
def nba_games(date: str = ""):
    import re
    from fastapi import HTTPException
    if date and not re.fullmatch(r"\d{8}",date):
        raise HTTPException(status_code=400,detail="Date must be YYYYMMDD")
    from zoneinfo import ZoneInfo
    from urllib.request import Request,urlopen
    from urllib.parse import urlencode
    today=datetime.now(ZoneInfo("America/Chicago")).strftime("%Y%m%d")
    requested=date or today
    from datetime import datetime as _dt
    if date:
        try:
            requested_day=_dt.strptime(date,"%Y%m%d").date()
            local_day=_dt.strptime(today,"%Y%m%d").date()
            if abs((requested_day-local_day).days)>7:raise ValueError("out of range")
        except ValueError:raise HTTPException(status_code=400,detail="Date outside supported seven-day range")
    now=time.monotonic()
    if _schedule_cache["date"]==requested and now<_schedule_cache["until"]:
        return _schedule_cache["result"]
    url="https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?"+urlencode({"dates":requested,"limit":100})
    try:
        with urlopen(Request(url,headers={"User-Agent":"PropEdge/2.2"}),timeout=8) as response:
            payload=json.load(response)
        games=[]
        for event in payload.get("events",[]):
            comp=(event.get("competitions") or [{}])[0]
            teams=comp.get("competitors") or []
            away=next((x for x in teams if x.get("homeAway")=="away"),None)
            home=next((x for x in teams if x.get("homeAway")=="home"),None)
            if not away or not home:continue
            def team(t):
                x=t.get("team") or {}
                return {"name":x.get("displayName") or x.get("name") or "Unknown", "abbreviation":x.get("abbreviation") or "", "logo":x.get("logo") or (x.get("logos") or [{}])[0].get("href") or "", "score":t.get("score")}
            games.append({"id":event.get("id"),"date":event.get("date"),"away":team(away),"home":team(home),"status":((event.get("status") or {}).get("type") or {}).get("shortDetail") or "Scheduled", "state":((event.get("status") or {}).get("type") or {}).get("state") or "pre"})
        result={"date":requested,"games":games,"source":"ESPN NBA scoreboard","as_of":datetime.now(timezone.utc).isoformat()}
        _schedule_cache.update({"until":now+180,"date":requested,"result":result})
        return result
    except Exception as exc:
        # Never claim an incomplete model-signal list is the complete slate.
        return {"date":requested,"games":[],"error":"NBA schedule temporarily unavailable ("+type(exc).__name__+")","source":"ESPN NBA scoreboard"}
