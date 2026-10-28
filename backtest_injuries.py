"""Leakage-safe, retrospective teammate-absence scenario audit.

Usage: python backtest_injuries.py
Uses local completed-game logs only. This is a research diagnostic, NOT
proof of an injury model: missing teammate rows are only an absence proxy,
and the baseline is a trailing average rather than the deployed model.
"""
import csv, json, math
from pathlib import Path
from collections import defaultdict

ROOT=Path(__file__).resolve().parent
MIN_HISTORY=10
MIN_WITH=5
MIN_WITHOUT=5
MARKETS=("PTS","REB","AST","3PM","PRA")

def read_logs():
    data=defaultdict(list)
    for filename in ("propedge_historical_2025_26_minutes.csv","nba_2026_27.csv"):
        path=ROOT/"data"/filename
        if not path.is_file():continue
        with path.open(encoding="utf-8-sig",newline="") as f:
            for r in csv.DictReader(f):
                date=str(r.get("Date") or "")
                if len(date)!=8 or not date.isdigit():continue
                if filename.startswith("nba_") and date<"20261020":continue
                if filename.startswith("propedge_") and str(r.get("Season Type") or "").lower()!="regular":continue
                try:stats={k:float(r[k]) for k in ("PTS","REB","AST","3PM")}
                except (ValueError,KeyError,TypeError):continue
                if not all(math.isfinite(v) and v>=0 for v in stats.values()):continue
                raw_min=str(r.get("MIN") or "").strip()
                try:
                    minutes=float(raw_min.split(":")[0])+float(raw_min.split(":")[1])/60 if ":" in raw_min else float(raw_min)
                    if not math.isfinite(minutes) or minutes<=0:minutes=None
                except (ValueError,TypeError,IndexError):
                    minutes=None
                stats["MIN"]=minutes
                key=(date,str(r.get("Game ID") or ""),str(r.get("Team") or "").lower().strip())
                if not key[1] or not key[2]:continue
                data[key].append((str(r.get("Player") or "").lower().strip(),stats))
    return data

def evaluate(data):
    by_team=defaultdict(list)
    for (date,gid,team),players in data.items():
        by_team[team].append((date,gid,{p:stats for p,stats in players if p}))
    summary=defaultdict(lambda:{"n":0,"base_abs":0.0,"adjusted_abs":0.0,"base_sq":0.0,"adjusted_sq":0.0})
    examples=[]
    for team,games in by_team.items():
        games.sort(key=lambda x:(x[0],x[1]))
        for idx,(date,gid,roster) in enumerate(games):
            prior=games[:idx]
            if len(prior)<MIN_HISTORY:continue
            # Restrict candidates to players observed repeatedly in the past.
            appearances=defaultdict(int)
            for _,_,past in prior:
                for p in past:appearances[p]+=1
            for player,actual in roster.items():
                history=[(d,g,past) for d,g,past in prior if player in past]
                if len(history)<MIN_HISTORY:continue
                # Only evaluate a teammate absent from the target game.
                absent=[mate for mate,n in appearances.items()
                        if mate!=player and mate not in roster and n>=MIN_WITH]
                if not absent:continue
                for market in MARKETS:
                    def stat(stats):
                        return sum(stats[k] for k in ("PTS","REB","AST")) if market=="PRA" else stats[market]
                    baseline=sum(stat(past[player]) for _,_,past in history[-10:])/10
                    if baseline<=0:continue
                    candidates=[]
                    for mate in absent:
                        on=[stat(past[player]) for _,_,past in history if mate in past]
                        off=[stat(past[player]) for _,_,past in history if mate not in past]
                        if len(on)<MIN_WITH or len(off)<MIN_WITHOUT:continue
                        on_mean=sum(on)/len(on)
                        if on_mean<=0.5:continue
                        observed=(sum(off)/len(off))/on_mean-1
                        candidates.append((min(len(on),len(off)),mate,observed))
                    if not candidates:continue
                    _,mate,observed=max(candidates,key=lambda x:x[0])
                    change=max(-0.15,min(0.15,observed*0.35))
                    adjusted=max(0,baseline*(1+change))
                    y=stat(actual)
                    row=summary[market]
                    row["n"]+=1
                    row["base_abs"]+=abs(y-baseline)
                    row["adjusted_abs"]+=abs(y-adjusted)
                    row["base_sq"]+=(y-baseline)**2
                    row["adjusted_sq"]+=(y-adjusted)**2
                    if len(examples)<12:
                        examples.append({"date":date,"player":player,"teammate_absence_proxy":mate,
                                         "market":market,"actual":y,"baseline":round(baseline,2),
                                         "adjusted":round(adjusted,2)})
    results={}
    for market,r in summary.items():
        n=r["n"]
        results[market]={"samples":n,"baseline_mae":round(r["base_abs"]/n,3),
                         "scenario_mae":round(r["adjusted_abs"]/n,3),
                         "baseline_rmse":round(math.sqrt(r["base_sq"]/n),3),
                         "scenario_rmse":round(math.sqrt(r["adjusted_sq"]/n),3),
                         "mae_change_pct":round(100*(r["adjusted_abs"]/r["base_abs"]-1),2) if r["base_abs"] else None}
    return {"method":"Walk-forward chronological audit; prior games only; 10-game trailing baseline",
            "limitations":["Teammate missing from box score is an absence proxy, not a verified injury designation.",
                           "Incomplete game logs can create false absences.",
                           "Historical rolling average is NOT the production shadow projection.",
                           "Not an independent holdout or causal estimate; do not promote to qualifying bets."],
            "results":results,"examples":examples,
            "verdict":"RESEARCH ONLY: do not change official projections or qualification."}

if __name__=="__main__":
    print(json.dumps(evaluate(read_logs()),indent=2))
