"""Research-only chronological calibration audit for immutable PropEdge NBA picks.

No production publishing. Never selects thresholds using the evaluation fold.
"""
import json, math, argparse
from collections import defaultdict
from pathlib import Path

def implied(odds):
    o=float(odds)
    return 100/(o+100) if o>0 else -o/(-o+100)

def payout(odds):
    o=float(odds)
    return o/100 if o>0 else 100/-o

def audit(records, min_train=80):
    graded=[]
    for r in records:
        if r.get("status") not in ("win","loss","push"):continue
        try:
            projection=float(r["projection"]); line=float(r["line"]); odds=float(r["odds"])
            if not all(map(math.isfinite,(projection,line,odds))) or odds==0:continue
            direction=1 if r["side"]=="OVER" else -1
            edge=direction*(projection-line)
            if edge<0:continue
            graded.append(dict(r,edge=edge,prob_break_even=implied(odds)))
        except (ValueError,TypeError,KeyError):continue
    graded.sort(key=lambda r:(r.get("captured_at",""),r.get("id","")))
    # Immutable chronological split, grouped by capture date to avoid same-day leakage.
    dates=sorted(set(r["captured_at"][:10] for r in graded))
    if len(dates)<4 or len(graded)<min_train+40:
        return {"status":"INSUFFICIENT_OUT_OF_SAMPLE_DATA","graded_eligible":len(graded),
                "capture_dates":len(dates),"reason":"Need >=4 capture dates and >=120 eligible graded picks"}
    cutoff=dates[max(1,int(len(dates)*0.7))-1]
    train=[r for r in graded if r["captured_at"][:10]<=cutoff]
    test=[r for r in graded if r["captured_at"][:10]>cutoff]
    if len(train)<min_train or len(test)<40:
        return {"status":"INSUFFICIENT_OUT_OF_SAMPLE_DATA","train":len(train),"test":len(test)}
    def metrics(rows):
        settled=[r for r in rows if r["status"]!="push"]
        wins=sum(r["status"]=="win" for r in settled)
        profit=sum(payout(r["odds"]) if r["status"]=="win" else -1 for r in settled)
        return {"n":len(settled),"wins":wins,"hit_rate":round(wins/len(settled),4) if settled else None,
                "flat_stake_roi":round(profit/len(settled),4) if settled else None}
    # Threshold is fit ONLY on earlier dates; no use of held-out results.
    thresholds=(0,0.5,1,1.5,2,3,4)
    candidates=[]
    for t in thresholds:
        subset=[r for r in train if r["edge"]>=t]
        m=metrics(subset)
        if m["n"]>=30:candidates.append((m["flat_stake_roi"],m["n"],t))
    if not candidates:return {"status":"INSUFFICIENT_TRAINING_DATA","train":len(train),"test":len(test)}
    chosen=max(candidates,key=lambda x:(x[0],x[1]))[2]
    by_market={}
    for market in sorted(set(r["market"] for r in test)):
        rows=[r for r in test if r["market"]==market]
        by_market[market]={"baseline":metrics(rows),"challenger":metrics([r for r in rows if r["edge"]>=chosen])}
    return {"status":"RESEARCH_ONLY_NOT_APPROVED","cutoff":cutoff,"train":metrics(train),
            "test_baseline":metrics(test),"selected_edge_threshold":chosen,
            "test_challenger":metrics([r for r in test if r["edge"]>=chosen]),
            "by_market":by_market,
            "warning":"Historical sample is small; this is a selection-filter audit, not a new projection model or evidence of future profitability."}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--input",default="data/model_tracking.json")
    p.add_argument("--output",default="reports/nba_calibration_audit.json")
    a=p.parse_args()
    result=audit(json.loads(Path(a.input).read_text(encoding="utf-8")))
    dest=Path(a.output);dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,indent=2))
if __name__=="__main__":main()
