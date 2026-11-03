"""Leakage-safe, chronological NBA extended-market evaluation and current projections.
Only prior games contribute to each prediction. No claim of betting profitability.
"""
import csv, json, math, statistics
from collections import defaultdict
from datetime import date
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"data"/"nba_extended_gamelogs.csv"
REPORT=ROOT/"data"/"nba_extended_backtest.json"
PROJECTIONS=ROOT/"data"/"nba_extended_projections.csv"
# Regular season openers; exclude October preseason box scores.
START={"2025-26":"2025-10-21","2026-27":"2026-10-20"}
MARKETS=("FGA","FGM","2PA","2PM","FTA","FTM","TOV","STL","BLK","OREB","DREB")
def value(row,k):
    try:
        n=float(row[k])
        return n if math.isfinite(n) and n>=0 else None
    except (KeyError,ValueError,TypeError):return None
def predict(history,market):
    # Last 10 eligible appearances, favor recent games and normalize by minutes.
    eligible=[(value(r,market),value(r,"MIN")) for r in history[-10:]]
    eligible=[(v,m) for v,m in eligible if v is not None and m is not None and 5<=m<=60]
    if len(eligible)<5:return None
    recent_minutes=statistics.median(m for _,m in eligible[-5:])
    weights=list(range(1,len(eligible)+1))
    weighted_rate=sum(w*v/m for w,(v,m) in zip(weights,eligible))/sum(weights)
    return max(0,weighted_rate*recent_minutes)
def main():
    if not SOURCE.exists():raise RuntimeError("Extended game log missing; run backfill first")
    by_player=defaultdict(list)
    with SOURCE.open(newline="",encoding="utf-8") as f:
        for r in csv.DictReader(f):
            season=r.get("SEASON","")
            if season not in START or r.get("GAME_DATE","")<START[season]:continue
            if not r.get("PLAYER_ID") or not r.get("GAME_ID"):continue
            if value(r,"MIN") is None:continue
            by_player[(season,r["PLAYER_ID"])].append(r)
    errors=defaultdict(list)
    projections=[]
    for (season,pid),rows in by_player.items():
        rows.sort(key=lambda r:(r["GAME_DATE"],r["GAME_ID"]))
        # Strict chronological evaluation: predictions made before each game's result.
        history=[]
        for r in rows:
            for market in MARKETS:
                estimate=predict(history,market)
                actual=value(r,market)
                if estimate is not None and actual is not None:
                    errors[market].append((estimate-actual,abs(estimate-actual)))
            history.append(r)
        latest=rows[-1]
        for market in MARKETS:
            estimate=predict(history,market)
            if estimate is not None:
                projections.append({"season":season,"player_id":pid,"player":latest["PLAYER_NAME"],
                                    "team":latest["TEAM_ABBREVIATION"],"market":market,
                                    "projection":round(estimate,3),"games":len(rows),
                                    "last_game":latest["GAME_DATE"],"model":"EXTENDED_RESEARCH_V1"})
    if not projections:raise RuntimeError("No extended projections; refusing empty output")
    report={"model":"EXTENDED_RESEARCH_V1","method":"chronological prior-game minutes-normalized weighted rates",
            "regular_season_starts":START,"eligible_player_seasons":len(by_player),
            "projections":len(projections),
            "markets":{k:{"holdout_predictions":len(v),
                         "mae":round(statistics.mean(x[1] for x in v),3) if v else None,
                         "bias":round(statistics.mean(x[0] for x in v),3) if v else None}
                       for k,v in errors.items()},
            "warning":"Research only. No PrizePicks lines, hit rates, or ROI validated."}
    REPORT.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    with PROJECTIONS.open("w",newline="",encoding="utf-8") as f:
        writer=csv.DictWriter(f,fieldnames=["season","player_id","player","team","market","projection","games","last_game","model"])
        writer.writeheader()
        writer.writerows(projections)
    print(json.dumps(report,indent=2))
if __name__=="__main__":main()
