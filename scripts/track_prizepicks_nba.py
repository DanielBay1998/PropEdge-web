"""Archive observed PrizePicks NBA lines and grade against dated NBA box scores.
Fail closed: only real pregame board rows; no synthetic/historical PrizePicks lines.
"""
import csv,json,os,urllib.request
from datetime import datetime,timezone,timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
ARCHIVE=ROOT/"data"/"prizepicks_nba_tracking.json"
LOGS=ROOT/"data"/"nba_extended_gamelogs.csv"
BOARD=os.environ.get("PROPEDGE_BOARD_URL","https://propedge-custom.onrender.com/api/board")
def get_board():
    req=urllib.request.Request(BOARD,headers={"User-Agent":"PropEdge-PrizePicks-Tracker/1"})
    with urllib.request.urlopen(req,timeout=75) as resp:return json.load(resp)
def keyname(s):return "".join(c for c in str(s or "").casefold() if c.isalnum())
def grade(rows):
    if not LOGS.exists():return
    by={}
    with LOGS.open(newline="",encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("SEASON")!="2026-27":continue
            key=(r.get("GAME_DATE"),keyname(r.get("PLAYER_NAME")))
            by.setdefault(key,[]).append(r)
    for p in rows:
        if p.get("status")!="pending":continue
        # Event date is recorded from pregame capture's local date. Never assume
        # a subsequent-day game or assign ambiguous player/team matches.
        date=p.get("game_date")
        games=by.get((date,keyname(p.get("player"))),[])
        if len(games)!=1:continue
        game=games[0]
        market=p.get("market")
        if market not in game:continue
        try:actual=float(game[market]);line=float(p["line"])
        except (ValueError,TypeError):continue
        if actual<0:continue
        side=p.get("side")
        if side not in ("OVER","UNDER"):continue
        p["actual"]=actual
        p["status"]="push" if actual==line else ("win" if (actual>line)==(side=="OVER") else "loss")
        p["graded_at"]=datetime.now(timezone.utc).isoformat()
        p["grading_source"]="NBA extended box score, unique player/date"
def main():
    try:rows=json.loads(ARCHIVE.read_text()) if ARCHIVE.exists() else []
    except (OSError,ValueError):rows=[]
    if not isinstance(rows,list):raise RuntimeError("Tracking archive malformed")
    grade(rows)
    now=datetime.now(timezone.utc)
    try:
        board=get_board()
        if not board.get("live") or not board.get("verified"):
            raise RuntimeError("Live verified board unavailable; no capture")
        asof=datetime.fromisoformat(board["as_of"].replace("Z","+00:00"))
        if asof.tzinfo is None or abs((now-asof).total_seconds())>1200:
            raise RuntimeError("Board stale; no capture")
        local=now.astimezone(ZoneInfo("America/Chicago"))
        today=local.date().isoformat()
        existing={p["id"] for p in rows}
        added=0
        for r in board.get("game_props",[]):
            if not isinstance(r,dict):continue
            if not r.get("books") or "prizepicks" not in [str(x).casefold() for x in r["books"]]:continue
            if r.get("model_status") not in ("matched","extended_research"):continue
            if r.get("projection") is None or r.get("line") is None:continue
            if r.get("side") not in ("OVER","UNDER"):continue
            market=r.get("market")
            if market not in ("PTS","REB","AST","3PM","PRA","FGA","FGM","2PA","2PM","FTA","FTM","TOV","STL","BLK","OREB","DREB"):continue
            # Archive a single direction per player/line/model per event.
            ident="|".join(map(str,[r.get("event_id"),keyname(r.get("player")),market,r.get("side"),r.get("line"),r.get("projection_method") or "CORE"]))
            if ident in existing:continue
            rows.append({"id":ident,"captured_at":now.isoformat(),"game_date":today,
                         "event_id":str(r.get("event_id")),"game":r.get("game"),
                         "player":r.get("player"),"market":market,"side":r["side"],
                         "line":r["line"],"projection":r["projection"],"book":"prizepicks",
                         "model":r.get("projection_method") or "CORE",
                         "status":"pending","source":"verified live PropEdge PrizePicks board"})
            existing.add(ident);added+=1
        print("Captured",added,"new observed PrizePicks rows")
    except Exception as exc:
        print("Capture skipped:",type(exc).__name__,str(exc)[:250])
    ARCHIVE.parent.mkdir(parents=True,exist_ok=True)
    ARCHIVE.write_text(json.dumps(rows,indent=2)+"\n")
    print("Archive rows",len(rows),"graded",sum(r.get("status") in ("win","loss","push") for r in rows))
if __name__=="__main__":main()
