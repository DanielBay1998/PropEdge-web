"""Audit genuinely captured sportsbook selections and prices; no reconstructed bets.
Only uses the direction and American odds recorded at capture time.
"""
import json,math
from collections import Counter,defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parent
EXCLUDED={"invalid_matchup_review","unmatched_completed_schedule"}

def implied(odds):
    return 100/(odds+100) if odds>0 else -odds/(-odds+100)

def run():
    rows=json.loads((ROOT/"data/model_tracking.json").read_text())
    totals=defaultdict(lambda:{"n":0,"wins":0,"losses":0,"pushes":0,"profit":0.0,"implied_sum":0.0})
    excluded=Counter()
    seen=set()
    for r in rows:
        if r.get("validation_status") in EXCLUDED:
            excluded["identity_or_schedule_review"]+=1;continue
        if r.get("status") not in ("win","loss","push"):
            excluded["pending_or_ungraded"]+=1;continue
        try:
            line=float(r["line"]);actual=float(r["actual"]);odds=float(r["odds"])
            side=str(r["side"]).upper()
            if side not in ("OVER","UNDER") or not all(math.isfinite(x) for x in (line,actual,odds)) or odds==0:raise ValueError()
        except (ValueError,TypeError,KeyError):
            excluded["missing_or_invalid_capture"]+=1;continue
        key=(r.get("captured_at"),r.get("event_id"),r.get("player"),r.get("market"),side,line,r.get("book"))
        if key in seen:
            excluded["duplicate_capture"]+=1;continue
        seen.add(key)
        result="push" if actual==line else "win" if (actual>line)==(side=="OVER") else "loss"
        if result!=r["status"]:
            excluded["grading_mismatch"]+=1;continue
        profit=(odds/100 if odds>0 else 100/abs(odds)) if result=="win" else (-1 if result=="loss" else 0)
        date=str(r.get("captured_at",""))[:10]
        for label in ("overall","market:"+str(r.get("market")),"capture_date:"+date):
            t=totals[label];t["n"]+=1;t[result+"es" if result=="loss" else result+"s"]+=1
            t["profit"]+=profit;t["implied_sum"]+=implied(odds)
    report={}
    for label,t in totals.items():
        n=t["n"];decisions=t["wins"]+t["losses"]
        report[label]={"n":n,"wins":t["wins"],"losses":t["losses"],"pushes":t["pushes"],
                       "hit_rate_pct":round(100*t["wins"]/decisions,2) if decisions else None,
                       "flat_roi_pct":round(100*t["profit"]/n,2),
                       "average_implied_probability_pct":round(100*t["implied_sum"]/n,2)}
    output={"status":"ACTUAL_CAPTURED_SIDES_AND_ODDS_ONLY",
            "results":report,"excluded":dict(excluded),
            "limitations":["Captured primary picks only; V4 regression was not forecast at capture time",
                           "No opposite-side prices assumed",
                           "Small sample and potential correlated bets; not proof of future profitability",
                           "Future V4 predictions require immutable timestamped pregame snapshots"]}
    dest=ROOT/"reports/nba_v4_captured_odds_audit.json";dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(output,indent=2)+"\n")
    print(json.dumps(output,indent=2))
if __name__=="__main__":run()
