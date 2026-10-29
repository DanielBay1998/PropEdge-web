"""Diagnose losing NBA points and threes picks without fitting selection rules.

All captured odds and original sides are preserved. This is descriptive,
retrospective research and is not an independent profitability holdout.
"""
import json,statistics
from pathlib import Path
from collections import defaultdict,Counter
from nba_challenger_model import load,predict
ROOT=Path(__file__).resolve().parent
PATHS=[str(ROOT/"data"/f"propedge_historical_{s}_minutes.csv") for s in ("2023_24","2024_25","2025_26")]+[str(ROOT/"data/nba_2026_27.csv")]
def summary(rows):
    if not rows:return {"n":0}
    wins=[r for r in rows if r["margin"]>0];losses=[r for r in rows if r["margin"]<0]
    return {"n":len(rows),"wins":len(wins),"losses":len(losses),
       "roi_pct":round(100*(sum(r["payout"] for r in wins)-len(losses))/len(rows),2),
       "mean_model_error":round(statistics.mean(r["projection"]-r["actual"] for r in rows),2),
       "mean_absolute_error":round(statistics.mean(abs(r["projection"]-r["actual"]) for r in rows),2),
       "large_losses_gt5":sum(-r["margin"]>5 for r in losses),
       "mean_loss_margin":round(statistics.mean(-r["margin"] for r in losses),2) if losses else None}
def run():
    history=load(PATHS);tracking=json.loads((ROOT/"data/model_tracking.json").read_text())
    seen=set();rows=[];excluded=Counter()
    for r in tracking:
        if r.get("status") not in ("win","loss","push") or r.get("market") not in ("PTS","3PM"):continue
        if r.get("validation_status") in ("invalid_matchup_review","unmatched_completed_schedule"):excluded["identity_review"]+=1;continue
        day=str(r.get("captured_at") or "")[:10].replace("-","")
        name=" ".join(str(r.get("player") or "").casefold().split())
        key=(day,r.get("event_id"),name,r.get("market"),r.get("side"),str(r.get("line")))
        if key in seen:excluded["duplicate"]+=1;continue
        seen.add(key)
        prior=[g for g in history.get(name,[]) if g["date"]<day]
        if len(prior)<8:excluded["missing_history"]+=1;continue
        try:
            actual=float(r["actual"]);line=float(r["line"]);projection=float(r["projection"]);odds=float(r["odds"])
            if odds==0 or r["side"] not in ("OVER","UNDER"):raise ValueError()
            minute=[g["minutes"] for g in prior[-5:] if g.get("minutes") is not None]
            if not minute:raise ValueError()
            recent=statistics.mean(minute)
            rate=statistics.mean(g[r["market"]] for g in prior[-5:])
        except (ValueError,TypeError,KeyError):excluded["invalid"]+=1;continue
        margin=(actual-line)*(1 if r["side"]=="OVER" else -1)
        rows.append({"player":r.get("player"),"date":day,"market":r["market"],"side":r["side"],
           "line":line,"actual":actual,"projection":projection,"margin":margin,
           "payout":odds/100 if odds>0 else 100/abs(odds),"recent_minutes":recent,
           "recent_stat":rate})
    bymarket={}
    for market in ("PTS","3PM"):
        group=[r for r in rows if r["market"]==market]
        bymarket[market]={"overall":summary(group),
           "over":summary([r for r in group if r["side"]=="OVER"]),
           "under":summary([r for r in group if r["side"]=="UNDER"]),
           "recent_stat_above_projection":summary([r for r in group if r["recent_stat"]>r["projection"]]),
           "recent_stat_at_or_below_projection":summary([r for r in group if r["recent_stat"]<=r["projection"]]),
           "largest_losses":[{k:r[k] for k in ("player","date","side","line","actual","projection","margin","recent_minutes","recent_stat")}
             for r in sorted((r for r in group if r["margin"]<0),key=lambda r:r["margin"])[:12]]}
    report={"status":"DESCRIPTIVE_ONLY_NO_RULE_SELECTION","capture_dates":sorted({r["date"] for r in rows}),
       "matched":len(rows),"excluded":dict(excluded),"markets":bymarket,
       "limitations":["Single/few capture dates; not an independent prospective test.",
        "Recent minutes and recent boxscore stats are historical proxies, not injuries, lineup, usage or shot volume.",
        "Subgroup performance is exploratory, not a validated betting filter.",
        "Recorded projection may differ from reconstructed consensus; no production changes."]}
    path=ROOT/"reports/nba_points_threes_failure_diagnostics.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))
if __name__=="__main__":run()
