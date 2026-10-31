"""Research-only NBA per-market projection comparison with chronological holdout.

Uses only prior games. Evaluates a recent-rate/minutes candidate against
existing challenger and trailing-ten baseline. Never alters production.
"""
import argparse
import json
from pathlib import Path
from nba_challenger_model import load, predict, MARKETS

def candidate(prior):
    if len(prior) < 12:
        return None
    result = {}
    valid = [r for r in prior[-30:] if r["minutes"] is not None and r["minutes"] >= 8]
    if len(valid) < 8:
        return None
    recent = valid[-6:]
    expected_minutes = min(42, max(8, 0.65 * sum(r["minutes"] for r in recent)/len(recent)
                                   + 0.35 * sum(r["minutes"] for r in valid)/len(valid)))
    for market in MARKETS[:4]:
        short_rate = sum(r[market] for r in recent)/sum(r["minutes"] for r in recent)
        long_rate = sum(r[market] for r in valid)/sum(r["minutes"] for r in valid)
        result[market] = expected_minutes * (0.35 * short_rate + 0.65 * long_rate)
    result["PRA"] = sum(result[k] for k in ("PTS", "REB", "AST"))
    return result

def run(paths):
    histories = load(paths)
    rows = []
    for player, games in histories.items():
        for target in games:
            prior = [r for r in games if r["date"] < target["date"]]
            base = predict(prior)
            alt = candidate(prior)
            if base is None or alt is None:
                continue
            for market in MARKETS:
                rows.append({"date": target["date"], "market": market,
                             "actual": target[market], "challenger": base[market],
                             "minutes_rate": alt[market],
                             "trailing_ten": sum(r[market] for r in prior[-10:])/10})
    dates = sorted({r["date"] for r in rows})
    if len(dates) < 30:
        return {"status": "INSUFFICIENT_HOLDOUT_DATES", "dates": len(dates)}
    cutoff = dates[int(0.8 * len(dates))]
    test = [r for r in rows if r["date"] >= cutoff]
    def stats(rs):
        if not rs:
            return {"n": 0}
        return {"n": len(rs), **{name: {
            "mae": round(sum(abs(r[name]-r["actual"]) for r in rs)/len(rs), 4),
            "bias": round(sum(r[name]-r["actual"] for r in rs)/len(rs), 4)
        } for name in ("challenger", "minutes_rate", "trailing_ten")}}
    return {"status": "RESEARCH_ONLY_NOT_PROMOTED", "holdout_start": cutoff,
            "holdout": {m: stats([r for r in test if r["market"] == m]) for m in MARKETS},
            "limitations": ["Chronological holdout, not captured-odds ROI",
                            "No real injury, attempt-volume, or matchup inputs",
                            "Candidate uses historical minutes, not confirmed future minutes",
                            "No production changes"]}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="reports/nba_minutes_rate_candidate_comparison.json")
    args = parser.parse_args()
    paths = [f"data/propedge_historical_{s}_minutes.csv" for s in ("2023_24", "2024_25", "2025_26")]
    paths.append("data/nba_2026_27.csv")
    report = run(paths)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
