"""Leakage-safe NBA challenger: minutes-normalized historical player projections.

Research-only: never changes live board or production projections.
Uses prior games only, separates seasons, caps minute estimates and shrinks
small samples toward the player's longer-run rate.
"""
import argparse,csv,json,math
from collections import defaultdict
from pathlib import Path

MARKETS=("PTS","REB","AST","3PM","PRA")
def load(paths):
    games=defaultdict(dict)
    for path in paths:
        if not Path(path).exists():continue
        with open(path,encoding="utf-8-sig",newline="") as f:
            for r in csv.DictReader(f):
                day=str(r.get("Date","")); name=" ".join(str(r.get("Player","")).casefold().split())
                if len(day)!=8 or not day.isdigit() or not name:continue
                if "historical" in path and str(r.get("Season Type","")).casefold()!="regular":continue
                if "2026_27" in path and day<"20261020":continue
                try:
                    stats={k:float(r[k]) for k in MARKETS[:4]}
                    mins=float(r.get("MIN") or "nan")
                    if any(not math.isfinite(v) or v<0 for v in stats.values()):continue
                    if not math.isfinite(mins) or not 1<=mins<=60:mins=None
                except (ValueError,TypeError,KeyError):continue
                stats["PRA"]=sum(stats[k] for k in ("PTS","REB","AST"))
                key=(day,str(r.get("Game ID","")))
                games[name][key]={"date":day,"minutes":mins,"team":str(r.get("Team") or "").strip().casefold(),"game_id":str(r.get("Game ID") or ""),**stats}
    return {name:sorted(rows.values(),key=lambda x:x["date"]) for name,rows in games.items()}

def predict(history):
    if len(history)<8:return None
    # Only observed player games are eligible; do not infer injury or pace data.
    if any(r.get("minutes") is not None and (r["minutes"]<1 or r["minutes"]>60) for r in history):
        return None
    recent=history[-8:]; long=history[-30:]
    minute_rows=[r for r in long if r["minutes"] is not None and r["minutes"]>=8]
    projected_minutes=None
    if len(minute_rows)>=8:
        recent_minutes=[r["minutes"] for r in minute_rows[-5:]]
        all_minutes=[r["minutes"] for r in minute_rows]
        projected_minutes=max(8,min(42,0.7*sum(recent_minutes)/len(recent_minutes)
                                   +0.3*sum(all_minutes)/len(all_minutes)))
    output={}
    for k in MARKETS[:4]:
        recent_avg=sum(r[k] for r in recent)/len(recent)
        long_avg=sum(r[k] for r in long)/len(long)
        # Short-term role is informative, but regress toward a longer sample.
        baseline=0.45*recent_avg+0.55*long_avg
        if projected_minutes is not None:
            recent_rate_rows=minute_rows[-8:]
            recent_rate=sum(r[k] for r in recent_rate_rows)/sum(r["minutes"] for r in recent_rate_rows)
            long_rate=sum(r[k] for r in minute_rows)/sum(r["minutes"] for r in minute_rows)
            rate=0.45*recent_rate+0.55*long_rate
            minutes_estimate=rate*projected_minutes
            baseline=0.4*baseline+0.6*minutes_estimate
        # Guard against extreme historical projections when a new role has
        # emerged in at least four current-season observations.
        current=[r for r in long if str(r.get("date",""))>="20261020"]
        if len(current)>=4:
            current_mean=sum(r[k] for r in current[-8:])/len(current[-8:])
            baseline=0.65*baseline+0.35*current_mean
        output[k]=max(0,round(baseline,3))
    output["PRA"]=sum(output[k] for k in ("PTS","REB","AST"))
    return output

def evaluate(games):
    rows=[]
    for player,history in games.items():
        for i in range(8,len(history)):
            # Strict earlier date: exclude same-day rows, including doubleheaders.
            day=history[i]["date"]
            prior=[r for r in history[:i] if r["date"]<day]
            pred=predict(prior)
            if pred is None:continue
            target=history[i]
            for market in MARKETS:
                baseline=sum(r[market] for r in prior[-10:])/min(10,len(prior))
                rows.append({"date":day,"player":player,"market":market,
                             "actual":target[market],"challenger":pred[market],"baseline":baseline})
    dates=sorted(set(r["date"] for r in rows))
    # Holdout is most recent 30% of unique dates; no threshold tuning on holdout.
    split=dates[int(len(dates)*0.7)] if len(dates)>=10 else None
    def stats(items):
        if not items:return {"n":0}
        n=len(items)
        return {"n":n,"challenger_mae":round(sum(abs(r["challenger"]-r["actual"]) for r in items)/n,3),
                "baseline_mae":round(sum(abs(r["baseline"]-r["actual"]) for r in items)/n,3),
                "challenger_bias":round(sum(r["challenger"]-r["actual"] for r in items)/n,3)}
    result={"status":"RESEARCH_ONLY_NOT_PROMOTED","holdout_start":split,
            "games_available":sum(len(v) for v in games.values()),
            "limits":["No verified opponent defense or injury information","No sportsbook odds or profitability proof",
                      "Minutes adjustments only where real historical minutes exist",
                      "No live projections changed"]}
    if split:
        hold=[r for r in rows if r["date"]>=split]
        result["holdout"]={m:stats([r for r in hold if r["market"]==m]) for m in MARKETS}
        result["holdout"]["overall"]=stats(hold)
    else:
        result["status"]="INSUFFICIENT_DATES_FOR_HOLDOUT"
    return result

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--current",default="data/nba_2026_27.csv")
    p.add_argument("--historical",default="data/propedge_historical_2025_26_minutes.csv")
    p.add_argument("--output",default="reports/nba_challenger_validation.json")
    a=p.parse_args()
    result=evaluate(load([a.historical,a.current]))
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(result,indent=2))
if __name__=="__main__":main()
