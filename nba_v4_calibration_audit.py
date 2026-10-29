"""Research-only residual calibration for NBA V4 regression.
Fits residual distributions on chronological validation data and evaluates
coverage on later untouched test data. Does not use sportsbook lines or odds.
"""
import json,math
from pathlib import Path
from nba_v4_baseline_audit import ROOT,PATHS,MARKETS,load,predict,features,fit,estimate

def run():
    histories=load(PATHS); examples=[]
    for player,rows in histories.items():
        for i in range(8,len(rows)):
            target=rows[i];prior=[r for r in rows[:i] if r["date"]<target["date"]]
            if len(prior)<8 or predict(prior) is None:continue
            for m in MARKETS:
                examples.append((target["date"],m,features(prior,m),target[m]))
    dates=sorted({r[0] for r in examples})
    cut1=dates[int(.6*len(dates))];cut2=dates[int(.8*len(dates))]
    output={}
    for m in MARKETS:
        train=[(x,y) for day,market,x,y in examples if market==m and day<cut1]
        validation=[(x,y) for day,market,x,y in examples if market==m and cut1<=day<cut2]
        test=[(x,y) for day,market,x,y in examples if market==m and day>=cut2]
        candidates=[(lam,fit(train,lam)) for lam in (.001,.01,.1,1.,10.)]
        candidates=[(lam,model) for lam,model in candidates if model]
        lam,model=min(candidates,key=lambda z:sum(abs(estimate(z[1],x)-y) for x,y in validation)/len(validation))
        # Calibration distribution is frozen before test; empirical residual quantiles.
        residuals=sorted(y-estimate(model,x) for x,y in validation)
        def quantile(p):
            return residuals[min(len(residuals)-1,max(0,int(p*(len(residuals)-1))))]
        low,high=quantile(.1),quantile(.9)
        covered=sum(estimate(model,x)+low<=y<=estimate(model,x)+high for x,y in test)
        output[m]={"validation_n":len(validation),"test_n":len(test),
                   "selected_lambda":lam,"residual_p10":round(low,4),
                   "residual_p90":round(high,4),
                   "nominal_interval_coverage_pct":80,
                   "test_interval_coverage_pct":round(100*covered/len(test),2),
                   "note":"Population-wide residual interval, not player-conditional calibrated over probability"}
    report={"status":"RESEARCH_ONLY_NO_PRODUCTION_CHANGES",
            "train_before":cut1,"validation_before":cut2,"test_from":cut2,
            "intervals":output,"limitations":[
                "Empirical residual intervals are not sportsbook-side probabilities",
                "Player-level dependence and changing roles can cause undercoverage",
                "No pregame odds or profitability inference",
                "No live projection changes"]}
    out=ROOT/"reports/nba_v4_calibration_audit.json";out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report,indent=2)+"\n");print(json.dumps(report,indent=2))
if __name__=="__main__":run()
