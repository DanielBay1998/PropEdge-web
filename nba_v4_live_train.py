"""Fit deployable V4 ridge coefficients from strictly earlier NBA player games."""
import json,datetime
from pathlib import Path
from nba_v4_baseline_audit import ROOT,PATHS,features,fit,estimate
from nba_v4_clean_history import load
from nba_challenger_model import MARKETS

def run():
    histories=load(PATHS)
    samples={m:[] for m in MARKETS}
    cutoff=datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
    for rows in histories.values():
        for i in range(8,len(rows)):
            target=rows[i]
            if target["date"]>=cutoff:continue
            prior=rows[:i]
            if prior[-1]["date"]>=target["date"]:continue
            for m in MARKETS:
                samples[m].append((features(prior,m),target[m]))
    out={"as_of_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"method":"V4_RIDGE_PRIOR_GAME_FEATURES","markets":{}}
    for m,rows in samples.items():
        if len(rows)<500:continue
        model=fit(rows,0.1)
        if model:
            weights,means,scales=model
            out["markets"][m]={"weights":weights,"means":means,"scales":scales,"training_rows":len(rows)}
    if len(out["markets"])!=len(MARKETS):raise RuntimeError("Missing trained V4 market")
    dest=ROOT/"data/nba_v4_live_coefficients.json"
    dest.write_text(json.dumps(out,indent=2)+"\n")
    print(json.dumps({"markets":list(out["markets"]),"training_rows":{m:out["markets"][m]["training_rows"] for m in MARKETS}}))
if __name__=="__main__":run()
