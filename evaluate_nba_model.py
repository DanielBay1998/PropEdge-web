"""Audit settled PropEdge NBA picks without modifying production predictions.

Usage: python evaluate_nba_model.py
Outputs: data/nba_model_evaluation.json
Historical segmentation is descriptive; challenger candidates are NOT validated.
"""
import collections
import datetime
import json
import math
from pathlib import Path

ROOT=Path(__file__).resolve().parent
SOURCE=ROOT/"data"/"model_tracking.json"
OUTPUT=ROOT/"data"/"nba_model_evaluation.json"

def payout(odds):
    try:
        o=float(odds)
        if not math.isfinite(o) or o==0:return None
        return o/100 if o>0 else 100/abs(o)
    except (ValueError,TypeError):return None

def summarize(rows):
    wins=sum(r["status"]=="win" for r in rows)
    losses=sum(r["status"]=="loss" for r in rows)
    pushes=sum(r["status"]=="push" for r in rows)
    priced=[r for r in rows if payout(r.get("odds")) is not None]
    profit=sum(payout(r["odds"]) if r["status"]=="win" else (-1 if r["status"]=="loss" else 0) for r in priced)
    errors=[]
    for r in rows:
        try:
            a=float(r["actual"]);p=float(r["projection"])
            if math.isfinite(a) and math.isfinite(p):errors.append(p-a)
        except (ValueError,TypeError,KeyError):pass
    return {"bets":len(rows),"wins":wins,"losses":losses,"pushes":pushes,
      "win_rate":round(wins/(wins+losses),4) if wins+losses else None,
      "priced_bets":len(priced),"net_units":round(profit,4),
      "roi":round(profit/len(priced),4) if priced else None,
      "projection_bias":round(sum(errors)/len(errors),4) if errors else None,
      "mae":round(sum(abs(x) for x in errors)/len(errors),4) if errors else None}

def segment(rows,field):
    groups=collections.defaultdict(list)
    for r in rows:groups[str(r.get(field) or "unknown")].append(r)
    return {k:summarize(v) for k,v in sorted(groups.items())}

def main():
    data=json.loads(SOURCE.read_text())
    settled=[r for r in data if r.get("status") in ("win","loss","push")]
    # Never count duplicate event/player/market/side/line snapshots twice.
    unique={}
    for r in sorted(settled,key=lambda x:str(x.get("captured_at") or "")):
        key=(str(r.get("event_id")),str(r.get("player")).casefold(),str(r.get("market")),
             str(r.get("side")),str(r.get("line")))
        unique.setdefault(key,r)
    rows=list(unique.values())
    edges=collections.defaultdict(list)
    for r in rows:
        try:
            line=float(r["line"]);projection=float(r["projection"])
            if not math.isfinite(line) or not math.isfinite(projection):continue
            directional=(projection-line)*(1 if r.get("side")=="OVER" else -1)
            bucket="negative" if directional<0 else ("0–0.99" if directional<1 else ("1–2.99" if directional<3 else "3+"))
            edges[bucket].append(r)
        except (ValueError,TypeError,KeyError):pass
    # Time-ordered holdout: no optimization or parameter fitting on holdout.
    ordered=sorted(rows,key=lambda r:str(r.get("captured_at") or ""))
    split=int(len(ordered)*.7)
    train,test=ordered[:split],ordered[split:]
    candidate=[]
    for market in sorted({r.get("market") for r in train}):
        subset=[r for r in train if r.get("market")==market]
        stats=summarize(subset)
        if len(subset)>=20 and stats["roi"] is not None and stats["roi"]>0:
            candidate.append(market)
    # Informational shadow filter only; not a valid independent test when
    # predictions share games or capture timestamps.
    held=[r for r in test if r.get("market") in candidate]
    report={"generated_at":datetime.datetime.now(datetime.timezone.utc).isoformat(),
      "source":"saved pregame predictions with settled outcomes",
      "production_unchanged":True,"total_records":len(data),
      "settled_records":len(settled),"deduplicated_settled":len(rows),
      "pending":sum(r.get("status")=="pending" for r in data),
      "overall":summarize(rows),
      "by_market":segment(rows,"market"),"by_side":segment(rows,"side"),
      "by_book":segment(rows,"book"),"by_game":segment(rows,"game"),
      "by_edge":{k:summarize(v) for k,v in sorted(edges.items())},
      "exploratory_challenger":{"method":"train-only profitable markets, minimum 20 train bets",
        "train_count":len(train),"test_count":len(test),
        "candidate_markets":candidate,"train":summarize(train),
        "holdout_baseline":summarize(test),"holdout_filtered":summarize(held),
        "validated":False,
        "limitations":"Overlapping players/games and a short historical period; forward holdout required before deployment."}}
    # Strict paired evaluation: only predictions saved before the game, never backfilled.
    paired=[r for r in rows if r.get("shadow_minutes_projection") is not None
            and r.get("projection") is not None and r.get("actual") is not None]
    shadow_groups=collections.defaultdict(list)
    for r in paired:
        try:
            a=float(r["actual"]);p=float(r["projection"]);q=float(r["shadow_minutes_projection"])
            if not all(math.isfinite(x) for x in (a,p,q)):continue
            shadow_groups[str(r.get("market") or "unknown")].append((r,a,p,q))
        except (ValueError,TypeError):continue
    shadow_report={}
    for market,group in sorted(shadow_groups.items()):
        base_mae=sum(abs(p-a) for _,a,p,q in group)/len(group)
        shadow_mae=sum(abs(q-a) for _,a,p,q in group)/len(group)
        # Same captured side/line and odds; this measures forecast error only.
        # A different side decision requires a separately frozen candidate bet.
        shadow_report[market]={"paired_predictions":len(group),
            "production_mae":round(base_mae,5),"minutes_shadow_mae":round(shadow_mae,5),
            "mae_improvement":round(base_mae-shadow_mae,5)}
    report["minutes_shadow"]={"status":"CAPTURE_TIME_PAIRED_AUDIT",
        "paired_total":sum(len(v) for v in shadow_groups.values()),
        "by_market":shadow_report,"production_unchanged":True,
        "validated":False,"note":"Only newly captured predictions with saved shadow values count. No retroactive candidate forecasts or unverified ROI."}
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT.write_text(json.dumps(report,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"overall":report["overall"],"challenger":report["exploratory_challenger"]},indent=2))

if __name__=="__main__":main()
