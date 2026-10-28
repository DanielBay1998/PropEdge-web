"""Walk-forward, out-of-time evaluation of minutes × per-minute production.

Uses only previous games to estimate minutes and production rate. Missing
teammate box-score rows are an absence proxy, NOT verified injuries.
Research only; never changes official PropEdge projections.
"""
import json,math
from collections import defaultdict
from backtest_injuries import read_logs, MARKETS

def audit(data):
    teams=defaultdict(list)
    for (day,gid,team),players in data.items():
        teams[team].append((day,gid,{p:s for p,s in players}))
    totals=defaultdict(lambda:{"n":0,"baseline_abs":0.0,"minutes_abs":0.0,"scenario_abs":0.0})
    games_with_minutes=0
    games_total=0
    for team,games in teams.items():
        games.sort(key=lambda g:(g[0],g[1]))
        games_total+=len(games)
        games_with_minutes+=sum(any(s.get("MIN") for s in roster.values()) for _,_,roster in games)
        for idx,(_,_,roster) in enumerate(games):
            prior=games[:idx]
            if len(prior)<12:continue
            for player,actual in roster.items():
                if not actual.get("MIN"):continue
                history=[r[player] for _,_,r in prior if player in r and r[player].get("MIN")]
                if len(history)<10:continue
                last=history[-10:]
                expected_minutes=sum(s["MIN"] for s in last)/len(last)
                if expected_minutes<=0:continue
                candidates=[]
                for mate in {p for _,_,r in prior[-30:] for p in r if p!=player and p not in roster}:
                    with_mate=[r[player] for _,_,r in prior if player in r and mate in r and r[player].get("MIN")]
                    without=[r[player] for _,_,r in prior if player in r and mate not in r and r[player].get("MIN")]
                    if len(with_mate)<5 or len(without)<5:continue
                    on=sum(s["MIN"] for s in with_mate)/len(with_mate)
                    off=sum(s["MIN"] for s in without)/len(without)
                    if on<=0:continue
                    candidates.append((min(len(with_mate),len(without)),mate,max(-.15,min(.15,.35*(off/on-1)))))
                if not candidates:continue
                _,mate,shift=max(candidates,key=lambda x:x[0])
                for market in MARKETS:
                    def value(s):return sum(s[k] for k in ("PTS","REB","AST")) if market=="PRA" else s[market]
                    baseline=sum(value(s) for s in last)/len(last)
                    # Aggregate production / aggregate minutes; no future data.
                    rate=sum(value(s) for s in last)/sum(s["MIN"] for s in last)
                    minutes_pred=expected_minutes*rate
                    scenario_pred=max(0,expected_minutes*(1+shift)*rate)
                    y=value(actual)
                    t=totals[market];t["n"]+=1
                    t["baseline_abs"]+=abs(y-baseline)
                    t["minutes_abs"]+=abs(y-minutes_pred)
                    t["scenario_abs"]+=abs(y-scenario_pred)
    results={}
    for market,t in totals.items():
        n=t["n"]
        results[market]={"samples":n,"baseline_mae":round(t["baseline_abs"]/n,3),
                         "minutes_rate_mae":round(t["minutes_abs"]/n,3),
                         "absence_minutes_scenario_mae":round(t["scenario_abs"]/n,3),
                         "scenario_mae_change_pct":round(100*(t["scenario_abs"]/t["baseline_abs"]-1),2) if t["baseline_abs"] else None}
    return {"status":"evaluated" if results else "insufficient_minutes_data",
            "method":"Chronological prior-only trailing 10 games; expected minutes × aggregate per-minute production; capped/shrunk absence-proxy minutes adjustment",
            "team_games":games_total,"team_games_with_any_minutes":games_with_minutes,
            "results":results,
            "verified_historical_injury_designations":False,
            "limitations":["Missing teammate box-score rows are not confirmed injuries; roster changes and data gaps can bias estimates.",
                           "The minutes-rate comparator equals the trailing average algebraically; it is a decomposition, not an independent model.",
                           "This is not the deployed shadow model, and it has no independent held-out validation."],
            "promote_to_official_picks":False}

if __name__=="__main__":
    print(json.dumps(audit(read_logs()),indent=2))
