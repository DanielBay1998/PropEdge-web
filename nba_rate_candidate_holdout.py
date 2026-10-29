"""Research-only NBA per-minute rate candidate with chronological validation.

Uses strictly prior player games. No verified injury, usage possession, shot
attempt, or opponent data in this experiment; do not imply otherwise.
"""
import json,math
from pathlib import Path
from nba_challenger_model import load,predict,MARKETS
ROOT=Path(__file__).resolve().parent
PATHS=[str(ROOT/"data"/f"propedge_historical_{s}_minutes.csv") for s in ("2023_24","2024_25","2025_26")]+[str(ROOT/"data/nba_2026_27.csv")]
def mean(xs):return sum(xs)/len(xs)
def candidates(prior):
    base=predict(prior)
    valid=[r for r in prior[-30:] if r.get("minutes") is not None and r["minutes"]>=8]
    if base is None or len(valid)<8:return None
    recent=valid[-5:];middle=valid[-12:]
    pm=max(8,min(42,.7*mean([r["minutes"] for r in recent])+.3*mean([r["minutes"] for r in valid])))
    out={"challenger":base}
    for weight in (0.25,0.5,0.75):
        p={}
        for m in MARKETS[:4]:
            rate5=sum(r[m] for r in recent)/sum(r["minutes"] for r in recent)
            rate12=sum(r[m] for r in middle)/sum(r["minutes"] for r in middle)
            rate30=sum(r[m] for r in valid)/sum(r["minutes"] for r in valid)
            # Shrink short-term scoring-rate changes toward longer history.
            adjusted=weight*rate5+(1-weight)*(.6*rate12+.4*rate30)
            p[m]=max(0,pm*adjusted)
        p["PRA"]=p["PTS"]+p["REB"]+p["AST"]
        out["rate_"+str(weight)]=p
    return out
def mae(rows,key):return round(mean([abs(r["predictions"][key]-r["actual"]) for r in rows]),4) if rows else None
def run():
    histories=load(PATHS);examples=[]
    for player,rows in histories.items():
        for i in range(8,len(rows)):
            day=rows[i]["date"];prior=[r for r in rows[:i] if r["date"]<day]
            p=candidates(prior)
            if p is None:continue
            for m in MARKETS:
                examples.append({"date":day,"player":player,"market":m,
                                 "actual":rows[i][m],"predictions":{k:v[m] for k,v in p.items()}})
    dates=sorted({r["date"] for r in examples})
    if len(dates)<30:raise SystemExit("Insufficient distinct dates for chronological split")
    cut1=dates[int(.6*len(dates))];cut2=dates[int(.8*len(dates))]
    train=[r for r in examples if r["date"]<cut1]
    val=[r for r in examples if cut1<=r["date"]<cut2]
    test=[r for r in examples if r["date"]>=cut2]
    result={}
    for m in MARKETS:
        v=[r for r in val if r["market"]==m]
        t=[r for r in test if r["market"]==m]
        choices=("challenger","rate_0.25","rate_0.5","rate_0.75")
        selected=min(choices,key=lambda key:mae(v,key))
        result[m]={"validation_n":len(v),"test_n":len(t),"selected_on_validation":selected,
                   "validation_mae":{k:mae(v,k) for k in choices},
                   "holdout_mae":{k:mae(t,k) for k in choices},
                   "selected_holdout_mae":mae(t,selected),
                   "challenger_holdout_mae":mae(t,"challenger"),
                   "holdout_improvement":round(mae(t,"challenger")-mae(t,selected),4)}
    report={"status":"RESEARCH_ONLY_NO_PROMOTION","split":{"train_before":cut1,"validation_before":cut2,"test_from":cut2},
            "training_examples":len(train),"validation_examples":len(val),"test_examples":len(test),
            "markets":result,"limitations":[
              "Only historical per-minute scoring rates and minutes; shot volume, possession usage, confirmed injuries, and matchup defense are not available in this experiment.",
              "Validation selects candidate; holdout is never used for selection.",
              "This is not a head-to-head against the three-model production consensus or captured odds.",
              "No betting ROI claim and no changes to live NBA/NFL models."]}
    out=ROOT/"reports/nba_rate_candidate_holdout.json";out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))
if __name__=="__main__":run()
