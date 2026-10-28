"""Leakage-controlled walk-forward audit for provisional NBA per-game projections.

Uses current regular-season game logs only. Predict each game using strictly earlier
dates. The cross-player prior is rebuilt at each cutoff from earlier games.
No sportsbook prices, probabilities, or betting ROI are inferred.
"""
from collections import defaultdict
from datetime import datetime, timezone
import csv
from pathlib import Path

CODES=("PTS","REB","AST","3PM","PRA")

def evaluate(csv_path, player=None, min_games=3, prior_games=5):
    from math import sqrt
    by_player=defaultdict(list)
    with Path(csv_path).open(encoding="utf-8-sig",newline="") as handle:
        for r in csv.DictReader(handle):
            date=str(r.get("Date") or "")
            if len(date)!=8 or not date.isdigit() or date<"20261020":continue
            try: stats={k:float(r[k]) for k in CODES[:-1]}
            except (ValueError,TypeError,KeyError):continue
            if any(v<0 for v in stats.values()):continue
            stats["PRA"]=stats["PTS"]+stats["REB"]+stats["AST"]
            name=" ".join(str(r.get("Player") or "").casefold().split())
            if name:by_player[name].append((date,str(r.get("Game ID") or ""),stats))
    for name in by_player:
        unique={}
        for date,gid,stats in by_player[name]:unique[(date,gid)]=stats
        by_player[name]=sorted((d,g,s) for (d,g),s in unique.items())
    dates=sorted({d for rows in by_player.values() for d,_,_ in rows})
    errors=defaultdict(lambda:{"model":[],"baseline":[],"dates":[],"players":set()})
    player_key=" ".join(player.casefold().split()) if player else None
    for day in dates:
        # No data from the target date (including other games) enters training.
        history={name:[s for d,_,s in rows if d<day] for name,rows in by_player.items()}
        eligible={name:rows for name,rows in history.items() if len(rows)>=min_games}
        if not eligible:continue
        priors={code:sum(sum(s[code] for s in rows)/len(rows) for rows in eligible.values())/len(eligible)
                for code in CODES[:-1]}
        for name,rows in by_player.items():
            if player_key and name!=player_key:continue
            if name not in eligible:continue
            recent=eligible[name][-10:]
            targets=[s for d,_,s in rows if d==day]
            for target in targets:
                for code in CODES:
                    baseline=sum(s[code] for s in recent)/len(recent)
                    if code=="PRA":
                        model=baseline
                    else:
                        model=(len(recent)*baseline+prior_games*priors[code])/(len(recent)+prior_games) if code=="3PM" else baseline
                    e=errors[code]
                    e["model"].append(model-target[code])
                    e["baseline"].append(baseline-target[code])
                    e["dates"].append(day)
                    e["players"].add(name)
    output={}
    for code in CODES:
        e=errors[code];n=len(e["model"])
        output[code]={"predictions":n,"players":len(e["players"]),
                      "mae":round(sum(abs(v) for v in e["model"])/n,3) if n else None,
                      "rmse":round(sqrt(sum(v*v for v in e["model"])/n),3) if n else None,
                      "mean_bias":round(sum(e["model"])/n,3) if n else None,
                      "recent_average_mae":round(sum(abs(v) for v in e["baseline"])/n,3) if n else None,
                      "first_test_date":min(e["dates"]) if n else None,
                      "last_test_date":max(e["dates"]) if n else None}
    count=output["PTS"]["predictions"]
    return {"status":"INSUFFICIENT_EVIDENCE" if count<100 else "RESEARCH_ONLY_NOT_VALIDATED",
            "as_of_utc":datetime.now(timezone.utc).isoformat(),
            "player":player,"minimum_prior_games":min_games,"prior_strength_games":prior_games,
            "data_scope":"2026-27 regular season only, games from Oct 20, 2026",
            "method":"strictly earlier-date recent-form PTS/REB/AST/PRA and empirical-Bayes 3PM; fixed hyperparameters",
            "metrics":output,"limitations":["Current-season sample is short; historical season source not included",
            "Model configuration was selected after inspecting this same sample; improvements are in-sample and require future holdout validation",
            "No injuries, minutes, opponents, sportsbook odds, calibration, ROI or confidence intervals",
            "This audit does not authorize promotion into Top Picks"]}
