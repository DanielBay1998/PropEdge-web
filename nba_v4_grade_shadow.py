"""Conservative grading of NBA shadow selections from completed local game logs."""
import csv
import datetime
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SNAPSHOTS = ROOT / "data/nba_v4_shadow_selections.json"
GAMES = ROOT / "data/nba_2026_27.csv"

def normalize(value):
    key = re.sub(r"[^a-z0-9]", "", str(value or "").lower())
    return {"losangelesclippers": "laclippers"}.get(key, key)

def main():
    if not SNAPSHOTS.exists() or not GAMES.exists():
        print("No snapshots or completed boxscores; nothing to grade")
        return
    records = json.loads(SNAPSHOTS.read_text())
    games = defaultdict(list)
    with GAMES.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row.get("Game ID"):
                games[row["Game ID"]].append(row)
    changed = 0
    for record in records:
        if record.get("status") != "pending":
            continue
        try:
            teams = str(record["game"]).split(" @ ")
            if len(teams) != 2:
                continue
            fixture = {normalize(t) for t in teams}
            captured = datetime.datetime.fromisoformat(record["captured_at"].replace("Z", "+00:00")).date()
            matches = []
            for game_id, players in games.items():
                if {normalize(p.get("Team")) for p in players} != fixture:
                    continue
                dates = {str(p.get("Date", "")).replace("-", "") for p in players}
                if len(dates) != 1:
                    continue
                game_day = datetime.datetime.strptime(next(iter(dates)), "%Y%m%d").date()
                if not -1 <= (game_day - captured).days <= 2:
                    continue
                # Prefer exact game identity when the odds feed uses ESPN IDs.
                recorded_id = str(record.get("event_id") or "")
                if recorded_id and recorded_id in games and game_id != recorded_id:
                    continue
                found = [p for p in players if normalize(p.get("Player")) == normalize(record["player"])]
                if len(found) == 1:
                    matches.append((game_id, found[0]))
            if len(matches) != 1:
                continue
            game_id, player = matches[0]
            market = record["market"]
            actual = sum(float(player[k]) for k in ("PTS", "REB", "AST")) if market == "PRA" else float(player[market])
            line = float(record["line"])
            if actual < 0:
                continue
            side = record["side"]
            if side not in ("OVER", "UNDER"):
                continue
            record["actual"] = actual
            record["status"] = "push" if actual == line else ("win" if (actual > line) == (side == "OVER") else "loss")
            record["completed_game_id"] = game_id
            record["graded_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            changed += 1
        except (KeyError, ValueError, TypeError):
            continue
    if changed:
        SNAPSHOTS.write_text(json.dumps(records, indent=2) + "\n")
    def summarize(items):
        graded = [r for r in items if r.get("status") in ("win", "loss", "push")]
        profit = 0.0
        for r in graded:
            odds = float(r["odds"])
            if r["status"] == "win":
                profit += odds / 100 if odds > 0 else 100 / abs(odds)
            elif r["status"] == "loss":
                profit -= 1
        return {"captured": len(items), "graded": len(graded),
                "wins": sum(r["status"] == "win" for r in graded),
                "losses": sum(r["status"] == "loss" for r in graded),
                "roi_pct": round(100 * profit / len(graded), 2) if graded else None}
    settled_dates = sorted({r["captured_at"][:10] for r in records if r.get("status") in ("win", "loss", "push")})
    report = {"status": "PROSPECTIVE_LIVE_PRIMARY_RESEARCH",
              "newly_graded": changed, "overall": summarize(records),
              "assists_pra": summarize([r for r in records if r.get("market") in ("AST", "PRA")]),
              "other_markets": summarize([r for r in records if r.get("market") not in ("AST", "PRA")]),
              "capture_dates": sorted({r["captured_at"][:10] for r in records}),
              "settled_capture_dates": settled_dates,
              "promotion_gate": "RESEARCH_ONLY_INSUFFICIENT_DATES" if len(settled_dates) < 10 else "REQUIRES_OUT_OF_SAMPLE_REVIEW",
              "by_market": {m: summarize([r for r in records if r.get("market") == m]) for m in ("PTS", "REB", "AST", "3PM", "PRA")},
              "by_capture_date": {d: summarize([r for r in records if r["captured_at"][:10] == d]) for d in sorted({r["captured_at"][:10] for r in records})},
              "note": "These are live primary forecasts, not independent V4 forecasts."}
    destination = ROOT / "reports/nba_v4_prospective_roi.json"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
