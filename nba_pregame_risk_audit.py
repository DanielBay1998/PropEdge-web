"""Pregame-only minutes/role risk audit of captured NBA picks.

Historical rotation proxies are NOT confirmed injuries or lineups. Candidate
risk filters are exploratory; no live selection or model changes.
"""
import json,math,statistics
from collections import defaultdict
from pathlib import Path
from nba_challenger_model import load
ROOT=Path(__file__).resolve().parent
PATHS=[str(ROOT/"data"/f"propedge_historical_{s}_minutes.csv") for s in ("2023_24","2024_25","2025_26")]+[str(ROOT/"data/nba_2026_27.csv")]
BAD={"invalid_matchup_review","unmatched_completed_schedule"}

def avg(v):return sum(v)/len(v) if v else None
def profile(rows):
    wins=[r for r in rows if r["margin"]>0]
    losses=[r for r in rows if r["margin"]<0]
    return {"n":len(rows),"wins":len(wins),"losses":len(losses),
            "win_rate_pct":round(100*len(wins)/(len(wins)+len(losses)),2) if wins or losses else None,
            "flat_roi_pct":round(100*(sum(r["payout"] for r in wins)-len(losses))/len(rows),2) if rows else None,
            "avg_loss_margin":round(avg([-r["margin"] for r in losses]),2) if losses else None,
            "losses_more_than_5":sum(-r["margin"]>5 for r in losses)}

def run():
    history=load(PATHS)
    tracking=json.loads((ROOT/"data/model_tracking.json").read_text())
    rows=[];seen=set();excluded=defaultdict(int)
    for r in tracking:
        if r.get("validation_status") in BAD:excluded["identity_review"]+=1;continue
        if r.get("status") not in ("win","loss","push"):excluded["pending"]+=1;continue
        day=str(r.get("captured_at") or "")[:10].replace("-","")
        name=" ".join(str(r.get("player") or "").casefold().split())
        market=r.get("market")
        key=(day,r.get("game"),name,market,r.get("side"),r.get("line"))
        if key in seen:excluded["duplicate_same_line"]+=1;continue
        seen.add(key)
        try:
            actual=float(r["actual"]);line=float(r["line"]);odds=float(r["odds"])
            if not all(math.isfinite(x) for x in (actual,line,odds)) or odds==0:raise ValueError()
            side=str(r["side"]).upper()
            if side not in ("OVER","UNDER"):raise ValueError()
        except (ValueError,TypeError,KeyError):excluded["invalid"]+=1;continue
        prior=[g for g in history.get(name,[]) if g["date"]<day]
        minutes=[g["minutes"] for g in prior[-12:] if g.get("minutes") is not None and 1<=g["minutes"]<=60]
        if len(minutes)<5:excluded["no_pregame_minutes"]+=1;continue
        last3=minutes[-3:];last5=minutes[-5:]
        baseline=avg(minutes)
        recent=avg(last3)
        minutes_change=recent-baseline
        # Historical role volatility proxy; not an actual pregame lineup confirmation.
        volatility=statistics.pstdev(minutes)
        projected=.65*avg(last5)+.35*baseline
        actual_minutes=None
        # Target-game minutes are read ONLY for after-the-fact diagnostics, never for risk grouping.
        same_day=[g for g in history.get(name,[]) if g["date"]==day]
        if same_day:actual_minutes=same_day[0].get("minutes")
        margin=(actual-line)*(1 if side=="OVER" else -1)
        rows.append({"day":day,"player":r.get("player"),"game":r.get("game"),"market":market,
                     "side":side,"line":line,"actual":actual,"margin":margin,
                     "payout":odds/100 if odds>0 else 100/abs(odds),
                     "projected_minutes":round(projected,2),
                     "recent_minutes_change":round(minutes_change,2),
                     "minutes_volatility":round(volatility,2),
                     "actual_minutes":actual_minutes})
    def subset(fn):return profile([r for r in rows if fn(r)])
    dates=sorted({r["day"] for r in rows})
    report={"status":"RESEARCH_ONLY_NO_LIVE_FILTERS",
            "captured_dates":dates,"eligible":len(rows),"excluded":dict(excluded),
            "overall":profile(rows),
            "by_market":{m:profile([r for r in rows if r["market"]==m]) for m in ("PTS","PRA","AST","REB","3PM")},
            "minutes_risk_groups":{
                "recent_minutes_drop_at_least_4":subset(lambda r:r["recent_minutes_change"]<=-4),
                "recent_minutes_increase_at_least_4":subset(lambda r:r["recent_minutes_change"]>=4),
                "stable_recent_minutes":subset(lambda r:abs(r["recent_minutes_change"])<4),
                "volatile_minutes_std_at_least_7":subset(lambda r:r["minutes_volatility"]>=7),
                "stable_minutes_std_below_7":subset(lambda r:r["minutes_volatility"]<7),
                "projected_minutes_below_22":subset(lambda r:r["projected_minutes"]<22),
                "projected_minutes_22_plus":subset(lambda r:r["projected_minutes"]>=22)},
            "by_market_minutes_risk":{m:{
                "recent_drop_4_plus":profile([r for r in rows if r["market"]==m and r["recent_minutes_change"]<=-4]),
                "volatile_7_plus":profile([r for r in rows if r["market"]==m and r["minutes_volatility"]>=7]),
                "other":profile([r for r in rows if r["market"]==m and r["recent_minutes_change"]>-4 and r["minutes_volatility"]<7])
            } for m in ("PTS","PRA")},
            "largest_losses":[{"player":r["player"],"game":r["game"],"market":r["market"],"side":r["side"],
                "line":r["line"],"actual":r["actual"],"loss_margin":-r["margin"],
                "projected_minutes":r["projected_minutes"],"actual_minutes":r["actual_minutes"],
                "recent_minutes_change":r["recent_minutes_change"],"minutes_volatility":r["minutes_volatility"]}
                for r in sorted((r for r in rows if r["margin"]<0),key=lambda r:r["margin"])[:20]],
            "decision":"INSUFFICIENT_INDEPENDENT_DATES" if len(dates)<10 else "REQUIRES_CHRONOLOGICAL_VALIDATION",
            "limitations":["No timestamped injury or confirmed lineup data exists in this audit; do not label rotation proxies as verified injuries.",
                           "Historical minute features use strictly prior games, not target-game minutes.",
                           "Actual minutes are shown after grading only for diagnosing losses.",
                           "Same-game picks remain correlated; capture-date count matters more than raw bet count.",
                           "No post-hoc rule selection, production promotion, or claim of improved ROI."]}
    out=ROOT/"reports/nba_pregame_risk_audit.json";out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({"status":report["status"],"eligible":len(rows),"dates":dates,
                      "minutes_risk_groups":report["minutes_risk_groups"],"decision":report["decision"]},indent=2))
if __name__=="__main__":run()
