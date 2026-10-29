"""Research-only chronological calibration of scoring-volatility error risk.
Compares validation-calibrated probability of a >5 point miss to a constant
validation baseline. Does not change projections or generate betting filters.
"""
import json, statistics
from pathlib import Path
from nba_scoring_volatility_holdout import PATHS
from nba_challenger_model import load, predict

ROOT = Path(__file__).resolve().parent
def mean(xs):
    return sum(xs)/len(xs)
def evaluate(rows, model):
    if not rows: return {"n": 0}
    outcomes=[int(r["error"]>5) for r in rows]
    probs=[model(r) for r in rows]
    return {"n":len(rows),"miss_rate_pct":round(100*mean(outcomes),3),
            "brier":round(mean([(p-y)**2 for p,y in zip(probs,outcomes)]),6),
            "mean_predicted_risk_pct":round(100*mean(probs),3)}
def run():
    histories=load(PATHS); rows=[]
    for games in histories.values():
        for i,target in enumerate(games):
            prior=[g for g in games[:i] if g["date"]<target["date"]]
            if len(prior)<12: continue
            pred=predict(prior)
            if pred is None: continue
            rows.append({"date":target["date"],
                         "vol":statistics.pstdev([g["PTS"] for g in prior[-12:]]),
                         "error":abs(pred["PTS"]-target["PTS"])})
    dates=sorted({r["date"] for r in rows})
    if len(dates)<30: raise SystemExit("Insufficient dates")
    vstart=dates[int(.6*len(dates))]; hstart=dates[int(.8*len(dates))]
    val=[r for r in rows if vstart<=r["date"]<hstart]
    test=[r for r in rows if r["date"]>=hstart]
    if not val or not test: raise SystemExit("Empty split")
    threshold=statistics.median(r["vol"] for r in val)
    def is_high(r):return r["vol"]>threshold
    baseline=mean([r["error"]>5 for r in val])
    high=[r for r in val if is_high(r)]
    low=[r for r in val if not is_high(r)]
    # Laplace smoothing prevents zero or one probability in sparse groups.
    rates={True:(sum(r["error"]>5 for r in high)+1)/(len(high)+2),
           False:(sum(r["error"]>5 for r in low)+1)/(len(low)+2)}
    constant=lambda r:baseline
    calibrated=lambda r:rates[is_high(r)]
    base=evaluate(test,constant); candidate=evaluate(test,calibrated)
    report={
        "status":"RESEARCH_ONLY_NOT_PROMOTED",
        "comparison":"Historical challenger points miss-risk calibration; NOT three-model production consensus or betting ROI",
        "validation_start":vstart,"holdout_start":hstart,
        "threshold_from_validation":round(threshold,5),
        "validation_n":len(val),"holdout_n":len(test),
        "validation_miss_rate_pct":round(100*baseline,3),
        "validation_group_risk_pct":{"high":round(100*rates[True],3),"low":round(100*rates[False],3)},
        "holdout":{"constant_risk":base,"volatility_calibrated_risk":candidate,
                   "brier_improvement":round(base["brier"]-candidate["brier"],6),
                   "high":evaluate([r for r in test if is_high(r)],calibrated),
                   "low":evaluate([r for r in test if not is_high(r)],calibrated)},
        "limitations":["Risk predicts absolute points error >5, not bet win probability.",
                       "Validation chooses the cutoff and risk rates; holdout is not used for tuning.",
                       "Uses historical challenger predictions, not matched production three-model consensus.",
                       "No sportsbook odds, ROI, field-goal attempts or true usage inputs.",
                       "No production NBA or NFL projections or confidence displays changed."]}
    path=ROOT/"reports/nba_volatility_risk_calibration.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))
if __name__=="__main__":run()
