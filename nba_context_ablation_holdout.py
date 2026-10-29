"""NBA pregame-context ablation with strictly chronological validation.

Historical opponent team allowances, recent minutes trend and player stat
rates are observed box-score proxies. They are NOT injury, confirmed lineup,
possessions, shot-attempt, or usage data. Research-only; no live changes.
"""
import json
from pathlib import Path
from collections import defaultdict
from nba_challenger_model import load,predict,MARKETS
from nba_matchup_features import load_team_games,prior_allowances
ROOT=Path(__file__).resolve().parent
PATHS=[str(ROOT/"data"/f"propedge_historical_{s}_minutes.csv") for s in ("2023_24","2024_25","2025_26")]+[str(ROOT/"data/nba_2026_27.csv")]
def avg(v):return sum(v)/len(v)
def mae(rows,k):return round(avg([abs(r["pred"][k]-r["actual"]) for r in rows]),4) if rows else None
def run():
    histories=load(PATHS);teams=load_team_games(PATHS)
    pair=defaultdict(set)
    for player,rows in histories.items():
        for r in rows:
            if r.get("team") and r.get("game_id"):
                pair[(r["date"],r["game_id"])].add(r["team"])
    # League reference built only from historical team game totals before each date.
    league_events=sorted([(r["date"],r["allowed"]) for rows in teams.values() for r in rows],key=lambda x:x[0])
    examples=[]
    for player,rows in histories.items():
        for i,target in enumerate(rows):
            day=target["date"];prior=[r for r in rows[:i] if r["date"]<day]
            if len(prior)<12:continue
            base=predict(prior)
            if base is None:continue
            opponents=pair.get((day,target.get("game_id")),set())-{target.get("team")}
            if len(opponents)!=1:continue
            opp=next(iter(opponents))
            defense=prior_allowances(teams,opp,day)
            league=[tot for d,tot in league_events if d<day][-600:]
            if defense is None or len(league)<100:continue
            recent=[r for r in prior[-12:] if r.get("minutes") is not None and r["minutes"]>=8]
            if len(recent)<8:continue
            last5=recent[-5:]
            minute_delta=avg([r["minutes"] for r in last5])-avg([r["minutes"] for r in recent])
            # Base candidate: established challenger. Context candidate: moderate
            # shrinkage adjustment from strictly earlier opponent team allowances.
            candidates={"challenger":base}
            for strength in (.15,.30,.45):
                result={}
                for j,m in enumerate(MARKETS[:4]):
                    league_mean=avg([v[j] for v in league])
                    ratio=max(.8,min(1.2,defense[m]/max(1.,league_mean)))
                    result[m]=max(0.,base[m]*(1+strength*(ratio-1)))
                result["PRA"]=sum(result[m] for m in ("PTS","REB","AST"))
                candidates["defense_"+str(strength)]=result
            # A role-trend candidate, distinct from defensive context.
            rate={m:sum(r[m] for r in recent)/sum(r["minutes"] for r in recent) for m in MARKETS[:4]}
            for strength in (.25,.5):
                result={m:max(0,base[m]+strength*minute_delta*rate[m]) for m in MARKETS[:4]}
                result["PRA"]=sum(result[m] for m in ("PTS","REB","AST"))
                candidates["role_"+str(strength)]=result
            for m in MARKETS:
                examples.append({"date":day,"market":m,"actual":target[m],
                                 "pred":{k:v[m] for k,v in candidates.items()}})
    dates=sorted({r["date"] for r in examples})
    if len(dates)<30:raise SystemExit("Insufficient distinct dates")
    c1=dates[int(len(dates)*.6)];c2=dates[int(len(dates)*.8)]
    val=[r for r in examples if c1<=r["date"]<c2]
    test=[r for r in examples if r["date"]>=c2]
    choices=("challenger","defense_0.15","defense_0.3","defense_0.45","role_0.25","role_0.5")
    markets={}
    for m in MARKETS:
        v=[r for r in val if r["market"]==m];t=[r for r in test if r["market"]==m]
        selected=min(choices,key=lambda k:mae(v,k))
        markets[m]={"validation_n":len(v),"test_n":len(t),"selected_on_validation":selected,
                    "validation_mae":{k:mae(v,k) for k in choices},
                    "test_mae":{k:mae(t,k) for k in choices},
                    "selected_test_mae":mae(t,selected),
                    "challenger_test_mae":mae(t,"challenger"),
                    "improvement":round(mae(t,"challenger")-mae(t,selected),4)}
    report={"status":"RESEARCH_ONLY_NOT_PROMOTED",
            "split":{"validation_from":c1,"holdout_from":c2},
            "validation_examples":len(val),"holdout_examples":len(test),"markets":markets,
            "limitations":["Opponent defense is historical team boxscore allowance, not possession-adjusted defensive rating.",
              "Role is historical minutes trend, not confirmed starter status or lineup.",
              "No shot attempts, actual usage, injury reports, verified pregame lineups, or travel.",
              "Only completed games prior to target date enter forecast features; opponent identity is known pregame.",
              "Selection on validation only; final holdout not used for tuning.",
              "Not matched against production consensus or sportsbook odds; no ROI proof.",
              "No NBA or NFL live projection changes."]}
    out=ROOT/"reports/nba_context_ablation_holdout.json";out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))
if __name__=="__main__":run()
