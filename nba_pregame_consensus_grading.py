"""Research-only grading of immutable pregame NBA three-model snapshots.

Reads existing graded tracking records; never backfills forecasts or changes picks.
"""
import json,math,statistics
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parent
FIELDS={"primary":"pregame_primary_projection","challenger":"pregame_challenger_projection","v4":"pregame_v4_projection","consensus":"pregame_consensus_projection"}
def metrics(rows):
    if not rows:return {"n":0}
    errors=[abs(r["prediction"]-r["actual"]) for r in rows]
    signed=[r["prediction"]-r["actual"] for r in rows]
    return {"n":len(rows),"mae":round(statistics.mean(errors),4),
      "bias":round(statistics.mean(signed),4),
      "median_absolute_error":round(statistics.median(errors),4),
      **{"within_"+str(k)+"_pct":round(100*sum(e<=k for e in errors)/len(errors),2) for k in (1,2,3,5)},
      "miss_over_5_pct":round(100*sum(e>5 for e in errors)/len(errors),2)}
def margin_stats(rows):
    losses=[];wins=[];pushes=0;profit=0.
    for r in rows:
        margin=(r["actual"]-r["line"])*(1 if r["side"]=="OVER" else -1)
        if margin>0:
            wins.append(margin);profit+=r["payout"]
        elif margin<0:
            losses.append(-margin);profit-=1
        else:pushes+=1
    return {"n":len(rows),"wins":len(wins),"losses":len(losses),"pushes":pushes,
      "losses_within_0_5":sum(v<=.5 for v in losses),
      "losses_within_1":sum(v<=1 for v in losses),
      "losses_within_2":sum(v<=2 for v in losses),
      "losses_over_5":sum(v>5 for v in losses),
      "mean_loss_margin":round(statistics.mean(losses),3) if losses else None,
      "captured_side_flat_stake_roi_pct":round(100*profit/len(rows),2) if rows else None,
      "roi_note":"ROI of originally captured bets only; not ROI of hypothetical consensus selections"}
def run():
    tracking=json.loads((ROOT/"data/model_tracking.json").read_text())
    complete=[r for r in tracking if r.get("consensus_capture_status")=="complete"]
    graded=[];excluded=defaultdict(int)
    for r in complete:
        if r.get("status") not in ("win","loss","push"):
            excluded["pending"]+=1;continue
        if r.get("validation_status") in ("invalid_matchup_review","unmatched_completed_schedule"):
            excluded["identity_review"]+=1;continue
        try:
            actual=float(r["actual"]);line=float(r["line"]);odds=float(r["odds"])
            forecasts={k:float(r[v]) for k,v in FIELDS.items()}
            volatility=float(r["pregame_points_volatility_12"])
            if r.get("side") not in ("OVER","UNDER") or odds==0 or not all(math.isfinite(v) for v in [actual,line,odds,volatility,*forecasts.values()]):raise ValueError()
        except (ValueError,KeyError,TypeError):
            excluded["invalid_fields"]+=1;continue
        graded.append({"id":r.get("id"),"date":str(r.get("captured_at",""))[:10],"market":r.get("market"),
            "actual":actual,"line":line,"side":r["side"],"volatility":volatility,
            "payout":odds/100 if odds>0 else 100/abs(odds),"forecasts":forecasts})
    def model_breakdown(group):
        return {k:metrics([{"prediction":r["forecasts"][k],"actual":r["actual"]} for r in group]) for k in FIELDS}
    # Only compare immutable pregame forecasts after outcomes have settled.
    context_report_path=ROOT/"reports/nba_context_holdout.json"
    context_models=json.loads(context_report_path.read_text()).get("markets",{}) if context_report_path.exists() else {}
    paired=[]
    for r in complete:
        if r.get("status") not in ("win","loss","push") or r.get("validation_status") in ("invalid_matchup_review","unmatched_completed_schedule"):
            continue
        m=context_models.get(r.get("market"),{})
        ctx=r.get("pregame_advanced_metrics")
        if not isinstance(ctx,dict) or m.get("status")!="holdout_evaluated" or not m.get("improved"):
            continue
        rest=ctx.get("rest_days")
        b2b=ctx.get("back_to_back")
        if rest is None or b2b is None:continue
        try:
            actual=float(r["actual"])
            consensus=float(r["pregame_consensus_projection"])
            weights=m["weights"]
            correction=weights["intercept"]+weights["weights"]["rest"]*(min(float(rest),5)-weights["means"]["rest"])+weights["weights"]["b2b"]*(float(b2b)-weights["means"]["b2b"])
            adjusted=max(0,consensus+correction)
            if all(math.isfinite(v) for v in (actual,consensus,adjusted)):
                paired.append((str(r.get("captured_at",""))[:10],actual,consensus,adjusted))
        except (KeyError,ValueError,TypeError):continue
    dates={r[0] for r in paired}
    context_comparison={"status":"DESCRIPTIVE_NOT_PRODUCTION_ELIGIBLE" if len(dates)>=20 else "AWAITING_SETTLED_PROSPECTIVE_CAPTURES",
                        "eligible":len(paired),"independent_capture_dates":len(dates),
                        "consensus_mae":round(statistics.mean(abs(a-b) for _,a,b,_ in paired),4) if paired else None,
                        "adjusted_mae":round(statistics.mean(abs(a-c) for _,a,_,c in paired),4) if paired else None,
                        "note":"Historical challenger-trained weights; not independently fitted to consensus. Requires separate validation before promotion.",
                        "production_promoted":False}
    points=[r for r in graded if r["market"]=="PTS"]
    dates=sorted({r["date"] for r in points})
    # Grouping by observed volatility is descriptive only, not a tuned confidence policy.
    threshold=statistics.median(r["volatility"] for r in points) if points else None
    report={"status":"RESEARCH_ONLY_PENDING_MORE_GRADED_DATES" if len(dates)<20 else "RESEARCH_ONLY_DESCRIPTIVE",
      "total_complete_pregame_snapshots":len(complete),"graded_eligible":len(graded),
      "excluded":dict(excluded),"points_graded":len(points),"points_capture_dates":dates,
      "overall":model_breakdown(graded),"context_comparison":context_comparison,
      "by_market":{m:model_breakdown([r for r in graded if r["market"]==m]) for m in ("PTS","REB","AST","3PM","PRA")},
      "captured_bet_outcomes":margin_stats(graded),
      "points_volatility_exploratory":{"median_cutoff":round(threshold,4) if threshold is not None else None,
        "high":model_breakdown([r for r in points if r["volatility"]>threshold]) if threshold is not None else model_breakdown([]),
        "low":model_breakdown([r for r in points if r["volatility"]<=threshold]) if threshold is not None else model_breakdown([])},
      "holdout_ready":len(dates)>=20,
      "limitations":["Only forecasts stored before games count; older retrospective reconstructions excluded.",
        "A minimum of 20 independent capture dates is required before chronological calibration/holdout claims.",
        "Volatility median calculated on graded data is exploratory, not an out-of-sample threshold.",
        "Captured-side ROI does not establish consensus-selected betting profitability.",
        "No production projection, confidence score, or betting filter changes."]}
    out=ROOT/"reports/nba_pregame_consensus_grading.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({"status":report["status"],"complete":len(complete),"graded":len(graded),"points_dates":dates,"overall":report["overall"]},indent=2))
if __name__=="__main__":run()
