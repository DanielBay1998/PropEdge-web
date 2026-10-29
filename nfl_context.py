"""Verified, timestamped NFL enrichment: weather, injuries, line, usage and coverage.
No fabricated provider values. A missing metric remains missing, never zero.
Optional provider CSV: data/nfl_context.csv, keyed by event_id and player_id or team,
with observed_at UTC ISO8601 and any of the supported numeric columns.
"""
import csv,datetime,math
from pathlib import Path
ROOT=Path(__file__).resolve().parent
FILE=ROOT/"data"/"nfl_context.csv"
NUMERIC=("temperature_f","wind_mph","precip_probability","humidity_pct","spread","game_total",
 "snap_pct","route_pct","target_share","targets","air_yards","redzone_targets",
 "rush_share","carries","redzone_carries","pass_block_win_rate","run_block_win_rate",
 "pressure_rate_allowed","sacks_allowed","opponent_pressure_rate","opponent_blitz_rate",
 "opponent_man_coverage_pct","opponent_zone_coverage_pct","opponent_yards_per_attempt_allowed",
 "opponent_yards_per_carry_allowed","opponent_yards_per_target_allowed",
 "opponent_pressure_to_sack_rate","opponent_epa_per_pass_allowed","opponent_epa_per_rush_allowed")
CATEGORICAL=("injury_status","practice_status","stadium_roof","surface","position","source")
def timestamp(value):
 try:
  dt=datetime.datetime.fromisoformat(str(value).replace("Z","+00:00"))
  return dt if dt.tzinfo else None
 except (TypeError,ValueError):return None
def records():
 if not FILE.exists():return []
 with FILE.open(newline="",encoding="utf-8-sig") as f:rows=list(csv.DictReader(f))
 result=[]
 for row in rows:
  when=timestamp(row.get("observed_at"))
  if not when or not row.get("event_id") or not row.get("source"):continue
  item={"event_id":str(row["event_id"]),"player_id":str(row.get("player_id") or ""),
        "team":row.get("team") or "","observed_at":when,"source":row["source"]}
  for name in NUMERIC:
   try:
    v=float(row.get(name,""))
    if math.isfinite(v):item[name]=v
   except (TypeError,ValueError):pass
  for name in CATEGORICAL:
   if row.get(name):item[name]=str(row[name])[:120]
  result.append(item)
 return result
def for_prop(rows,event_id,player_id,team,kickoff):
 """No future-observed input, no stale statuses, no cross-player leakage."""
 cutoff=timestamp(kickoff)
 if not cutoff:return {"available":False,"reason":"Kickoff timestamp missing"}
 now=datetime.datetime.now(datetime.timezone.utc)
 cutoff=min(cutoff,now)
 candidates=[r for r in rows if r["event_id"]==str(event_id) and r["observed_at"]<=cutoff
             and ((r["player_id"] and r["player_id"]==str(player_id)) or
                  (not r["player_id"] and (not r["team"] or r["team"]==team)))]
 candidates.sort(key=lambda r:r["observed_at"])
 out={}
 for r in candidates:
  for key,value in r.items():
   if key in ("event_id","player_id","team","observed_at","source"):continue
   if key in ("injury_status","practice_status") and (cutoff-r["observed_at"]).total_seconds()>7*86400:continue
   out[key]={"value":value,"source":r["source"],"observed_at":r["observed_at"].isoformat()}
 return {"available":bool(out),"metrics":out,"metric_count":len(out),
         "note":"Source-stamped inputs, not automatically assumed predictive"}
def injury_block(context):
 status=str(context.get("metrics",{}).get("injury_status",{}).get("value","")).lower()
 return status in ("out","inactive","injured reserve","ir","suspended","doubtful")
def categories():
 return {"weather":["temperature_f","wind_mph","precip_probability","humidity_pct","stadium_roof"],
         "injuries":["injury_status","practice_status"],
         "offensive_line":["pass_block_win_rate","run_block_win_rate","pressure_rate_allowed","sacks_allowed"],
         "usage":["snap_pct","route_pct","target_share","targets","air_yards","redzone_targets","rush_share","carries","redzone_carries"],
         "coverage":["opponent_man_coverage_pct","opponent_zone_coverage_pct","opponent_blitz_rate","opponent_pressure_rate",
                     "opponent_yards_per_attempt_allowed","opponent_yards_per_carry_allowed",
                     "opponent_yards_per_target_allowed","opponent_epa_per_pass_allowed","opponent_epa_per_rush_allowed"],
         "game_environment":["spread","game_total","surface"]}
