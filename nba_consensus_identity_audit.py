"""Audit captured NBA prop identities without altering immutable snapshots."""
import csv,json,collections,datetime
from pathlib import Path
from track_model import canonical,team_key
ROOT=Path(__file__).resolve().parent
def audit():
 rows=json.loads((ROOT/"data/model_tracking.json").read_text())
 season=ROOT/"data/nba_2026_27.csv"
 players=collections.defaultdict(list)
 if season.exists():
  with season.open(encoding="utf-8-sig",newline="") as f:
   for r in csv.DictReader(f):
    if r.get("Player") and r.get("Team") and r.get("Date"):
     players[canonical(r["Player"])].append((str(r["Date"]).replace("-",""),team_key(r["Team"]),r.get("Game ID")))
 for v in players.values():v.sort()
 events=collections.defaultdict(set)
 for r in rows:
  if r.get("event_id") and r.get("game"):events[str(r["event_id"])].add(str(r["game"]))
 results=[];counts=collections.Counter()
 for r in rows:
  if r.get("consensus_capture_status")!="complete":continue
  issues=[]
  teams=str(r.get("game") or "").split(" @ ")
  event=str(r.get("event_id") or "")
  if len(teams)!=2 or not all(teams):issues.append("invalid_matchup")
  if len(events.get(event,set()))>1:issues.append("event_multiple_matchups")
  try:day=datetime.datetime.fromisoformat(str(r["captured_at"]).replace("Z","+00:00")).strftime("%Y%m%d")
  except ValueError:day=""
  prior=[x for x in players.get(canonical(r.get("player")),[]) if x[0]<day]
  if prior and len(teams)==2:
   last=prior[-1]
   if last[1] not in {team_key(t) for t in teams}:issues.append("latest_logged_team_mismatch")
  elif not prior:issues.append("no_prior_current_season_team_evidence")
  status="quarantined" if any(x in issues for x in ("invalid_matchup","event_multiple_matchups","latest_logged_team_mismatch")) else ("needs_review" if issues else "verified_from_available_logs")
  counts[status]+=1
  results.append({"id":r.get("id"),"player":r.get("player"),"game":r.get("game"),"market":r.get("market"),"status":status,"issues":issues,"latest_prior_team":prior[-1][1] if prior else None})
 out={"audit":"capture_identity_research_v1","total":len(results),"counts":dict(counts),
      "limitations":["Verified means only consistency with available prior-season logs, not independently verified scheduled event or pregame eligibility.","No prior current-season log is not proof of an incorrect assignment.","Original predictions and grades remain unchanged."],"records":results}
 path=ROOT/"reports/nba_consensus_identity_audit.json";path.parent.mkdir(exist_ok=True);path.write_text(json.dumps(out,indent=2)+"\n")
 print(json.dumps({"total":len(results),"counts":dict(counts)},indent=2))
if __name__=="__main__":audit()
