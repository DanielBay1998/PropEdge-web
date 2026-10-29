"""Auditable, date-scoped model evaluation. Never invent historical sportsbook odds."""
import argparse, collections, datetime, json, math
from pathlib import Path

def evaluate(rows):
    groups=collections.defaultdict(list)
    excluded=collections.Counter()
    capture_dates=set()
    for r in rows:
        try:
            stamp=datetime.datetime.fromisoformat(str(r["captured_at"]).replace("Z","+00:00"))
            day=stamp.date().isoformat()
            capture_dates.add(day)
        except (ValueError,KeyError,TypeError):
            excluded["invalid_timestamp"]+=1
            continue
        if r.get("status") not in ("win","loss","push"):
            excluded["pending_or_ungraded"]+=1
            continue
        if r.get("validation_status") in ("invalid_matchup_review","unmatched_completed_schedule"):
            excluded["identity_or_schedule_review"]+=1
            continue
        try:
            actual=float(r["actual"]);line=float(r["line"]);projection=float(r["projection"])
            if not all(math.isfinite(v) for v in (actual,line,projection)):raise ValueError()
            if r.get("side") not in ("OVER","UNDER"):raise ValueError()
        except (ValueError,TypeError,KeyError):
            excluded["invalid_numbers_or_side"]+=1
            continue
        market=str(r.get("market") or "UNKNOWN")
        book=str(r.get("book") or "UNKNOWN")
        model=str(r.get("model_version") or "unversioned")
        for dimension,key in (("date",day),("season",day[:4]),("market",market),("book",book),("model",model),("overall","all")):
            groups[(dimension,key)].append(r)
    def stats(items):
        w=sum(r["status"]=="win" for r in items)
        l=sum(r["status"]=="loss" for r in items)
        p=sum(r["status"]=="push" for r in items)
        errors=[abs(float(r["projection"])-float(r["actual"])) for r in items]
        profit=[];priced=0
        for r in items:
            try:
                odds=float(r["odds"])
                if not math.isfinite(odds) or odds==0:continue
                priced+=1
                payout=odds/100 if odds>0 else 100/abs(odds)
                profit.append(payout if r["status"]=="win" else -1 if r["status"]=="loss" else 0)
            except (ValueError,TypeError,KeyError):continue
        return {"graded":len(items),"wins":w,"losses":l,"pushes":p,
                "hit_rate_pct":round(100*w/(w+l),2) if w+l else None,
                "mae":round(sum(errors)/len(errors),4) if errors else None,
                "priced":priced,"flat_roi_pct":round(100*sum(profit)/priced,2) if priced else None}
    return {"status":"OBSERVED_CAPTURED_PICKS_ONLY","distinct_capture_dates":len(capture_dates),
            "distinct_graded_dates":len({k for d,k in groups if d=="date"}),
            "excluded":dict(excluded),
            "by_dimension":{dim:{key:stats(v) for (d,key),v in sorted(groups.items()) if d==dim}
                            for dim in ("overall","date","season","market","book","model")},
            "limitations":["Past box scores without timestamped pregame odds are not betting captures",
                           "Unversioned historical records cannot prove a model version",
                           "This is observational performance, not a randomized or independent backtest"]}
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--input",default="data/model_tracking.json")
    p.add_argument("--output",default="reports/multidate_model_performance.json")
    a=p.parse_args()
    rows=json.loads(Path(a.input).read_text())
    if not isinstance(rows,list):raise ValueError("Tracking must be a list")
    report=evaluate(rows)
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({"distinct_capture_dates":report["distinct_capture_dates"],"distinct_graded_dates":report["distinct_graded_dates"],"overall":report["by_dimension"]["overall"],"excluded":report["excluded"]},indent=2))
if __name__=="__main__":main()
