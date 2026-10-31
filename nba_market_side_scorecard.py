"""Research-only scorecard: captured pick performance by market and side.

No model training, no production changes. Uses immutable recorded picks.
"""
import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

MARKETS = ("PTS", "REB", "AST", "3PM", "PRA")
EXCLUDED = {"invalid_matchup_review", "unmatched_completed_schedule"}

def summarize(records):
    settled = [r for r in records if r["status"] in ("win", "loss")]
    wins = sum(r["status"] == "win" for r in settled)
    profit = sum((r["odds"] / 100 if r["odds"] > 0 else 100 / -r["odds"])
                 if r["status"] == "win" else -1 for r in settled)
    losses = [r for r in settled if r["status"] == "loss"]
    return {
        "n": len(settled), "wins": wins, "losses": len(losses),
        "pushes": sum(r["status"] == "push" for r in records),
        "hit_rate_pct": round(100 * wins / len(settled), 2) if settled else None,
        "flat_stake_roi_pct": round(100 * profit / len(settled), 2) if settled else None,
        "loss_within_1_stat": sum(0 < -r["margin"] <= 1 for r in losses),
        "loss_within_2_stats": sum(0 < -r["margin"] <= 2 for r in losses),
        "mean_loss_margin": round(sum(-r["margin"] for r in losses) / len(losses), 3) if losses else None,
        "projection_mae": round(sum(abs(r["projection"] - r["actual"]) for r in records) / len(records), 3) if records else None,
        "projection_bias": round(sum(r["projection"] - r["actual"] for r in records) / len(records), 3) if records else None,
    }

def audit(raw):
    seen = set()
    eligible = []
    excluded = defaultdict(int)
    for r in raw:
        if r.get("status") not in ("win", "loss", "push"):
            excluded["ungraded"] += 1
            continue
        if r.get("validation_status") in EXCLUDED:
            excluded["identity_review"] += 1
            continue
        market = str(r.get("market", "")).upper()
        side = str(r.get("side", "")).upper()
        if market not in MARKETS or side not in ("OVER", "UNDER"):
            excluded["unsupported_market_or_side"] += 1
            continue
        try:
            line, actual, projection, odds = (float(r[k]) for k in ("line", "actual", "projection", "odds"))
            if not all(math.isfinite(v) for v in (line, actual, projection, odds)) or odds == 0:
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            excluded["invalid_numbers"] += 1
            continue
        stamp = str(r.get("captured_at") or "")
        key = (stamp, str(r.get("event_id")), str(r.get("player", "")).casefold(),
               market, side, line, odds)
        if key in seen:
            excluded["duplicate"] += 1
            continue
        seen.add(key)
        margin = (actual - line) * (1 if side == "OVER" else -1)
        if (margin > 0 and r["status"] != "win") or (margin < 0 and r["status"] != "loss") or (margin == 0 and r["status"] != "push"):
            excluded["grade_mismatch"] += 1
            continue
        eligible.append(dict(status=r["status"], market=market, side=side, margin=margin,
                             projection=projection, actual=actual, odds=odds, date=stamp[:10]))
    groups = defaultdict(list)
    for r in eligible:
        groups[(r["market"], r["side"])].append(r)
    dates = sorted({r["date"] for r in eligible if r["date"]})
    return {
        "status": "DESCRIPTIVE_ONLY_NOT_VALIDATED_FOR_SELECTION",
        "total": summarize(eligible),
        "by_market_side": {m: {s: summarize(groups[(m, s)]) for s in ("OVER", "UNDER")} for m in MARKETS},
        "by_capture_date": {d: summarize([r for r in eligible if r["date"] == d]) for d in dates},
        "excluded": dict(excluded),
        "limitations": [
            "Historical captured picks, not a prospective independent holdout",
            "Near misses measure distance from sportsbook line, not model error",
            "ROI assumes one unit at recorded American odds, excludes pushes from denominator",
            "Do not tune market/side filters on this same dataset and claim validated gains",
            "No live model, odds, or website changes",
        ],
    }

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", default="data/model_tracking.json")
    p.add_argument("--output", default="reports/nba_market_side_scorecard.json")
    args = p.parse_args()
    report = audit(json.loads(Path(args.input).read_text(encoding="utf-8")))
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
