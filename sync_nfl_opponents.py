"""Build an auditable map of final NFL matchups from ESPN scoreboards."""
import json,datetime
from pathlib import Path
from nfl import espn
FILE=Path(__file__).resolve().parent/"data"/"nfl_opponents.json"
def run():
 try:out=json.loads(FILE.read_text())
 except (OSError,ValueError):out={}
 now=datetime.datetime.now(datetime.timezone.utc).year
 for year in (now-1,now):
  for week in range(1,19):
   try:events=espn("scoreboard?dates="+str(year)+"&seasontype=2&week="+str(week)+"&limit=40").get("events",[])
   except Exception:continue
   for e in events:
    if not ((e.get("status") or {}).get("type") or {}).get("completed"):continue
    teams=[(c.get("team") or {}).get("displayName") for c in (e.get("competitions") or [{}])[0].get("competitors",[])]
    if len(teams)==2 and all(teams):out[str(e["id"])]=teams
 FILE.parent.mkdir(exist_ok=True)
 FILE.write_text(json.dumps(out,indent=2,sort_keys=True)+"\n")
 print("NFL opponent mappings",len(out))
if __name__=="__main__":run()
