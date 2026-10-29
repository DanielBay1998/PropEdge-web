"""Retrospective three-model consensus audit on identical captured NBA lines.

Only grade the originally captured side at its actually recorded odds.
Consensus reversal is not assigned invented opposite-side prices.
"""
import json,math,datetime
from collections import defaultdict,Counter
from pathlib import Path
from nba_challenger_model import load,predict,MARKETS
from nba_v4_baseline_audit import features,estimate
ROOT=Path(__file__).resolve().parent
PATHS=[str(ROOT/"data"/f"propedge_historical_{s}_minutes.csv") for s in ("2023_24","2024_25","2025_26")]+[str(ROOT/"data/nba_2026_27.csv")]
EXCLUDED={"invalid_matchup_review","unmatched_completed_schedule"}

def summary(rows,mode):
    wins=losses=pushes=0;profit=error=0.
    for r in rows:
        p=r["primary"] if mode=="primary" else r["consensus"]
        error+=abs(p-r["actual"])
        edge=(p-r["line"])*(1 if r["side"]=="OVER" else -1)
        if edge<=0:continue
        if r["actual"]==r["line"]:pushes+=1
        elif (r["actual"]>r["line"])==(r["side"]=="OVER"):
            wins+=1;profit+=r["payout"]
        else:
            losses+=1;profit-=1
    n=wins+losses+pushes
    return {"eligible_bets":n,"wins":wins,"losses":losses,"pushes":pushes,
            "flat_stake_roi_pct":round(100*profit/n,2) if n else None,
            "mean_absolute_error_on_all_matched":round(error/len(rows),4) if rows else None}

def run():
    history=load(PATHS)
    coeff=json.loads((ROOT/"data/nba_v4_live_coefficients.json").read_text())["markets"]
    tracking=json.loads((ROOT/"data/model_tracking.json").read_text())
    matched=[];excluded=Counter();seen=set()
    for r in tracking:
        if r.get("status") not in ("win","loss","push"):excluded["pending"]+=1;continue
        if r.get("validation_status") in EXCLUDED:excluded["identity_review"]+=1;continue
        name=" ".join(str(r.get("player") or "").casefold().split())
        market=r.get("market")
        date=str(r.get("captured_at") or "")[:10].replace("-","")
        if market not in MARKETS or len(date)!=8:excluded["invalid_identity"]+=1;continue
        prior=[g for g in history.get(name,[]) if g["date"]<date]
        if len(prior)<8:excluded["insufficient_prior_history"]+=1;continue
        key=(r.get("captured_at"),r.get("event_id"),name,market,r.get("side"),r.get("line"),r.get("book"))
        if key in seen:excluded["duplicate"]+=1;continue
        seen.add(key)
        try:
            primary=float(r["projection"]);line=float(r["line"]);actual=float(r["actual"]);odds=float(r["odds"])
            if not all(math.isfinite(x) for x in (primary,line,actual,odds)) or odds==0:raise ValueError()
            challenger=predict(prior)[market]
            params=coeff[market]
            v4=estimate((params["weights"],params["means"],params["scales"]),features(prior,market))
            consensus=(primary+challenger+v4)/3
            side=r["side"]
            if side not in ("OVER","UNDER"):raise ValueError()
        except (ValueError,TypeError,KeyError,ZeroDivisionError):
            excluded["invalid_prediction"]+=1;continue
        matched.append({"date":date,"market":market,"line":line,"actual":actual,"side":side,
                        "payout":odds/100 if odds>0 else 100/abs(odds),
                        "primary":primary,"consensus":consensus})
    dates=sorted({r["date"] for r in matched})
    report={"status":"RETROSPECTIVE_RESEARCH_ONLY_NOT_PROSPECTIVE",
            "matched":len(matched),"capture_dates":dates,"excluded":dict(excluded),
            "overall":{m:summary(matched,m) for m in ("primary","consensus")},
            "by_market":{market:{m:summary([r for r in matched if r["market"]==market],m) for m in ("primary","consensus")} for market in MARKETS},
            "by_date":{day:{m:summary([r for r in matched if r["date"]==day],m) for m in ("primary","consensus")} for day in dates},
            "limitations":["V4 coefficients were trained using games through coefficient timestamp, which can overlap these retrospective test dates: this is IN-SAMPLE and cannot justify production promotion.",
                           "Opposite-side sportsbook prices were not captured; only originally captured side can be evaluated.",
                           "Consensus predictions were reconstructed retrospectively, not stored before games.",
                           "No live betting qualification or ranking changes."]}
    path=ROOT/"reports/nba_three_model_consensus_audit.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({"status":report["status"],"matched":len(matched),"overall":report["overall"],"excluded":report["excluded"]},indent=2))
if __name__=="__main__":run()
