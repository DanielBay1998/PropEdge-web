"""Walk-forward minutes/usage research audit; does not modify production picks.

Tests minutes decomposition against a trailing baseline on prior-only games.
A missing teammate box-score row is an absence proxy, not a verified injury.
"""
import json,math
from collections import defaultdict
from backtest_injuries import read_logs

def audit(data):
    teams=defaultdict(list)
    for (day,gid,team),roster in data.items():
        teams[team].append((day,gid,{p:stat for p,stat in roster}))
    # The baseline backtest loader has no minutes column. Refuse to fabricate
    # per-minute rates; explicitly report missing feature coverage.
    return {
        "status":"not_evaluable",
        "reason":"Historical backtest reader currently provides PTS/REB/AST/3PM but not verified per-player minutes or historical injury designations.",
        "games":sum(len(games) for games in teams.values()),
        "minutes_model_validated":False,
        "usage_model_validated":False,
        "verified_injury_history_available":False,
        "promotion_to_official_picks":False,
        "next_requirements":["Parse and audit historical player minutes in the backtest loader",
                             "Validate complete team-game participation and DNP semantics",
                             "Obtain dated verified injury designations",
                             "Compare minutes and rate adjustments against an out-of-time holdout"]
    }

if __name__=="__main__":
    print(json.dumps(audit(read_logs()),indent=2))
