"""Research-only price/edge filter evaluation on genuine captured bets.
Never selects a strategy on the evaluation date. Insufficient independent
capture dates produce an explicit no-promotion decision.
"""
import json,math
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parent
BAD={"invalid_matchup_review","unmatched_completed_schedule"}
RULES={
 "all_eligible":lambda r:True,
 "price_at_least_minus_120":lambda r:r["odds"]>=-120,
 "price_at_least_minus_110":lambda r:r["odds"]>=-110,
 "edge_at_least_1":lambda r:r["edge"]>=1,
 "edge_at_least_2":lambda r:r["edge"]>=2,
 "edge_1_and_price_minus_120":lambda r:r["edge"]>=1 and r["odds"]>=-120,
 "edge_2_and_price_minus_120":lambda r:r["edge"]>=2 and r["odds"]>=-120,
 "assists_and_pra":lambda r:r["market"] in ("AST","PRA"),
}
def summarize(rows):
    if not rows:return {"n":0,"roi_pct":None,"wins":0,"losses":0,"pushes":0}
    profit=sum(r["profit"] for r in rows)
    return {"n":len(rows),"roi_pct":round(100*profit/len(rows),2),
            "wins":sum(r["result"]=="win" for r in rows),
            "losses":sum(r["result"]=="loss" for r in rows),
            "pushes":sum(r["result"]=="push" for r in rows)}
def run():
    raw=json.loads((ROOT/"data/model_tracking.json").read_text())
    rows=[];seen=set();excluded=defaultdict(int)
    for r in raw:
        if r.get("validation_status") in BAD:
            excluded["identity_review"]+=1;continue
        if r.get("status") not in ("win","loss","push"):
            excluded["pending"]+=1;continue
        try:
            actual=float(r["actual"]);line=float(r["line"]);odds=float(r["odds"])
            projection=float(r["projection"]);side=str(r["side"]).upper()
            if side not in ("OVER","UNDER") or odds==0 or not all(map(math.isfinite,(actual,line,odds,projection))):raise ValueError
        except (ValueError,KeyError,TypeError):
            excluded["invalid_fields"]+=1;continue
        key=(r.get("captured_at"),r.get("event_id"),r.get("player"),r.get("market"),side,line,r.get("book"))
        if key in seen:
            excluded["duplicate"]+=1;continue
        seen.add(key)
        result="push" if actual==line else ("win" if (actual>line)==(side=="OVER") else "loss")
        if result!=r["status"]:
            excluded["grade_mismatch"]+=1;continue
        payout=(odds/100 if odds>0 else 100/abs(odds))
        rows.append({"date":str(r.get("captured_at",""))[:10],"market":r.get("market"),
                     "odds":odds,"edge":(projection-line)*(1 if side=="OVER" else -1),
                     "result":result,"profit":payout if result=="win" else (-1 if result=="loss" else 0)})
    dates=sorted({r["date"] for r in rows})
    results={}
    for name,rule in RULES.items():
        selected=[r for r in rows if rule(r)]
        results[name]={"all":summarize(selected),
                       "by_capture_date":{d:summarize([r for r in selected if r["date"]==d]) for d in dates}}
    report={"status":"EXPLORATORY_ONLY_NO_PROMOTION",
            "eligible_capture_dates":dates,"eligible_picks":len(rows),
            "rules":results,"excluded":dict(excluded),
            "decision":"INSUFFICIENT_INDEPENDENT_DATES" if len(dates)<10 else "REQUIRES_FUTURE_DATE_HOLDOUT",
            "limitations":["Rules are predeclared and not selected by highest historical ROI",
                           "Captures from only a few dates cannot validate future ROI",
                           "Correlated picks within a game are not independent",
                           "Model projection versus line is not a calibrated probability or positive EV",
                           "No production changes or automated promotion"]}
    out=ROOT/"reports/nba_v4_selection_filters.json";out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))
if __name__=="__main__":run()
