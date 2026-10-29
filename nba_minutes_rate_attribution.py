"""Research-only attribution of losing NBA picks to minutes vs per-minute production.

Uses captured graded picks and historical logs. No future data enters the
pregame prediction. Actual game minutes are only used in retrospective
decomposition; no claim of a pregame injury/lineup feed.
"""
import json,math,statistics
from collections import defaultdict
from pathlib import Path
from nba_challenger_model import load
ROOT=Path(__file__).resolve().parent
PATHS=[str(ROOT/"data"/f"propedge_historical_{s}_minutes.csv") for s in ("2023_24","2024_25","2025_26")]+[str(ROOT/"data/nba_2026_27.csv")]
MARKETS=("PTS","REB","AST","3PM","PRA")
BAD={"invalid_matchup_review","unmatched_completed_schedule"}
def mean(x):return sum(x)/len(x) if x else None
def summarize(rows):
    losses=[r for r in rows if r["margin"]<0]
    wins=[r for r in rows if r["margin"]>0]
    return {"n":len(rows),"wins":len(wins),"losses":len(losses),
            "roi_pct":round(100*(sum(r["payout"] for r in wins)-len(losses))/len(rows),2) if rows else None,
            "average_losing_margin":round(mean([-r["margin"] for r in losses]),2) if losses else None,
            "large_losses_over_5":sum(r["margin"]< -5 for r in rows),
            "mean_absolute_minutes_error":round(mean([abs(r["minute_error"]) for r in rows]),2) if rows else None,
            "mean_abs_minutes_component":round(mean([abs(r["minutes_component"]) for r in rows]),2) if rows else None,
            "mean_abs_rate_component":round(mean([abs(r["rate_component"]) for r in rows]),2) if rows else None,
            "minutes_dominant_losses":sum(abs(r["minutes_component"])>abs(r["rate_component"]) for r in losses),
            "rate_dominant_losses":sum(abs(r["minutes_component"])<=abs(r["rate_component"]) for r in losses)}
def run():
    history=load(PATHS)
    tracked=json.loads((ROOT/"data/model_tracking.json").read_text())
    rows=[];seen=set();excluded=defaultdict(int)
    for r in tracked:
        if r.get("validation_status") in BAD:excluded["identity_review"]+=1;continue
        if r.get("status") not in ("win","loss","push"):excluded["pending"]+=1;continue
        day=str(r.get("captured_at") or "")[:10].replace("-","")
        name=" ".join(str(r.get("player") or "").casefold().split())
        market=r.get("market")
        key=(day,r.get("game"),name,market,r.get("side"),str(r.get("line")))
        if key in seen:excluded["duplicate_same_line"]+=1;continue
        seen.add(key)
        try:
            actual=float(r["actual"]);line=float(r["line"]);odds=float(r["odds"])
            side=str(r["side"]).upper()
            if market not in MARKETS or side not in ("OVER","UNDER") or odds==0 or not all(map(math.isfinite,(actual,line,odds))):raise ValueError()
        except (ValueError,KeyError,TypeError):excluded["invalid"]+=1;continue
        logs=history.get(name,[])
        prior=[g for g in logs if g["date"]<day]
        minute_logs=[g for g in prior[-12:] if g.get("minutes") is not None and 1<=g["minutes"]<=60]
        if len(minute_logs)<5:excluded["insufficient_prior_minutes"]+=1;continue
        # Use identical pregame minute forecast formula as minutes-risk audit.
        pm=.65*mean([g["minutes"] for g in minute_logs[-5:]])+.35*mean([g["minutes"] for g in minute_logs])
        targets=[g for g in logs if g["date"]==day and g.get("minutes") is not None and 1<=g["minutes"]<=60]
        if len(targets)!=1:excluded["missing_or_ambiguous_actual_minutes"]+=1;continue
        am=targets[0]["minutes"]
        if abs(float(targets[0][market])-actual)>.01:
            excluded["actual_stat_mismatch"]+=1;continue
        # Forecast per-minute production strictly from prior games.
        rate=mean([g[market]/g["minutes"] for g in minute_logs[-10:]])
        forecast=pm*rate
        # Exact additive identity: actual - forecast = rate*(actual_minutes - projected_minutes)
        #                                + (actual - rate*actual_minutes).
        minutes_component=rate*(am-pm)
        rate_component=actual-rate*am
        if abs((minutes_component+rate_component)-(actual-forecast))>1e-6:raise AssertionError("Attribution identity failed")
        margin=(actual-line)*(1 if side=="OVER" else -1)
        rows.append({"date":day,"player":r.get("player"),"game":r.get("game"),
                     "market":market,"side":side,"line":line,"actual":actual,
                     "margin":margin,"payout":odds/100 if odds>0 else 100/abs(odds),
                     "projected_minutes":pm,"actual_minutes":am,"minute_error":am-pm,
                     "pregame_per_minute_rate":rate,"minutes_component":minutes_component,
                     "rate_component":rate_component,"rate_based_projection":forecast})
    dates=sorted({r["date"] for r in rows})
    def classify(r):
        return "minutes_dominant" if abs(r["minutes_component"])>abs(r["rate_component"]) else "production_rate_dominant"
    large=sorted((r for r in rows if r["margin"]< -5),key=lambda r:r["margin"])
    report={"status":"RESEARCH_ONLY_NO_PRODUCTION_CHANGE",
            "eligible":len(rows),"capture_dates":dates,"excluded":dict(excluded),
            "overall":summarize(rows),
            "by_market":{m:summarize([r for r in rows if r["market"]==m]) for m in MARKETS},
            "by_cause":{cause:summarize([r for r in rows if classify(r)==cause]) for cause in ("minutes_dominant","production_rate_dominant")},
            "points_and_pra_large_losses":{m:{"n":sum(r["market"]==m for r in large),
                "minutes_dominant":sum(r["market"]==m and classify(r)=="minutes_dominant" for r in large),
                "rate_dominant":sum(r["market"]==m and classify(r)=="production_rate_dominant" for r in large)}
                for m in ("PTS","PRA")},
            "largest_losses":[{"player":r["player"],"game":r["game"],"market":r["market"],
                "side":r["side"],"line":r["line"],"actual":r["actual"],"loss_margin":round(-r["margin"],2),
                "projected_minutes":round(r["projected_minutes"],2),
                "actual_minutes":round(r["actual_minutes"],2),
                "minutes_component":round(r["minutes_component"],2),
                "rate_component":round(r["rate_component"],2),
                "dominant_cause":classify(r)} for r in large[:25]],
            "decision":"INSUFFICIENT_INDEPENDENT_DATES" if len(dates)<10 else "REQUIRES_CHRONOLOGICAL_HOLDOUT",
            "limitations":["This decomposes a separate historical minutes-times-rate proxy, not the production model projection.",
                "Actual game minutes and rates are postgame diagnostic facts only, never pregame selection inputs.",
                "Historical player logs may not match captured event identity; mismatched actual stat rows are excluded.",
                "No verified injury or lineup data; cannot infer injury cause.",
                "Same-game picks and different lines are correlated; sample dates are limited.",
                "No changes to NBA/NFL live predictions, consensus weights, filters, or rankings."]}
    out=ROOT/"reports/nba_minutes_rate_attribution.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({"eligible":len(rows),"capture_dates":dates,"by_market":report["by_market"],"points_and_pra_large_losses":report["points_and_pra_large_losses"],"decision":report["decision"]},indent=2))
if __name__=="__main__":run()
