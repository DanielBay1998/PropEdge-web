"""Chronological defense ablation: strict pre-game features and held-out evaluation."""
import json
from pathlib import Path
from collections import defaultdict
from nba_challenger_model import load,predict,MARKETS
from nba_matchup_features import load_team_games,prior_allowances

ROOT=Path(__file__).resolve().parent
def run():
    paths=[*[str(ROOT/"data"/f"propedge_historical_{season}_minutes.csv") for season in ("2023_24","2024_25","2025_26")],str(ROOT/"data/nba_2026_27.csv")]
    histories=load(paths)
    opponents=load_team_games(paths)
    # Date-specific league baseline from prior games only; prevents scale drift.
    by_day=defaultdict(list)
    for rows in opponents.values():
        for row in rows:by_day[row["date"]].append(row["allowed"])
    dates=sorted(by_day)
    league_history=[]
    league_by_day={}
    for day in dates:
        if league_history:
            last=league_history[-1000:]
            league_by_day[day]=[sum(v[i] for v in last)/len(last) for i in range(4)]
        league_history.extend(by_day[day])
    # Matchup identity is taken from the same game's two recorded teams.
    game_teams=defaultdict(set)
    for rows in histories.values():
        for r in rows:
            if r.get("game_id") and r.get("team"):
                game_teams[(r["date"],r["game_id"])].add(r["team"])
    samples=[]
    for player,history in histories.items():
        for i,actual in enumerate(history):
            day=actual["date"]
            prior=[r for r in history[:i] if r["date"]<day]
            baseline=predict(prior)
            if baseline is None:continue
            teams=game_teams.get((day,actual.get("game_id")))
            if not teams or len(teams)!=2 or actual.get("team") not in teams:continue
            opponent=next(iter(teams-{actual["team"]}))
            allowed=prior_allowances(opponents,opponent,day)
            league=league_by_day.get(day)
            if not allowed or not league:continue
            samples.append({"date":day,"player":player,"actual":actual,"base":baseline,
                            "allowed":allowed,"league":league})
    days=sorted(set(s["date"] for s in samples))
    if len(days)<20:raise SystemExit("Insufficient chronological evaluation dates")
    split=days[int(len(days)*0.7)]
    train=[s for s in samples if s["date"]<split]
    hold=[s for s in samples if s["date"]>=split]
    # Fixed candidate strengths, selected using training period only.
    strengths=[0.0,0.15,0.30,0.45]
    def estimate(s,market,strength):
        if market=="PRA":
            return sum(estimate(s,k,strength) for k in ("PTS","REB","AST"))
        i=("PTS","REB","AST","3PM").index(market)
        ratio=max(0.75,min(1.25,s["allowed"][market]/max(1,s["league"][i])))
        return s["base"][market]*(1+strength*(ratio-1))
    def evaluate(rows,market,strength):
        if not rows:return {"n":0,"mae":None}
        err=[abs(estimate(s,market,strength)-s["actual"][market]) for s in rows]
        return {"n":len(err),"mae":round(sum(err)/len(err),4)}
    chosen={m:min(strengths,key=lambda w:evaluate(train,m,w)["mae"]) for m in MARKETS}
    report={"status":"COMPLETED_RESEARCH_ONLY","holdout_start":split,
            "training_player_games":len(train),"holdout_player_games":len(hold),
            "selected_defense_strength_by_market":chosen,
            "holdout":{m:{"original_challenger":evaluate(hold,m,0.0),
                          "defense_adjusted":evaluate(hold,m,chosen[m])} for m in MARKETS},
            "limitations":["Compared to reconstructed challenger, not captured production model",
                           "Team boxscore allowances are not possession-adjusted defensive ratings",
                           "No verified injury or sportsbook odds data; no ROI claim",
                           "Historical team names and game IDs must join exactly"]}
    report["defense_wins_markets"]=[m for m,v in report["holdout"].items()
        if v["defense_adjusted"]["mae"]<v["original_challenger"]["mae"]]
    out=ROOT/"reports/nba_defense_backtest.json";out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))
    if not hold:raise SystemExit("No holdout samples")
if __name__=="__main__":run()
