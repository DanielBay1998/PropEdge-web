"""Scoring volatility holdout: test pregame uncertainty against future points errors.

No shot-attempt/usage columns are assumed. Research-only, no live filters.
"""
import json,statistics
from pathlib import Path
from collections import defaultdict
from nba_challenger_model import load,predict
ROOT=Path(__file__).resolve().parent
PATHS=[str(ROOT/"data"/f"propedge_historical_{s}_minutes.csv") for s in ("2023_24","2024_25","2025_26")]+[str(ROOT/"data/nba_2026_27.csv")]
def stats(rows):
    if not rows:return {"n":0}
    return {"n":len(rows),"pts_mae":round(statistics.mean(r["error"] for r in rows),3),
            "pts_bias":round(statistics.mean(r["bias"] for r in rows),3),
            "miss_over_5_pct":round(100*sum(r["error"]>5 for r in rows)/len(rows),2)}
def run():
    histories=load(PATHS);examples=[]
    for player,games in histories.items():
        for i,target in enumerate(games):
            prior=[g for g in games[:i] if g["date"]<target["date"]]
            if len(prior)<12:continue
            pred=predict(prior)
            if pred is None:continue
            recent=[g["PTS"] for g in prior[-12:]]
            volatility=statistics.pstdev(recent)
            minutes=[g["minutes"] for g in prior[-12:] if g.get("minutes") is not None]
            minute_vol=statistics.pstdev(minutes) if len(minutes)>=8 else None
            examples.append({"date":target["date"],"error":abs(pred["PTS"]-target["PTS"]),
               "bias":pred["PTS"]-target["PTS"],"volatility":volatility,
               "minute_vol":minute_vol})
    dates=sorted({r["date"] for r in examples})
    if len(dates)<30:raise SystemExit("Insufficient dates")
    validation_start=dates[int(.6*len(dates))]
    holdout_start=dates[int(.8*len(dates))]
    validation=[r for r in examples if validation_start<=r["date"]<holdout_start]
    holdout=[r for r in examples if r["date"]>=holdout_start]
    # Fixed median cutoffs selected using validation only.
    pts_cut=statistics.median(r["volatility"] for r in validation)
    minute_values=[r["minute_vol"] for r in validation if r["minute_vol"] is not None]
    minute_cut=statistics.median(minute_values)
    def breakdown(rows):
        return {"all":stats(rows),
            "high_pts_volatility":stats([r for r in rows if r["volatility"]>pts_cut]),
            "low_pts_volatility":stats([r for r in rows if r["volatility"]<=pts_cut]),
            "high_minutes_volatility":stats([r for r in rows if r["minute_vol"] is not None and r["minute_vol"]>minute_cut]),
            "low_minutes_volatility":stats([r for r in rows if r["minute_vol"] is not None and r["minute_vol"]<=minute_cut])}
    report={"status":"RESEARCH_ONLY_NO_BET_FILTER","validation_start":validation_start,
      "holdout_start":holdout_start,"thresholds_from_validation":{"points_std":round(pts_cut,3),"minutes_std":round(minute_cut,3)},
      "validation":breakdown(validation),"holdout":breakdown(holdout),
      "limitations":["Historical points and minutes only; no field-goal attempts, 3PA or true usage data.",
        "Player-game projection errors, not captured-odds betting ROI.",
        "Volatility thresholds are exploratory; not validated for profitability.",
        "No changes to production NBA or NFL models."]}
    out=ROOT/"reports/nba_scoring_volatility_holdout.json";out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))
if __name__=="__main__":run()
