"""Audit feasibility of a leakage-safe volatility-calibrated production consensus holdout.
Only captured pregame predictions and independently later graded outcomes qualify.
Never infer historical primary predictions or use retrospective V4 fits as pregame.
"""
import json, math, statistics
from collections import Counter
from pathlib import Path
from nba_challenger_model import load
from nba_scoring_volatility_holdout import PATHS
ROOT=Path(__file__).resolve().parent
def run():
    history=load(PATHS)
    tracking=json.loads((ROOT/"data/model_tracking.json").read_text())
    valid=[]; exclusions=Counter()
    for row in tracking:
        if row.get("status") not in ("win","loss","push"):
            exclusions["ungraded"]+=1;continue
        if row.get("validation_status") in ("invalid_matchup_review","unmatched_completed_schedule"):
            exclusions["identity_review"]+=1;continue
        if row.get("market")!="PTS":
            exclusions["non_points"]+=1;continue
        day=str(row.get("captured_at") or "")[:10].replace("-","")
        player=" ".join(str(row.get("player") or "").casefold().split())
        prior=[g for g in history.get(player,[]) if g["date"]<day]
        if len(prior)<12:
            exclusions["insufficient_history"]+=1;continue
        try:
            projection=float(row["projection"]); actual=float(row["actual"])
            if not math.isfinite(projection) or not math.isfinite(actual):raise ValueError()
        except (ValueError,TypeError,KeyError):
            exclusions["invalid_numbers"]+=1;continue
        valid.append({"date":day,"player":player,"volatility":statistics.pstdev([g["PTS"] for g in prior[-12:]]),
                      "primary_error":abs(projection-actual)})
    dates=sorted(set(r["date"] for r in valid))
    # A single date per split cannot establish robust out-of-sample performance.
    # No three-model consensus is reconstructed with potentially overlapping V4 fits.
    report={"status":"INSUFFICIENT_PROSPECTIVE_CONSENSUS_HISTORY",
      "purpose":"Audit eligibility for chronological, pregame three-model consensus confidence calibration",
      "eligible_primary_points_captures":len(valid),
      "distinct_capture_dates":dates,"excluded":dict(exclusions),
      "requirements":["Store pregame primary, challenger, V4 and consensus points projections at capture time",
        "Store pregame trailing-12 scoring volatility and selected sportsbook line/odds",
        "Grade outcomes after completion; keep player/game identity verification",
        "Use earlier dates to calibrate risk and untouched later dates for holdout",
        "Require at least 10 distinct capture dates in both calibration and holdout windows before claiming validated performance"],
      "not_evaluated":["Production three-model consensus Brier score","Consensus betting ROI","Volatility-adjusted consensus accuracy"],
      "limitations":["Retrospective three-model audit reconstructs V4 using coefficients that may overlap test dates.",
        "Only primary captured projections can be verified as pregame from existing tracking.",
        "No production model, endpoint, betting filter, or confidence score changes."]}
    path=ROOT/"reports/nba_consensus_volatility_holdout_readiness.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))
if __name__=="__main__":run()
