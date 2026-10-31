"""PropEdge NBA distribution/EV engine (research-only until calibrated).

Separate projection, uncertainty and sportsbook price. No automatic promotion.
"""
import math
import random
from statistics import mean, pstdev

MARKETS = ("PTS", "REB", "AST", "3PM", "PRA")

def american_profit(odds):
    odds = float(odds)
    if not math.isfinite(odds) or odds == 0:
        raise ValueError("Invalid American odds")
    return odds / 100 if odds > 0 else 100 / -odds

def expected_value(probability, odds):
    if not 0 <= probability <= 1:
        raise ValueError("Probability must be between 0 and 1")
    b = american_profit(odds)
    return probability * b - (1 - probability)

def predict_minutes(prior):
    valid = [float(r["minutes"]) for r in prior[-30:] if r.get("minutes") is not None and 1 <= float(r["minutes"]) <= 60]
    if len(valid) < 8:
        return None
    return max(8, min(42, .65 * mean(valid[-5:]) + .35 * mean(valid)))

def project(prior, pace_factor=1.0, matchup_factors=None):
    """Only historical observations; external context factors must be verified upstream."""
    minutes = predict_minutes(prior)
    if minutes is None or not .85 <= pace_factor <= 1.15:
        return None
    matchup_factors = matchup_factors or {}
    valid = [r for r in prior[-30:] if r.get("minutes") is not None and r["minutes"] >= 8]
    if len(valid) < 8:
        return None
    out = {}
    for market in MARKETS[:4]:
        short = valid[-8:]
        rate_short = sum(r[market] for r in short) / sum(r["minutes"] for r in short)
        rate_long = sum(r[market] for r in valid) / sum(r["minutes"] for r in valid)
        factor = float(matchup_factors.get(market, 1.0))
        if not .8 <= factor <= 1.2:
            raise ValueError("Unverified/extreme matchup factor")
        out[market] = max(0.0, minutes * (.35 * rate_short + .65 * rate_long) * pace_factor * factor)
    out["PRA"] = sum(out[m] for m in ("PTS", "REB", "AST"))
    return {"minutes": minutes, "projections": out}

def simulate(prior, projection, market, line, odds_over, odds_under, n=10000, seed=42):
    """Historical-residual bootstrap; paired PTS/REB/AST residuals preserve PRA dependence.

    Research-only: probabilities are NOT calibrated and must not be shown as
    validated betting confidence without independent chronological calibration.
    """
    if market not in MARKETS or n < 1000:
        raise ValueError("Unsupported market or insufficient draws")
    recent = [r for r in prior[-30:] if all(m in r for m in MARKETS)]
    if len(recent) < 12:
        return {"status": "INSUFFICIENT_HISTORY"}
    rng = random.Random(seed)
    base = {m: mean([r[m] for r in recent]) for m in MARKETS[:4]}
    target = projection["projections"]
    wins_over = wins_under = pushes = 0
    for _ in range(n):
        sample = rng.choice(recent)
        outcomes = {m: max(0, round(target[m] + sample[m] - base[m])) for m in MARKETS[:4]}
        outcomes["PRA"] = outcomes["PTS"] + outcomes["REB"] + outcomes["AST"]
        value = outcomes[market]
        wins_over += value > line
        wins_under += value < line
        pushes += value == line
    # Push refunds stake; unconditional probabilities are required for EV.
    p_over, p_under = wins_over / n, wins_under / n
    return {"status": "UNCALIBRATED_RESEARCH_ONLY", "market": market, "line": line,
            "projected": target[market], "draws": n, "over_probability": p_over,
            "under_probability": p_under, "push_probability": pushes / n,
            "over_ev": p_over * american_profit(odds_over) - p_under,
            "under_ev": p_under * american_profit(odds_under) - p_over,
            "warning": "Bootstrap does not establish calibrated probability or profitable EV"}

if __name__ == "__main__":
    print("NBA distribution engine ready; research-only, requires real historical inputs.")
