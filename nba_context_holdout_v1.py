"""Leakage-safe NBA contextual adjustment research.

Train only on strictly earlier games; keep missing features out of predictions.
Uses available completed-game team context and chronological holdout.
"""
import csv,json,math,statistics
from collections import defaultdict
from pathlib import Path
from nba_challenger_model import load,predict
ROOT=Path(__file__).resolve().parent
PATHS=[str(ROOT/"data"/("propedge_historical_"+s+"_minutes.csv")) for s in ("2023_24","2024_25","2025_26")]+[str(ROOT/"data/nba_2026_27.csv")]
MARKETS=("PTS","REB","AST","3PM","PRA")
def context(rows,day,team):
    previous=[r for r in rows if r["date"]<day]
    previous.sort(key=lambda r:r["date"])
    if not previous:return {}
    import datetime
    d=lambda s:datetime.datetime.strptime(s,"%Y%m%d").date()
    rest=max(0,(d(day)-d(previous[-1]["date"])).days-1)
    return {"rest":min(rest,5),"b2b":float(rest==0)}
def build():
    histories=load(PATHS)
    observations=defaultdict(list)
    for name,history in histories.items():
        if len(history)<16:continue
        for i in range(12,len(history)):
            target=history[i]
            day=target["date"]
            prior=[r for r in history[:i] if r["date"]<day]
            if len(prior)<12:continue
            baseline=predict(prior)
            if baseline is None:continue
            x=context(prior,day,target.get("team"))
            if not x:continue
            for m in MARKETS:
                observations[m].append((day,baseline[m],target[m],x))
    return observations
def fit(train):
    # Ridge correction with intercept; features centered at neutral values.
    import math
    cols=("rest","b2b")
    if len(train)<150:return None
    means={k:sum(r[3][k] for r in train)/len(train) for k in cols}
    a=[[0.0]*3 for _ in range(3)]
    b=[0.0]*3
    for _,baseline,actual,ctx in train:
        x=[1.0]+[ctx[k]-means[k] for k in cols]
        y=actual-baseline
        for j in range(3):
            b[j]+=x[j]*y
            for k in range(3):a[j][k]+=x[j]*x[k]
    for i in (1,2):a[i][i]+=200.0
    for i in range(3):
        pivot=max(range(i,3),key=lambda j:abs(a[j][i]))
        a[i],a[pivot]=a[pivot],a[i];b[i],b[pivot]=b[pivot],b[i]
        if abs(a[i][i])<1e-8:return None
        scale=a[i][i]
        for j in range(i,3):a[i][j]/=scale
        b[i]/=scale
        for k in range(3):
            if k==i:continue
            scale=a[k][i]
            for j in range(i,3):a[k][j]-=scale*a[i][j]
            b[k]-=scale*b[i]
    return {"intercept":b[0],"weights":dict(zip(cols,b[1:])),"means":means}
def evaluate():
    results={}
    for market,rows in build().items():
        dates=sorted(set(r[0] for r in rows))
        if len(dates)<20:
            results[market]={"status":"insufficient_dates","n":len(rows)};continue
        cutoff=dates[int(len(dates)*.7)]
        train=[r for r in rows if r[0]<cutoff]
        test=[r for r in rows if r[0]>=cutoff]
        model=fit(train)
        if not model:
            results[market]={"status":"insufficient_training","n":len(train)};continue
        def adjusted(r):
            correction=model["intercept"]+sum(model["weights"][k]*(r[3][k]-model["means"][k]) for k in model["weights"])
            return max(0,r[1]+correction)
        base=sum(abs(r[1]-r[2]) for r in test)/len(test)
        new=sum(abs(adjusted(r)-r[2]) for r in test)/len(test)
        results[market]={"status":"holdout_evaluated","train_n":len(train),"holdout_n":len(test),
                         "holdout_start":cutoff,"baseline_mae":round(base,4),
                         "adjusted_mae":round(new,4),"improved":new<base,
                         "weights":model,"production_promoted":False}
    out={"model":"context_rest_back_to_back_v1","research_only":True,
         "limitations":["Not all requested features are present in source data",
                        "No forward-looking team pace, possessions, usage, travel, or betting totals",
                        "No production projections modified"],"markets":results}
    path=ROOT/"reports/nba_context_holdout.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out,indent=2)+"\n")
    print(json.dumps({k:{"status":v["status"],"improved":v.get("improved")} for k,v in results.items()}))
if __name__=="__main__":evaluate()
