"""Research-only NBA role, teammate-absence and opponent context ablation.
All features use completed games strictly earlier than the target date.
Teammate absence is inferred from past participation, NOT an injury report.
Boxscore team totals proxy pace/defense; no possessions are available.
"""
import json
from pathlib import Path
from collections import defaultdict
from nba_challenger_model import load,predict,MARKETS
from nba_matchup_features import load_team_games,prior_allowances

ROOT=Path(__file__).resolve().parent
PATHS=[str(ROOT/"data"/f"propedge_historical_{season}_minutes.csv") for season in ("2023_24","2024_25","2025_26")]+[str(ROOT/"data/nba_2026_27.csv")]
def avg(rows,key):
    return sum(r[key] for r in rows)/len(rows)
def forecast(prior,teammates,opp,league,weights):
    base=predict(prior)
    if base is None:return None
    recent=[r for r in prior[-12:] if r.get("minutes") is not None and r["minutes"]>=8]
    if len(recent)<8:return None
    last5=recent[-5:]
    minutes=max(8,min(42,.65*avg(last5,"minutes")+.35*avg(recent,"minutes")))
    # Detect role movement without assuming a confirmed lineup.
    minutes=max(8,min(42,minutes+weights["role"]*(avg(last5,"minutes")-avg(recent,"minutes"))))
    previous=prior[-1]
    team=previous.get("team")
    # Prior-date teammate presence only. Unknown future injuries are never inferred.
    present=teammates.get((previous["date"],previous["game_id"],team),set())
    known=teammates.get(("history",team),set())
    absent=len(known-present) if present else 0
    absent=min(absent,4)
    output={}
    for m in MARKETS[:4]:
        rate=sum(r[m] for r in recent)/sum(r["minutes"] for r in recent)
        role_projection=rate*minutes
        # Limited and shrunk usage scenario, derived from prior teammate availability.
        usage_factor=1+weights["absence"]*absent
        ratio=max(.75,min(1.25,opp[m]/max(1,league[m]))) if opp and league else 1
        output[m]=max(0,(1-weights["minutes"])*base[m]+weights["minutes"]*role_projection*usage_factor)
        output[m]*=1+weights["matchup"]*(ratio-1)
    output["PRA"]=sum(output[m] for m in ("PTS","REB","AST"))
    return output

def run():
    histories=load(PATHS);teams=load_team_games(PATHS)
    game_teams=defaultdict(set);teammates=defaultdict(set)
    for player,rows in histories.items():
        for r in rows:
            if r.get("game_id") and r.get("team"):
                game_teams[(r["date"],r["game_id"])].add(r["team"])
                teammates[(r["date"],r["game_id"],r["team"])].add(player)
    # Determine expected teammate roster from prior dates only in each sample.
    team_history=defaultdict(list)
    for player,rows in histories.items():
        for r in rows:
            if r.get("team"):team_history[r["team"]].append((r["date"],player))
    league_rows=sorted((r["date"],r["allowed"]) for rows in teams.values() for r in rows)
    examples=[]
    for player,rows in histories.items():
        for i,target in enumerate(rows):
            day=target["date"];prior=[r for r in rows[:i] if r["date"]<day]
            if len(prior)<8 or predict(prior) is None:continue
            # The integrated forecast requires eight *observed* minute games.
            # Skip ineligible rows instead of scoring a missing prediction.
            if sum(1 for row in prior[-12:] if row.get("minutes") is not None and row["minutes"]>=8)<8:continue
            pair=game_teams.get((day,target.get("game_id")))
            if not pair or len(pair)!=2 or target.get("team") not in pair:continue
            opponent=next(iter(pair-{target["team"]}))
            opp=prior_allowances(teams,opponent,day)
            history=[v for d,v in league_rows if d<day][-1000:]
            if not opp or len(history)<100:continue
            league={m:sum(v[j] for v in history)/len(history) for j,m in enumerate(MARKETS[:4])}
            previous=prior[-1];team=previous.get("team")
            # Prior 10 team dates define a historical rotation proxy.
            recent_dates=sorted({d for d,p in team_history[team] if d<day})[-10:]
            expected={p for d,p in team_history[team] if d in recent_dates}
            known=teammates.get((previous["date"],previous["game_id"],team),set())
            sample_teammates={("history",team):expected,
                              (previous["date"],previous["game_id"],team):known}
            examples.append((day,prior,target,sample_teammates,opp,league))
    dates=sorted({e[0] for e in examples})
    if len(dates)<20:raise SystemExit("Insufficient chronological samples")
    split=dates[int(.7*len(dates))]
    train=[e for e in examples if e[0]<split];hold=[e for e in examples if e[0]>=split]
    candidates=[
      {"minutes":0,"role":0,"absence":0,"matchup":0},
      {"minutes":.25,"role":0,"absence":0,"matchup":0},
      {"minutes":.5,"role":.25,"absence":0,"matchup":0},
      {"minutes":.25,"role":.25,"absence":.01,"matchup":.15},
      {"minutes":.5,"role":.25,"absence":.01,"matchup":.3},
      {"minutes":0,"role":0,"absence":0,"matchup":.3}]
    def error(rows,m,w):
        total=0.0;count=0
        for day,prior,target,t,opp,league in rows:
            p=forecast(prior,t,opp,league,w)
            if p is None:continue
            total+=abs(p[m]-target[m]);count+=1
        if count==0:raise ValueError("No eligible integrated-role predictions for "+m)
        return total/count
    selected={m:min(range(len(candidates)),key=lambda i:error(train,m,candidates[i])) for m in MARKETS}
    report={"status":"RESEARCH_ONLY_NOT_PROMOTED","holdout_start":split,
            "training_player_games":len(train),"holdout_player_games":len(hold),
            "selected_candidate_by_market":selected,
            "holdout":{m:{"baseline_mae":round(error(hold,m,candidates[0]),4),
                          "integrated_mae":round(error(hold,m,candidates[selected[m]]),4)}
                       for m in MARKETS},
            "limitations":["Historical teammate absence proxy is not verified injury status",
                           "No lineup announcements or possession-level pace in input data",
                           "Compared with research challenger, not production on matched holdout",
                           "No odds or ROI inference; production unchanged"]}
    out=ROOT/"reports/nba_integrated_role_backtest.json";out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report,indent=2)+"\n");print(json.dumps(report,indent=2))
if __name__=="__main__":run()
