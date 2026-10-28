"""Backfill NBA regular-season box scores from ESPN's public scoreboard/summary feeds.
Checkpointed CSV: safe to rerun; missing games are not interpreted as injuries.
"""
import csv, json, os, time, urllib.request, urllib.error
from datetime import date,timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DEST=ROOT/"data"/"propedge_historical_2025_26_minutes.csv"
START=date(2025,10,21)
END=date(2026,4,12)
FIELDS=["Game ID","Date","Player","Team","PTS","REB","AST","3PM","MIN","Season Type"]
BASE="https://site.api.espn.com/apis/site/v2/sports/basketball/nba/"
def get(url):
    for attempt in range(5):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 (compatible; PropEdgeResearch/1.0)"})
            with urllib.request.urlopen(req,timeout=25) as res:return json.load(res)
        except (urllib.error.URLError,TimeoutError,ValueError) as e:
            if attempt==4:raise RuntimeError(f"Failed {url}: {e}") from e
            time.sleep(2**attempt)
def existing():
    rows={}
    if DEST.exists():
        with DEST.open(encoding="utf-8-sig",newline="") as f:
            for r in csv.DictReader(f):
                if r.get("Game ID") and r.get("Player"):
                    rows[(r["Game ID"],r["Player"])]=r
    return rows
def save(rows):
    DEST.parent.mkdir(parents=True,exist_ok=True)
    tmp=DEST.with_suffix(".tmp")
    with tmp.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=FIELDS,extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows.values(),key=lambda r:(r["Date"],r["Game ID"],r["Player"])))
    os.replace(tmp,DEST)
def extract(summary,event_id,day):
    result=[]
    for team_block in summary.get("boxscore",{}).get("players",[]):
        team=(team_block.get("team") or {}).get("displayName") or ""
        for group in team_block.get("statistics",[]):
            labels=[str(x).upper() for x in group.get("labels",[])]
            for athlete in group.get("athletes",[]):
                player=(athlete.get("athlete") or {}).get("displayName")
                stats=athlete.get("stats") or []
                if not player or not stats or athlete.get("didNotPlay"):continue
                values=dict(zip(labels,stats))
                def numeric(*names):
                    for n in names:
                        if n in values:
                            try:return float(values[n])
                            except (ValueError,TypeError):pass
                    return None
                pts=numeric("PTS")
                reb=numeric("REB")
                ast=numeric("AST")
                threes=numeric("3PM")
                if threes is None:
                    try:threes=float(str(values.get("3PT","")).split("-")[0])
                    except (ValueError,TypeError):pass
                if None in (pts,reb,ast,threes):continue
                minutes=values.get("MIN","")
                result.append({"Game ID":str(event_id),"Date":day.strftime("%Y%m%d"),
                               "Player":player,"Team":team,"PTS":pts,"REB":reb,"AST":ast,
                               "3PM":threes,"MIN":minutes,"Season Type":"regular"})
    return result
def main():
    rows=existing()
    completed=set(r["Game ID"] for r in rows.values())
    current=START
    games=0
    while current<=END:
        day=current.strftime("%Y%m%d")
        payload=get(BASE+"scoreboard?dates="+day+"&limit=100")
        for event in payload.get("events",[]):
            eid=str(event.get("id") or "")
            if not eid or eid in completed:continue
            season=(event.get("season") or {}).get("type")
            if season is not None and str(season)!="2":continue
            if not (event.get("status") or {}).get("type",{}).get("completed"):continue
            summary=get(BASE+"summary?event="+eid)
            parsed=extract(summary,eid,current)
            if not parsed:continue
            for r in parsed:rows[(eid,r["Player"])]=r
            completed.add(eid)
            games+=1
            if games%25==0:
                save(rows)
                print(f"Checkpoint: {games} new games; {len(rows)} player rows",flush=True)
            time.sleep(.12)
        current+=timedelta(days=1)
    save(rows)
    print(f"Finished: {len(completed)} completed games; {len(rows)} player rows")
if __name__=="__main__":main()
