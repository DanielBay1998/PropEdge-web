"""Historical team matchup features, strictly computed before prediction date.

Boxscore-derived opponent allowances are descriptive proxies, not possession-
adjusted defense. Never use the target game's statistics as features.
"""
import csv
from collections import defaultdict
from pathlib import Path

def load_team_games(paths):
    games=defaultdict(lambda:defaultdict(list))
    for path in paths:
        if not Path(path).exists():continue
        with open(path,encoding="utf-8-sig",newline="") as f:
            for row in csv.DictReader(f):
                day=str(row.get("Date") or "")
                if len(day)!=8 or not day.isdigit():continue
                if "2026_27" in path and day<"20261020":continue
                if "historical" in path and str(row.get("Season Type") or "").casefold()!="regular":continue
                team=str(row.get("Team") or "").strip().casefold()
                gid=str(row.get("Game ID") or "")
                try:stats=tuple(float(row[k]) for k in ("PTS","REB","AST","3PM"))
                except (ValueError,KeyError,TypeError):continue
                if not team or not gid or min(stats)<0:continue
                games[(day,gid)][team].append(stats)
    out=defaultdict(list)
    for (day,gid),teams in games.items():
        if len(teams)!=2:continue
        a,b=list(teams)
        if min(len(teams[a]),len(teams[b]))<5:continue
        for defense,offense in ((a,b),(b,a)):
            totals=tuple(sum(player[i] for player in teams[offense]) for i in range(4))
            out[defense].append({"date":day,"allowed":totals,"opponent":offense})
    return {team:sorted(rows,key=lambda r:r["date"]) for team,rows in out.items()}

def prior_allowances(games,opponent,asof,window=15):
    rows=[r for r in games.get(str(opponent).strip().casefold(),[]) if r["date"]<asof]
    if len(rows)<8:return None
    recent=rows[-window:]
    return {k:sum(r["allowed"][i] for r in recent)/len(recent)
            for i,k in enumerate(("PTS","REB","AST","3PM"))}
