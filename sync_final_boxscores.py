"""Append verified completed ESPN NBA boxscores without overwriting older logs."""
import csv,datetime,json,urllib.request,time
from pathlib import Path
FILE=Path(__file__).resolve().parent/"data"/"nba_2026_27.csv"
FIELDS=["Game ID","Date","Player","Team","PTS","REB","AST","3PM"]
def get(url):
 req=urllib.request.Request(url,headers={"User-Agent":"PropEdge-BoxscoreSync/1.0","Accept":"application/json"})
 with urllib.request.urlopen(req,timeout=25) as response:return json.load(response)
def run():
 existing=[]
 if FILE.exists():
  with FILE.open(newline="",encoding="utf-8-sig") as f:existing=list(csv.DictReader(f))
 rows={(r["Game ID"],r["Player"].lower()):r for r in existing if r.get("Game ID") and r.get("Player")}
 today=datetime.datetime.now(datetime.timezone.utc).date()
 checked=0;completed=0;added=0
 for offset in range(0,4):
  date=today-datetime.timedelta(days=offset)
  day=date.strftime("%Y%m%d")
  try:events=get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates="+day).get("events",[])
  except Exception as e:print("Scoreboard fetch failed",day,type(e).__name__,flush=True);continue
  for event in events:
   checked+=1
   if not ((event.get("status") or {}).get("type") or {}).get("completed"):continue
   eid=str(event.get("id") or "")
   if not eid:continue
   try:data=get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event="+eid)
   except Exception as e:print("Boxscore fetch failed",eid,type(e).__name__,flush=True);continue
   groups=(data.get("boxscore") or {}).get("players") or []
   new=[]
   for group in groups:
    team=(group.get("team") or {}).get("displayName") or ""
    for statgroup in group.get("statistics") or []:
     labels=[str(x).upper() for x in statgroup.get("labels") or []]
     if not all(x in labels for x in ("PTS","REB","AST","3PT")):continue
     for a in statgroup.get("athletes") or []:
      if a.get("didNotPlay"):continue
      name=(a.get("athlete") or {}).get("displayName") or ""
      values=a.get("stats") or []
      if not name or len(values)!=len(labels):continue
      try:
       def val(k):return int(float(values[labels.index(k)]))
       threes=str(values[labels.index("3PT")]).split("-")[0]
       record={"Game ID":eid,"Date":day,"Player":name,"Team":team,
               "PTS":val("PTS"),"REB":val("REB"),"AST":val("AST"),"3PM":int(threes)}
       if min(record[k] for k in ("PTS","REB","AST","3PM"))<0:continue
       new.append(record)
      except (ValueError,IndexError,TypeError):continue
   if len(new)<10:
    print("Skipping incomplete boxscore",eid,"valid players",len(new),flush=True)
    continue
   completed+=1
   for record in new:
    key=(record["Game ID"],record["Player"].lower())
    if key not in rows:rows[key]=record;added+=1
 print("Boxscore sync:",checked,"games checked,",completed,"completed with valid boxscores,",added,"new player rows",flush=True)
 FILE.parent.mkdir(parents=True,exist_ok=True)
 with FILE.open("w",newline="",encoding="utf-8") as f:
  w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(rows.values())
if __name__=="__main__":run()
