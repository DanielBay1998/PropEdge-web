"""Prospective NBA projection accuracy trends. Research only; no changes to picks."""
import json, math, statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "data/model_tracking.json"
OUTPUT = ROOT / "reports/nba_accuracy_trends.json"
MODELS = {
    "primary": "pregame_primary_projection",
    "challenger": "pregame_challenger_projection",
    "v4": "pregame_v4_projection",
    "consensus": "pregame_consensus_projection",
}
MARKETS = ("PTS", "REB", "AST", "3PM", "PRA")
EXCLUDED = {"invalid_matchup_review", "unmatched_completed_schedule"}

def number(value):
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None

def summary(rows):
    if not rows:
        return {"n": 0, "mae": None, "bias": None, "median_absolute_error": None}
    errors = [abs(r["projection"] - r["actual"]) for r in rows]
    return {
        "n": len(rows),
        "independent_capture_dates": len({r["date"] for r in rows}),
        "mae": round(statistics.mean(errors), 4),
        "bias": round(statistics.mean(r["projection"] - r["actual"] for r in rows), 4),
        "median_absolute_error": round(statistics.median(errors), 4),
        "within_1_pct": round(100 * sum(e <= 1 for e in errors) / len(errors), 2),
        "within_2_pct": round(100 * sum(e <= 2 for e in errors) / len(errors), 2),
        "within_5_pct": round(100 * sum(e <= 5 for e in errors) / len(errors), 2),
    }

def compare(rows):
    # Identical paired observations for every model, avoiding sample-selection bias.
    return {name: summary([{"projection": r["forecasts"][name],
                            "actual": r["actual"], "date": r["date"]} for r in rows])
            for name in MODELS}

def run():
    records = json.loads(SOURCE.read_text())
    eligible, excluded = [], defaultdict(int)
    seen = set()
    for r in records:
        if r.get("status") not in ("win", "loss", "push"):
            excluded["ungraded"] += 1
            continue
        if r.get("validation_status") in EXCLUDED:
            excluded["identity_review"] += 1
            continue
        if r.get("consensus_capture_status") != "complete":
            excluded["incomplete_pregame_model_snapshot"] += 1
            continue
        market = r.get("market")
        if market not in MARKETS:
            excluded["unsupported_market"] += 1
            continue
        actual = number(r.get("actual"))
        forecasts = {name: number(r.get(field)) for name, field in MODELS.items()}
        try:
            capture = datetime.fromisoformat(str(r["captured_at"]).replace("Z", "+00:00"))
            if capture.tzinfo is None:
                raise ValueError("capture timestamp has no timezone")
            date = capture.date().isoformat()
        except (KeyError, TypeError, ValueError):
            excluded["invalid_capture_date"] += 1
            continue
        if actual is None or any(v is None for v in forecasts.values()):
            excluded["missing_or_nonfinite_values"] += 1
            continue
        key = (r.get("event_id"), r.get("player"), market, date)
        if key in seen:
            excluded["duplicate_event_player_market_date"] += 1
            continue
        seen.add(key)
        eligible.append({"date": date, "market": market, "actual": actual, "forecasts": forecasts})
    eligible.sort(key=lambda r: r["date"])
    dates = sorted({r["date"] for r in eligible})
    today = datetime.now(timezone.utc).date()
    def window(days):
        cutoff = (today - timedelta(days=days-1)).isoformat()
        return [r for r in eligible if r["date"] >= cutoff]
    def halves(rows):
        distinct = sorted({r["date"] for r in rows})
        if len(distinct) < 2:
            return {"status": "INSUFFICIENT_INDEPENDENT_DATES"}
        midpoint = distinct[len(distinct)//2]
        early = [r for r in rows if r["date"] < midpoint]
        late = [r for r in rows if r["date"] >= midpoint]
        return {"status": "DESCRIPTIVE_NOT_CAUSAL",
                "earlier": compare(early), "later": compare(late),
                "change_in_mae_later_minus_earlier": {
                    name: round(compare(late)[name]["mae"] - compare(early)[name]["mae"], 4)
                    for name in MODELS}}
    # Fixed, provisional market-specific challengers; never tune on evaluation outcomes.
    # Preserve live production projections and original pregame snapshots.
    candidate_rules = {
        "REB": {"over_subtract": 0.5, "under_subtract": 0.0},
        "3PM": {"over_subtract": 0.75, "under_subtract": 0.0},
    }
    calibration_trials = {}
    for market, rule in candidate_rules.items():
        subset = [r for r in eligible if r["market"] == market]
        baseline = [{"projection": r["forecasts"]["primary"], "actual": r["actual"], "date": r["date"]} for r in subset]
        adjusted = [{"projection": max(0.0, r["forecasts"]["primary"] - rule["over_subtract"]),
                     "actual": r["actual"], "date": r["date"]} for r in subset]
        calibration_trials[market] = {
            "status": "SHADOW_ONLY_NOT_PRODUCTION",
            "candidate_over_subtract": rule["over_subtract"],
            "candidate_under_subtract": rule["under_subtract"],
            "baseline_primary": summary(baseline),
            "counterfactual_over_adjusted_forecast": summary(adjusted),
            "note": "Forecast MAE counterfactual only; side-specific bet performance cannot be inferred from this. No production changes.",
        }
    report = {
        "status": "PROSPECTIVE_CAPTURED_RESEARCH_ONLY",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "eligible_paired_snapshots": len(eligible),
        "independent_capture_dates": len(dates),
        "excluded": dict(excluded),
        "overall": compare(eligible),
        "market_calibration_trials": calibration_trials,
        "by_market": {m: compare([r for r in eligible if r["market"] == m]) for m in MARKETS},
        "last_7_days": compare(window(7)),
        "last_30_days": compare(window(30)),
        "by_capture_date": {d: compare([r for r in eligible if r["date"] == d]) for d in dates},
        "earlier_vs_later": halves(eligible),
        "by_market_earlier_vs_later": {
            m: halves([r for r in eligible if r["market"] == m]) for m in MARKETS},
        "notes": [
            "Only immutable, complete pregame four-model snapshots with final outcomes are compared.",
            "All models use identical paired observations; duplicated event/player/market/day captures are excluded.",
            "Lower MAE is better. MAE across different markets has incompatible stat-unit scales.",
            "An earlier/later change does not prove newly added metrics caused an improvement.",
            "Do not promote a model or change live picks without independent chronological holdout validation.",
            "Pending outcomes are not scored; captured bet hit rate is a separate metric."
        ]
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"eligible": len(eligible), "dates": len(dates),
                      "overall": report["overall"], "excluded": report["excluded"]}, indent=2))

if __name__ == "__main__":
    run()
