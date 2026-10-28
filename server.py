from pathlib import Path
import os,csv,math,time,threading
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
            # Use a separate SDK client per worker to avoid shared-client thread issues.
            sdk=PropLine(key)
            return sdk.get_odds("basketball_nba",event_id=event["id"],markets=list(MARKETS))
        successful=0
        failures=[]
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
            signals.append({"player":m.get("ResolvedPlayer"),"market":code,"side":side.upper(),"line":line,"projection":round(proj,2),"gap":round(gap,2),"book":bk,"books":sorted(distinct),"book_count":len(sportsbooks),"game":game,"odds":o.get("price"),"history":m.get("HistoricalGames"),"current":m.get("CurrentGames"),"minutes":m.get("ProjectedMinutes"),"source":"live main-line feed","event_id":eid})
        signals.sort(key=lambda s:(s["book_count"],s["gap"]/max(s["line"],1)),reverse=True)
        base.update(signals=signals[:200],live=True,verified=(len(failures)==0))
        if failures:
            base["signals"]=[]
            base["error"]=f"Partial live feed: {len(failures)} of {len(upcoming)} games failed. Signals withheld until complete."
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

@app.get("/healthz")
def health():return {"status":"ok"}

@app.get("/")
def home():return HTMLResponse((ROOT/"static"/"index.html").read_text(encoding="utf-8"))
