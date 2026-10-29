"""Prospective shadow selection: immutable snapshots of live NBA signals.
Research only; does not change published picks or grading.
"""
import datetime,json,os,urllib.request,urllib.error,time,math
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DEST=ROOT/"data"/"nba_v4_shadow_selections.json"
SITE=os.getenv("PROPEDGE_SITE","https://propedge-custom.onrender.com").rstrip("/")
def run():
    existing=json.loads(DEST.read_text()) if DEST.exists() else []
    seen={r["key"] for r in existing}
    req=urllib.request.Request(SITE+"/api/board",headers={"User-Agent":"PropEdge-Prospective-Shadow/1.0","Cache-Control":"no-cache"})
    board=None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req,timeout=25) as resp:
                board=json.load(resp)
            break
        except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError,ValueError) as exc:
            print('Board temporarily unavailable',type(exc).__name__,'attempt',attempt+1,flush=True)
            if attempt<2:time.sleep(8*(attempt+1))
    if board is None:
        print('SKIPPED: board unreachable; no snapshots written',flush=True)
        return
    if not isinstance(board,dict) or not board.get("live") or board.get("error"):
        print("No live verified board; no new shadow selections")
        return
    now=datetime.datetime.now(datetime.timezone.utc).isoformat()
    added=0
    for signal in board.get("signals",[]):
        if signal.get("identity_verified_by_current_season_logs") is not True:continue
        market=str(signal.get("market") or "")
        if market not in ("PTS","REB","AST","3PM","PRA"):continue
        side=str(signal.get("side") or "")
        if side not in ("OVER","UNDER"):continue
        try:
            line=float(signal["line"]);projection=float(signal["projection"]);odds=float(signal["odds"])
        except (ValueError,TypeError,KeyError):continue
        event=str(signal.get("event_id") or "")
        player=str(signal.get("player") or "")
        if not event or not player or odds==0 or not all(map(math.isfinite,(line,projection,odds))):continue
        key="|".join((now[:10],event,player.casefold(),market,side,str(line)))
        if key in seen:continue
        seen.add(key)
        edge=(projection-line)*(1 if side=="OVER" else -1)
        existing.append({"key":key,"captured_at":now,"event_id":event,"player":player,
                         "game":signal.get("game"),"market":market,"side":side,
                         "line":line,"projection":projection,"odds":odds,"book":signal.get("book"),
                         "model_version":signal.get("model_version") or "live_primary",
                         "research_group":"assists_pra" if market in ("AST","PRA") else "other_market",
                         "edge":round(edge,4),"status":"pending"})
        added+=1
    DEST.parent.mkdir(exist_ok=True)
    DEST.write_text(json.dumps(existing,indent=2)+"\n")
    print(json.dumps({"new_snapshots":added,"total":len(existing),"path":str(DEST)}))
if __name__=="__main__":run()
