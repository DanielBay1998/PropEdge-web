"""Walk-forward projected-minutes ablation; no target-game data enters predictions."""
import json
from pathlib import Path
from nba_challenger_model import load,predict,MARKETS

ROOT=Path(__file__).resolve().parent
def estimate_minutes(prior):
    rows=[r for r in prior[-30:] if r.get("minutes") is not None and 8<=r["minutes"]<=48]
    if len(rows)<8:return None
    last5=rows[-5:]
    last12=rows[-12:]
    return max(8,min(42,.65*sum(r["minutes"] for r in last5)/len(last5)+
                       .35*sum(r["minutes"] for r in last12)/len(last12)))

def minute_projection(prior,market,weight):
    base=predict(prior)
    if base is None:return None
    if market=="PRA":return sum(minute_projection(prior,k,weight) for k in ("PTS","REB","AST"))
    minutes=estimate_minutes(prior)
    if minutes is None:return base[market]
    rows=[r for r in prior[-20:] if r.get("minutes") is not None and 8<=r["minutes"]<=48]
    if len(rows)<8:return base[market]
    rate=sum(r[market] for r in rows)/sum(r["minutes"] for r in rows)
    return (1-weight)*base[market]+weight*rate*minutes

def run():
    paths=[*[str(ROOT/"data"/f"propedge_historical_{season}_minutes.csv") for season in ("2023_24","2024_25","2025_26")],str(ROOT/"data/nba_2026_27.csv")]
    histories=load(paths)
    examples=[]
    for player,rows in histories.items():
        for i in range(8,len(rows)):
            target=rows[i];day=target["date"]
            prior=[r for r in rows[:i] if r["date"]<day]
            base=predict(prior)
            if base is None:continue
            minutes=estimate_minutes(prior)
            if minutes is None:continue
            examples.append((day,prior,target,base))
    days=sorted({e[0] for e in examples})
    if len(days)<20:raise SystemExit("Insufficient evaluation dates")
    split=days[int(.7*len(days))]
    train=[e for e in examples if e[0]<split]
    hold=[e for e in examples if e[0]>=split]
    weights=(0,.25,.5,.75,1)
    def mae(rows,market,weight):
        return sum(abs(minute_projection(prior,market,weight)-actual[market])
                   for _,prior,actual,_ in rows)/len(rows)
    chosen={m:min(weights,key=lambda w:mae(train,m,w)) for m in MARKETS}
    report={"status":"RESEARCH_ONLY","holdout_start":split,
            "train_player_games":len(train),"holdout_player_games":len(hold),
            "selected_weights":chosen,
            "holdout":{m:{"baseline_mae":round(mae(hold,m,0),4),
                           "minutes_mae":round(mae(hold,m,chosen[m]),4)}
                       for m in MARKETS},
            "limitations":["Only games with recorded historical minutes qualify",
                           "No injury, starting lineup or rotation forecast is assumed",
                           "Not a direct test against captured production picks",
                           "No betting ROI inference"]}
    out=ROOT/"reports/nba_minutes_backtest.json";out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))
if __name__=="__main__":run()
