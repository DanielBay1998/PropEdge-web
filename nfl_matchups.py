"""Independent NFL matchup research from verified ESPN final game records."""
import json
from pathlib import Path
from collections import defaultdict
from nfl_model import history,canon
FILE=Path(__file__).resolve().parent/"data"/"nfl_opponents.json"
def matchup(opponent,market):
 try:events=json.loads(FILE.read_text())
 except (OSError,ValueError):return {"available":False,"reason":"Game opponent mapping unavailable"}
 grouped=defaultdict(list)
 for r in history():
  if r["date"].year==2026 and r["date"].month>=7:grouped[str(r["event_id"])].append(r)
 values=[]
 for eid,teams in events.items():
  if canon(opponent) not in [canon(x) for x in teams]:continue
  stats=[r["stats"][market] for r in grouped.get(eid,[]) if canon(r["team"])!=canon(opponent) and market in r["stats"]]
  if stats:values.append(sum(stats))
 if len(values)<4:return {"available":False,"games":len(values),"reason":"At least four completed games required"}
 return {"available":True,"games":len(values),"opponent_total_allowed_avg":round(sum(values)/len(values),2),"note":"Raw opposing team totals, not position-adjusted"}
