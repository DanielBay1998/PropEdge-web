"""Incrementally import completed NFL regular-season ESPN player boxscores."""
import csv,datetime,time,json
from pathlib import Path
from nfl import espn,parse_boxscore
ROOT=Path(__file__).resolve().parent
FILE=ROOT/"data"/"nfl_gamelogs.csv"
FIELDS=["event_id","date","player_id","player","team","PASS_YDS","RUSH_YDS","REC_YDS","REC","PASS_TD","RUSH_TD"]
def run():
 rows={}
 if FILE.exists():
  with FILE.open(encoding="utf-8-sig",newline="") as f:
   for r in csv.DictReader(f):rows[(r.get("event_id"),r.get("player_id"))]=r
 existing_games={k[0] for k in rows}
 completed=[]
 now=datetime.datetime.now(datetime.timezone.utc)
 for season in (now.year-1,now.year):
  for week in range(1,19):
   try:events=espn("scoreboard?dates="+str(season)+"&seasontype=2&week="+str(week)+"&limit=40").get("events",[])
   except Exception as e:
    print("Scoreboard unavailable",season,week,type(e).__name__,flush=True);continue
   for e in events:
    eid=str(e.get("id") or "")
    if not eid or eid in existing_games:continue
    if not ((e.get("status") or {}).get("type") or {}).get("completed"):continue
    date=str(e.get("date") or "")[:10]
    if not date:continue
    completed.append((date,eid))
 added=0;games=0
 for date,eid in sorted(set(completed),reverse=True):
  try:players=parse_boxscore(eid)
  except Exception as e:
   print("Boxscore unavailable",eid,type(e).__name__,flush=True);continue
  valid=[p for p in players if p.get("id") and p.get("player") and p.get("stats")]
  if len(valid)<12:
   print("Incomplete boxscore",eid,len(valid),flush=True);continue
  for p in valid:
   row={"event_id":eid,"date":date,"player_id":p["id"],"player":p["player"],"team":p.get("team") or ""}
   row.update({m:p["stats"].get(m,"") for m in FIELDS[5:]})
   rows[(eid,p["id"])]=row;added+=1
  games+=1
 FILE.parent.mkdir(exist_ok=True)
 with FILE.open("w",encoding="utf-8",newline="") as f:
  w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader()
  w.writerows(sorted(rows.values(),key=lambda r:(r["date"],r["event_id"],r["player"])))
 print("NFL history:",games,"new games",added,"new player rows",len(rows),"total rows",flush=True)
if __name__=="__main__":run()
