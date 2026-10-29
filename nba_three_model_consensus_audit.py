"""Retrospective three-model consensus audit on identical captured NBA lines.

Only grade the originally captured side at its actually recorded odds.
Consensus reversal is not assigned invented opposite-side prices.
"""
import json,math,datetime,statistics
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

def error_profile(rows,model):
    if not rows:return {"n":0}
    errors=[abs(r[model]-r["actual"]) for r in rows]
    signed=[r[model]-r["actual"] for r in rows]
    ordered=sorted(errors)
    def percentile(p):
        return round(ordered[min(len(ordered)-1,math.ceil(p*len(ordered))-1)],3)
    return {"n":len(rows),
            "mae":round(statistics.mean(errors),3),
            "median_absolute_error":round(statistics.median(errors),3),
            "p75_absolute_error":percentile(.75),
            "p90_absolute_error":percentile(.9),
            "maximum_absolute_error":round(max(errors),3),
            "mean_signed_error_projection_minus_actual":round(statistics.mean(signed),3),
            "within_0_5_pct":round(100*sum(e<=.5 for e in errors)/len(errors),2),
            "within_1_pct":round(100*sum(e<=1 for e in errors)/len(errors),2),
            "within_2_pct":round(100*sum(e<=2 for e in errors)/len(errors),2),
            "within_3_pct":round(100*sum(e<=3 for e in errors)/len(errors),2),
            "within_5_pct":round(100*sum(e<=5 for e in errors)/len(errors),2)}

def betting_margin_profile(rows):
    """Outcome margins measured in stat units from the captured sportsbook line."""
    wins=[];losses=[];pushes=0
    for r in rows:
        margin=(r["actual"]-r["line"])*(1 if r["side"]=="OVER" else -1)
        if margin>0:wins.append(margin)
        elif margin<0:losses.append(-margin)
        else:pushes+=1
    def stats(values):
        return {"count":len(values),
                "average":round(statistics.mean(values),3) if values else None,
                "median":round(statistics.median(values),3) if values else None,
                "within_0_5":sum(v<=.5 for v in values),
                "within_1":sum(v<=1 for v in values),
                "within_2":sum(v<=2 for v in values),
                "within_3":sum(v<=3 for v in values),
                "more_than_5":sum(v>5 for v in values)}
    return {"wins":stats(wins),"losses":stats(losses),"pushes":pushes,
            "loss_distance_definition":"Actual result short of winning side of captured sportsbook line; not distance from model projection",
            "win_distance_definition":"Actual result beyond captured sportsbook line in chosen direction"}

def losing_pick_diagnostics(rows):
    """Find weak spots without optimizing thresholds on the same games."""
    unique={}
    for r in rows:
        key=(r["date"],r["game"],r["player"],r["market"],r["side"],r["line"])
        unique.setdefault(key,r)
    picks=list(unique.values())
    def breakdown(group):
        if not group:return {"n":0}
        losses=[r for r in group if (r["actual"]-r["line"])*(1 if r["side"]=="OVER" else -1)<0]
        wins=[r for r in group if (r["actual"]-r["line"])*(1 if r["side"]=="OVER" else -1)>0]
        profit=sum(r["payout"] for r in wins)-len(losses)
        margins=[abs(r["actual"]-r["line"]) for r in losses]
        return {"n":len(group),"wins":len(wins),"losses":len(losses),
                "win_rate_pct":round(100*len(wins)/(len(wins)+len(losses)),2) if wins or losses else None,
                "flat_roi_pct":round(100*profit/len(group),2),
                "losses_within_0_5":sum(x<=.5 for x in margins),
                "losses_within_2":sum(x<=2 for x in margins),
                "losses_more_than_5":sum(x>5 for x in margins),
                "average_loss_margin":round(statistics.mean(margins),2) if margins else None}
    def edge(r):
        return (r["consensus"]-r["line"])*(1 if r["side"]=="OVER" else -1)
    def edge_bucket(r):
        e=edge(r)
        if e<0:return "consensus_disagrees_with_pick"
        if e<1:return "consensus_edge_0_to_1"
        if e<2:return "consensus_edge_1_to_2"
        if e<4:return "consensus_edge_2_to_4"
        return "consensus_edge_4_plus"
    buckets=sorted({edge_bucket(r) for r in picks})
    losses=sorted((r for r in picks if (r["actual"]-r["line"])*(1 if r["side"]=="OVER" else -1)<0),
                  key=lambda r:abs(r["actual"]-r["line"]),reverse=True)
    return {"deduplicated_unique_picks":len(picks),"duplicate_captures_removed":len(rows)-len(picks),
            "overall":breakdown(picks),
            "by_market":{m:breakdown([r for r in picks if r["market"]==m]) for m in MARKETS},
            "by_consensus_edge_bucket":{b:breakdown([r for r in picks if edge_bucket(r)==b]) for b in buckets},
            "by_market_and_consensus_edge_bucket":{m:{b:breakdown([r for r in picks if r["market"]==m and edge_bucket(r)==b]) for b in buckets} for m in MARKETS},
            "largest_losing_margins":[{"player":r["player"],"game":r["game"],"market":r["market"],"side":r["side"],"line":r["line"],"actual":r["actual"],"loss_margin":abs(r["actual"]-r["line"]),"consensus":round(r["consensus"],2),"consensus_edge_on_pick":round(edge(r),2)} for r in losses[:20]],
            "limitations":["Deduplication removes repeated captures of same game/player/market/side/line, keeping the first captured record.",
                           "Edge buckets are retrospective and V4 coefficients overlap test dates; DO NOT treat bucket rankings as validated selection rules.",
                           "Odds are only for captured side; no hypothetical reversed-side ROI."]}

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
                        "primary":primary,"challenger":challenger,"v4":v4,"consensus":consensus,"player":r.get("player"),"game":r.get("game")})
    dates=sorted({r["date"] for r in matched})
    report={"status":"RETROSPECTIVE_RESEARCH_ONLY_NOT_PROSPECTIVE",
            "matched":len(matched),"capture_dates":dates,"excluded":dict(excluded),
            "overall":{m:summary(matched,m) for m in ("primary","consensus")},
            "by_market":{market:{m:summary([r for r in matched if r["market"]==market],m) for m in ("primary","consensus")} for market in MARKETS},
            "by_date":{day:{m:summary([r for r in matched if r["date"]==day],m) for m in ("primary","consensus")} for day in dates},
            "losing_pick_diagnostics":losing_pick_diagnostics(matched),
            "betting_margins":{
                "overall":betting_margin_profile(matched),
                "by_market":{market:betting_margin_profile([r for r in matched if r["market"]==market]) for market in MARKETS},
                "by_capture_date":{day:betting_margin_profile([r for r in matched if r["date"]==day]) for day in dates},
                "note":"The same captured picks and sides were used for primary and consensus. Margins are realized betting outcomes, not a test of consensus-selected bets."
            },
            "error_analysis":{
                "overall":{m:error_profile(matched,m) for m in ("primary","challenger","v4","consensus")},
                "by_market":{market:{m:error_profile([r for r in matched if r["market"]==market],m) for m in ("primary","challenger","v4","consensus")} for market in MARKETS},
                "by_capture_date":{day:{m:error_profile([r for r in matched if r["date"]==day],m) for m in ("primary","challenger","v4","consensus")} for day in dates},
                "consensus_better_than_primary_count":sum(abs(r["consensus"]-r["actual"])<abs(r["primary"]-r["actual"]) for r in matched),
                "consensus_worse_than_primary_count":sum(abs(r["consensus"]-r["actual"])>abs(r["primary"]-r["actual"]) for r in matched),
                "worst_consensus_misses":[{"player":r["player"],"game":r["game"],"date":r["date"],"market":r["market"],"actual":r["actual"],"primary":round(r["primary"],2),"challenger":round(r["challenger"],2),"v4":round(r["v4"],2),"consensus":round(r["consensus"],2),"absolute_miss":round(abs(r["consensus"]-r["actual"]),2)} for r in sorted(matched,key=lambda r:abs(r["consensus"]-r["actual"]),reverse=True)[:15]]
            },
            "limitations":["V4 coefficients were trained using games through coefficient timestamp, which can overlap these retrospective test dates: this is IN-SAMPLE and cannot justify production promotion.",
                           "Opposite-side sportsbook prices were not captured; only originally captured side can be evaluated.",
                           "Consensus predictions were reconstructed retrospectively, not stored before games.",
                           "No live betting qualification or ranking changes."]}
    path=ROOT/"reports/nba_three_model_consensus_audit.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({"status":report["status"],"matched":len(matched),"overall":report["overall"],"excluded":report["excluded"]},indent=2))
if __name__=="__main__":run()
