"""PropEdge 4.0: data-quality audit and regularized autoregressive NBA baseline.
Research-only. Chronological train/validation/test; no sportsbook or profit claims.
Uses standard library only and strictly prior player games.
"""
import json,math,statistics
from collections import Counter
from pathlib import Path
from nba_challenger_model import load,MARKETS,predict
ROOT=Path(__file__).resolve().parent
PATHS=[str(ROOT/"data"/f"propedge_historical_{s}_minutes.csv") for s in ("2023_24","2024_25","2025_26")]+[str(ROOT/"data/nba_2026_27.csv")]

def features(prior,m):
    last=prior[-5:]; long=prior[-20:]
    return [1.,sum(r[m] for r in last)/len(last),sum(r[m] for r in long)/len(long),
            sum(r[m] for r in prior[-10:])/len(prior[-10:]),
            sum(r["minutes"] for r in last if r["minutes"] is not None)/max(1,sum(r["minutes"] is not None for r in last))]

def solve(a,b):
    n=len(b)
    aug=[a[i][:]+[b[i]] for i in range(n)]
    for i in range(n):
        k=max(range(i,n),key=lambda j:abs(aug[j][i]))
        aug[i],aug[k]=aug[k],aug[i]
        if abs(aug[i][i])<1e-10:return None
        p=aug[i][i]
        for c in range(i,n+1):aug[i][c]/=p
        for j in range(n):
            if j==i:continue
            q=aug[j][i]
            for c in range(i,n+1):aug[j][c]-=q*aug[i][c]
    return [r[-1] for r in aug]

def fit(rows,lam):
    # Standardize using training only. Never peek at validation/test.
    n=len(rows[0][0])
    means=[0.]+[statistics.mean(x[j] for x,y in rows) for j in range(1,n)]
    scales=[1.]+[max(1e-6,statistics.pstdev(x[j] for x,y in rows)) for j in range(1,n)]
    a=[[0.]*n for _ in range(n)];b=[0.]*n
    for x,y in rows:
        z=[1.]+[(x[j]-means[j])/scales[j] for j in range(1,n)]
        for i in range(n):
            b[i]+=z[i]*y
            for j in range(n):a[i][j]+=z[i]*z[j]
    for i in range(1,n):a[i][i]+=lam*len(rows)
    w=solve(a,b)
    return (w,means,scales) if w else None

def estimate(model,x):
    w,mu,sd=model
    return max(0,w[0]+sum(w[j]*(x[j]-mu[j])/sd[j] for j in range(1,len(w))))

def mae(rows,fn):
    return round(sum(abs(fn(r)-r["actual"]) for r in rows)/len(rows),4) if rows else None

def run():
    histories=load(PATHS)
    quality=Counter();examples=[]
    for player,rows in histories.items():
        quality["player_games"]+=len(rows)
        quality["missing_minutes"]+=sum(r["minutes"] is None for r in rows)
        quality["duplicate_player_dates"]+=len(rows)-len({r["date"] for r in rows})
        quality["invalid_team"]+=sum(not r.get("team") for r in rows)
        for i in range(8,len(rows)):
            target=rows[i];prior=[r for r in rows[:i] if r["date"]<target["date"]]
            if len(prior)<8:continue
            challenger=predict(prior)
            if challenger is None:continue
            for m in MARKETS:
                examples.append({"date":target["date"],"market":m,"x":features(prior,m),
                                 "actual":target[m],"simple":sum(r[m] for r in prior[-10:])/len(prior[-10:]),
                                 "challenger":challenger[m]})
    dates=sorted({r["date"] for r in examples})
    if len(dates)<30:raise SystemExit("Insufficient distinct dates for split")
    cut1=dates[int(.6*len(dates))];cut2=dates[int(.8*len(dates))]
    partitions={"train":[r for r in examples if r["date"]<cut1],
                "validation":[r for r in examples if cut1<=r["date"]<cut2],
                "test":[r for r in examples if r["date"]>=cut2]}
    results={}
    for m in MARKETS:
        train=[(r["x"],r["actual"]) for r in partitions["train"] if r["market"]==m]
        val=[r for r in partitions["validation"] if r["market"]==m]
        test=[r for r in partitions["test"] if r["market"]==m]
        candidates=[(lam,fit(train,lam)) for lam in (.001,.01,.1,1.,10.)]
        candidates=[(lam,model) for lam,model in candidates if model]
        if not candidates:raise SystemExit("No fitted candidate for "+m)
        selected=min(candidates,key=lambda z:mae(val,lambda r:estimate(z[1],r["x"])))
        model=selected[1]
        results[m]={"train_n":len(train),"validation_n":len(val),"test_n":len(test),
                    "selected_lambda":selected[0],
                    "validation_ridge_mae":mae(val,lambda r:estimate(model,r["x"])),
                    "test_ridge_mae":mae(test,lambda r:estimate(model,r["x"])),
                    "test_simple_mae":mae(test,lambda r:r["simple"]),
                    "test_existing_challenger_mae":mae(test,lambda r:r["challenger"])}
    report={"status":"RESEARCH_ONLY_NO_PRODUCTION_CHANGES",
            "splits":{"train_before":cut1,"validation_before":cut2,"test_from":cut2},
            "data_quality":dict(quality),"markets":results,
            "limitations":["Boxscore-only features; no verified pregame injuries or odds",
                           "Player-level correlated examples; uncertainty not estimated",
                           "Historical results cannot establish betting profitability",
                           "Model parameters selected on validation only; test untouched"]}
    out=ROOT/"reports/nba_v4_baseline_audit.json";out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))
if __name__=="__main__":run()
