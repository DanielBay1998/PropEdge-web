"""Chronological, same-player NBA distribution model versus existing challenger.

Only prior games can enter a prediction. Historical game lines are not sportsbook
quotes: this evaluates projection MAE and synthetic half-point line calibration,
NOT betting ROI. Never promotes production automatically.
"""
import json
from pathlib import Path
from nba_challenger_model import load, predict, MARKETS
from nba_distribution_engine import project

def run(paths):
    histories=load(paths)
    rows=[]
    for player,games in histories.items():
        for target in games:
            prior=[r for r in games if r["date"]<target["date"]]
            if len(prior)<12:continue
            old=predict(prior)
            new=project(prior)
            if old is None or new is None:continue
            for market in MARKETS:
                rows.append((target["date"],market,float(target[market]),float(old[market]),float(new["projections"][market])))
    dates=sorted({r[0] for r in rows})
    if len(dates)<30:
        return {"status":"INSUFFICIENT_DATES","dates":len(dates)}
    split=dates[int(len(dates)*.8)]
    test=[r for r in rows if r[0]>=split]
    markets={}
    for market in MARKETS:
        rs=[r for r in test if r[1]==market]
        if not rs:continue
        old_mae=sum(abs(r[3]-r[2]) for r in rs)/len(rs)
        new_mae=sum(abs(r[4]-r[2]) for r in rs)/len(rs)
        markets[market]={"n":len(rs),"challenger_mae":round(old_mae,4),
                         "distribution_mae":round(new_mae,4),
                         "delta_mae_new_minus_old":round(new_mae-old_mae,4),
                         "distribution_better":new_mae<old_mae}
    return {"status":"RESEARCH_ONLY_NOT_PRODUCTION","holdout_start":split,
            "holdout_dates":len([d for d in dates if d>=split]),
            "markets":markets,
            "limitations":["No sportsbook prices, captured lines, injuries, lineup, opponent pace or usage inputs",
                           "Chronological same-player holdout; no simulated win probability calibration",
                           "No production model promotion or ROI claim"]}

if __name__=="__main__":
    paths=[f"data/propedge_historical_{s}_minutes.csv" for s in ("2023_24","2024_25","2025_26")]
    paths.append("data/nba_2026_27.csv")
    result=run(paths)
    path=Path("reports/nba_distribution_chronological_holdout.json")
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(result,indent=2))
