"""Compare defense-adjusted challenger and three-model consensus on captured odds.
Research only: reconstructed V4 may overlap evaluation dates.
"""
import json,math,statistics
from pathlib import Path
from collections import Counter
from nba_challenger_model import load,predict,MARKETS
from nba_v4_baseline_audit import features,estimate
from nba_matchup_features import load_team_games,prior_allowances
ROOT=Path(__file__).resolve().parent
PATHS=[str(ROOT/"data"/f"propedge_historical_{s}_minutes.csv") for s in ("2023_24","2024_25","2025_26")]+[str(ROOT/"data/nba_2026_27.csv")]
def metrics(rows,key):
    selected=[r for r in rows if (r[key]-r["line"])*r["direction"]>0]
    wins=[r for r in selected if (r["actual"]-r["line"])*r["direction"]>0]
    losses=[r for r in selected if (r["actual"]-r["line"])*r["direction"]<0]
    return {"matched":len(rows),"eligible":len(selected),"wins":len(wins),"losses":len(losses),
            "pushes":len(selected)-len(wins)-len(losses),
            "roi_pct":round(100*(sum(r["payout"] for r in wins)-len(losses))/len(selected),2) if selected else None,
            "mae":round(statistics.mean(abs(r[key]-r["actual"]) for r in rows),4) if rows else None,
            "large_losses_over_5":sum(abs(r["actual"]-r["line"])>5 for r in losses)}
def run():
    history=load(PATHS);team_games=load_team_games(PATHS)
    coeff=json.loads((ROOT/"data/nba_v4_live_coefficients.json").read_text())["markets"]
    tracking=json.loads((ROOT/"data/model_tracking.json").read_text())
    league=sorted((g["date"],g["allowed"]) for games in team_games.values() for g in games)
    # Resolve opponent from the historical player's game, with an exact
    # captured team-name check where team names are available.
    games={}
    for player,rows in history.items():
        for g in rows:
            games.setdefault((g["date"],g.get("game_id")),set()).add(g.get("team"))
    output=[];excluded=Counter();seen=set()
    for r in tracking:
        if r.get("status") not in ("win","loss","push"):excluded["pending"]+=1;continue
        if r.get("validation_status") in ("invalid_matchup_review","unmatched_completed_schedule"):excluded["identity_review"]+=1;continue
        day=str(r.get("captured_at") or "")[:10].replace("-","")
        name=" ".join(str(r.get("player") or "").casefold().split())
        market=r.get("market");side=r.get("side")
        key=(day,r.get("event_id"),name,market,side,str(r.get("line")))
        if key in seen:excluded["duplicate"]+=1;continue
        seen.add(key)
        if market not in MARKETS or side not in ("OVER","UNDER"):excluded["invalid_market"]+=1;continue
        player_history=history.get(name,[])
        prior=[g for g in player_history if g["date"]<day]
        actual_games=[g for g in player_history if g["date"]==day]
        if len(prior)<12 or len(actual_games)!=1:excluded["history_or_game_missing"]+=1;continue
        game=actual_games[0]
        opponents=games.get((day,game.get("game_id")),set())-{game.get("team")}
        if len(opponents)!=1:excluded["ambiguous_opponent"]+=1;continue
        opponent=next(iter(opponents))
        defense=prior_allowances(team_games,opponent,day)
        reference=[values for d,values in league if d<day][-600:]
        if defense is None or len(reference)<100:excluded["defense_history_missing"]+=1;continue
        try:
            actual=float(r["actual"]);line=float(r["line"]);primary=float(r["projection"]);odds=float(r["odds"])
            if odds==0 or not all(math.isfinite(v) for v in (actual,line,primary,odds)):raise ValueError()
            if abs(actual-game[market])>0.01:excluded["actual_mismatch"]+=1;continue
            challenger=predict(prior)
            params=coeff[market]
            v4=estimate((params["weights"],params["means"],params["scales"]),features(prior,market))
            def adjusted(m):
                idx=MARKETS.index(m)
                avg=sum(x[idx] for x in reference)/len(reference)
                ratio=max(.8,min(1.2,defense[m]/max(1.,avg)))
                return max(0.,challenger[m]*(1+.45*(ratio-1)))
            adj=sum(adjusted(m) for m in ("PTS","REB","AST")) if market=="PRA" else adjusted(market)
            consensus=(primary+challenger[market]+v4)/3
            modified=(primary+adj+v4)/3
        except (ValueError,KeyError,TypeError,ZeroDivisionError,IndexError):excluded["invalid_values"]+=1;continue
        output.append({"date":day,"market":market,"actual":actual,"line":line,
                       "direction":1 if side=="OVER" else -1,
                       "payout":odds/100 if odds>0 else 100/abs(odds),
                       "consensus":consensus,"adjusted":modified})
    report={"status":"RETROSPECTIVE_RESEARCH_ONLY","matched":len(output),
            "dates":sorted(set(r["date"] for r in output)),"excluded":dict(excluded),
            "overall":{k:metrics(output,k) for k in ("consensus","adjusted")},
            "markets":{m:{k:metrics([r for r in output if r["market"]==m],k) for k in ("consensus","adjusted")} for m in MARKETS},
            "limitations":["Historical game matching does not independently verify captured matchup identity.",
                           "V4 coefficients overlap captured dates; results are not independent holdout.",
                           "Only original captured sides and odds are scored.",
                           "No opposite-side hypothetical bets or production changes."]}
    out=ROOT/"reports/nba_defense_consensus_captured_roi.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))
if __name__=="__main__":run()
