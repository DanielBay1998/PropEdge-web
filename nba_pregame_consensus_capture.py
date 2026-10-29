"""Immutable pregame NBA consensus research snapshot; never alters production picks."""
import datetime,json,math,statistics
from pathlib import Path
from nba_challenger_model import load,predict
from nba_v4_baseline_audit import features,estimate
ROOT=Path(__file__).resolve().parent
PATHS=[str(ROOT/"data"/f"propedge_historical_{s}_minutes.csv") for s in ("2023_24","2024_25","2025_26")]+[str(ROOT/"data/nba_2026_27.csv")]
def history_for_capture(day):
    return load(PATHS)
def snapshot(signal,captured,histories,coefficients):
    market=signal.get("market")
    if market not in ("PTS","REB","AST","3PM","PRA"):return {}
    player=" ".join(str(signal.get("player") or "").casefold().split())
    day=captured.strftime("%Y%m%d")
    prior=[g for g in histories.get(player,[]) if g["date"]<day]
    result={"consensus_capture_version":"pregame_research_v1","consensus_capture_history_n":len(prior)}
    if len(prior)<12:
        result["consensus_capture_status"]="insufficient_history"
        return result
    recent=[g["PTS"] for g in prior[-12:]]
    result["pregame_points_volatility_12"]=round(statistics.pstdev(recent),5)
    # Pregame feature provenance: only facts observed before capture.
    # A feature is never marked available when the source lacks it.
    latest=prior[-1]
    last_day=datetime.datetime.strptime(latest["date"],"%Y%m%d").date()
    rest=(captured.date()-last_day).days-1
    matchup=str(signal.get("game") or "").split(" @ ")
    latest_team=str(latest.get("team") or "").casefold().strip()
    home=(latest_team==matchup[1].casefold().strip()) if len(matchup)==2 and latest_team else None
    away=(latest_team==matchup[0].casefold().strip()) if len(matchup)==2 and latest_team else None
    if home is False and away is False:home=None
    minute_values=[g["minutes"] for g in prior[-5:] if g.get("minutes") is not None]
    context={"minutes":round(statistics.mean(minute_values),3) if minute_values else None,
             "usage_rate":None,"pace":None,"opponent_defense":None,
             "rest_days":rest if rest>=0 else None,
             "back_to_back":rest==0 if rest>=0 else None,
             "home":home,"travel_miles":None,"team_total":None}
    result["pregame_advanced_metrics"]=context
    result["pregame_advanced_metrics_coverage"]={k:v is not None for k,v in context.items()}

    challenger=predict(prior)
    if challenger is None:
        result["consensus_capture_status"]="challenger_unavailable"
        return result
    result["pregame_challenger_projection"]=round(challenger[market],5)
    try:
        primary=float(signal["projection"])
        if not math.isfinite(primary) or primary<0:raise ValueError()
        result["pregame_primary_projection"]=primary
    except (ValueError,TypeError,KeyError):
        result["consensus_capture_status"]="primary_unavailable"
        return result
    # Training provenance must predate capture. Never use future-fitted V4.
    try:
        fitted=datetime.datetime.fromisoformat(coefficients["as_of_utc"].replace("Z","+00:00"))
        if fitted.tzinfo is None or fitted>=captured:
            result["consensus_capture_status"]="v4_not_pregame"
            return result
        params=coefficients["markets"][market]
        v4=estimate((params["weights"],params["means"],params["scales"]),features(prior,market))
        if not math.isfinite(v4):raise ValueError()
    except (KeyError,TypeError,ValueError,ZeroDivisionError,IndexError):
        result["consensus_capture_status"]="v4_unavailable"
        return result
    result.update({"pregame_v4_projection":round(v4,5),
                   "pregame_v4_coefficients_as_of_utc":coefficients["as_of_utc"],
                   "pregame_consensus_projection":round((primary+challenger[market]+v4)/3,5),
                   "consensus_capture_status":"complete"})
    return result
