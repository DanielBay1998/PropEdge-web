"""Walk-forward defense-feature ablation, using only prior completed games."""
import json
from pathlib import Path
from collections import defaultdict
from nba_challenger_model import load,predict,MARKETS
from nba_matchup_features import load_team_games,prior_allowances

ROOT=Path(__file__).resolve().parent
def run():
    paths=[str(ROOT/"data/propedge_historical_2025_26_minutes.csv"),
           str(ROOT/"data/nba_2026_27.csv")]
    histories=load(paths)
    opponents=load_team_games(paths)
    # Build team and opposing-team labels from game ID only when both teams
    # have boxscores. Never use that game's stats as predictive features.
    games=defaultdict(set)
    for path in paths:
        import csv
        if not Path(path).exists():continue
        with open(path,encoding="utf-8-sig",newline="") as f:
            for row in csv.DictReader(f):
                day=str(row.get("Date") or "")
                if len(day)!=8 or not day.isdigit():continue
                if "2026_27" in path and day<"20261020":continue
                if "historical" in path and str(row.get("Season Type") or "").casefold()!="regular":continue
                games[(day,str(row.get("Game ID") or ""))].add(str(row.get("Team") or "").strip().casefold())
    league=defaultdict(list)
    for team,rows in opponents.items():
        for row in rows:
            league[row["date"]].append(row["allowed"])
    results={k:{"n":0,"baseline_abs":0.0,"defense_abs":0.0} for k in MARKETS}
    for player,history in histories.items():
        for idx,actual in enumerate(history):
            day=actual["date"]
            prior=[r for r in history[:idx] if r["date"]<day]
            baseline=predict(prior)
            if baseline is None:continue
            # Require a unique known team assignment for the player on the date.
            # Player history is keyed by date, so recover matchup only if unique.
            candidate_games=[]
            for (game_day,gid),teams in games.items():
                if game_day!=day or len(teams)!=2:continue
                candidate_games.append((gid,teams))
            if len(candidate_games)!=1:continue
            gid,teams=candidate_games[0]
            # Player team isn't present in the basic challenger history;
            # therefore skip rather than fabricate a matchup assignment.
            continue
    report={"status":"INSUFFICIENT_PLAYER_GAME_TEAM_JOIN",
            "results":results,
            "reason":"Challenger history currently omits team and game ID. A leakage-safe defensive backtest requires those identifiers."}
    out=ROOT/"reports/nba_defense_backtest.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))
    raise SystemExit("Cannot validate opponent effects without verified player-game-team joins")
if __name__=="__main__":run()
