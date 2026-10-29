"""Matched-line retrospective audit, not a prospective challenger performance claim.
Uses actual captured sportsbook lines/odds and grades, excludes identity-review records,
and computes all model decisions on exactly the same eligible captured selections.
"""
import json, math
from collections import defaultdict
from pathlib import Path
from nba_challenger_model import load,predict

ROOT=Path(__file__).resolve().parent
PATHS=[str(ROOT/"data"/f"propedge_historical_{s}_minutes.csv") for s in ("2023_24","2024_25","2025_26")]+[str(ROOT/"data/nba_2026_27.csv")]
EXCLUDED={"invalid_matchup_review","unmatched_completed_schedule"}
MODELS=("primary","challenger","blend")

def summarize(rows,model):
    wins=losses=pushes=0
    error=profit=0.
    for r in rows:
        estimate=r["predictions"][model]
        delta=estimate-r["line"]
        actual_delta=r["actual"]-r["line"]
        error+=abs(estimate-r["actual"])
        if delta==0 or actual_delta==0:
            pushes+=1
        elif delta*actual_delta>0:
            wins+=1
            profit+=r["payout"]
        else:
            losses+=1
            profit-=1
    n=len(rows)
    return {"n":n,"wins":wins,"losses":losses,"pushes":pushes,
            "hit_rate_pct":round(100*wins/(wins+losses),2) if wins+losses else None,
            "mae":round(error/n,4) if n else None,
            "flat_roi_pct":round(100*profit/n,2) if n else None}

def run():
    history=load(PATHS)
    tracking=json.loads((ROOT/"data/model_tracking.json").read_text())
    matched=[];excluded=defaultdict(int)
    for r in tracking:
        if r.get("status") not in ("win","loss","push"):
            excluded["pending"]+=1;continue
        if r.get("validation_status") in EXCLUDED:
            excluded["identity_or_schedule_review"]+=1;continue
        stamp=str(r.get("captured_at") or "")
        day=stamp[:10].replace("-","")
        name=" ".join(str(r.get("player") or "").casefold().split())
        market=r.get("market")
        if len(day)!=8 or not day.isdigit() or not name:
            excluded["invalid_capture_identity"]+=1;continue
        prior=[g for g in history.get(name,[]) if g["date"]<day]
        alt=predict(prior)
        if not alt or market not in alt:
            excluded["no_prior_history_or_market"]+=1;continue
        try:
            actual=float(r["actual"]);line=float(r["line"])
            primary=float(r["projection"]);odds=float(r["odds"])
            if not all(map(math.isfinite,(actual,line,primary,odds))) or odds==0:raise ValueError()
        except (KeyError,ValueError,TypeError):
            excluded["invalid_numbers_or_odds"]+=1;continue
        # The captured side is not used to choose the challenger direction.
        payout=odds/100 if odds>0 else 100/abs(odds)
        matched.append({"date":day,"market":market,"line":line,"actual":actual,
                        "payout":payout,"predictions":{"primary":primary,
                        "challenger":alt[market],"blend":(primary+alt[market])/2}})
    dates=sorted({r["date"] for r in matched})
    recent=[r for r in matched if r["date"]>="20261020"]
    by_date={d:{m:summarize([r for r in matched if r["date"]==d],m) for m in MODELS} for d in dates}
    by_market={k:{m:summarize([r for r in matched if r["market"]==k],m) for m in MODELS}
               for k in sorted({r["market"] for r in matched})}
    report={"status":"MATCHED_LINE_RETROSPECTIVE_RESEARCH_ONLY_NOT_PROSPECTIVE",
            "capture_dates":dates,"matched_captured_selections":len(matched),
            "excluded":dict(excluded),
            "all_dates":{m:summarize(matched,m) for m in MODELS},
            "recent_2026_27":{m:summarize(recent,m) for m in MODELS},
            "by_capture_date":by_date,"by_market":by_market,
            "limitations":[
                "All models evaluated on identical captured selections, lines, and odds",
                "Challenger and blend reconstructed retrospectively; not timestamped pregame forecasts",
                "No independent prospective holdout or proof of profitability",
                "Same captured sportsbook odds applied to both possible directions; opposite-side odds may differ",
                "No production model changes or automatic promotion"]}
    out=ROOT/"reports/nba_matched_line_holdout_audit.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({"status":report["status"],"capture_dates":dates,
                      "matched":len(matched),"excluded":dict(excluded),
                      "all_dates":report["all_dates"],"recent":report["recent_2026_27"]},indent=2))
    if not matched:raise SystemExit("No matched eligible captured selections")
if __name__=="__main__":run()
