"""Refresh verified ESPN NFL game weather/venue and market context.
Preserves independently sourced advanced metrics already in the context CSV.
Only fields explicitly present in ESPN responses are imported.
"""
import csv,datetime,json,urllib.request
from pathlib import Path
from nfl_context import FILE,NUMERIC,CATEGORICAL,timestamp
from nfl import espn
def run():
 existing=[]
 if FILE.exists():
  with FILE.open(newline="",encoding="utf-8-sig") as f:existing=list(csv.DictReader(f))
 # ESPN scoreboard date is the season year. Future games only.
 now=datetime.datetime.now(datetime.timezone.utc)
 for week in range(1,19):
  try:data=espn(f"scoreboard?dates={now.year}&seasontype=2&week={week}&limit=40")
  except Exception as e:
   print("ESPN week unavailable",week,type(e).__name__);continue
  for event in data.get("events",[]):
   kickoff=timestamp(event.get("date"))
   if not kickoff or not (now<=kickoff<=now+datetime.timedelta(days=8)):continue
   comp=(event.get("competitions") or [{}])[0]
   weather=comp.get("weather") or event.get("weather") or {}
   if not isinstance(weather,dict):weather={}
   venue=comp.get("venue") or {}
   row={"event_id":str(event.get("id") or ""),"observed_at":now.isoformat(),"source":"ESPN scoreboard"}
   temp=weather.get("temperature")
   if temp is not None:row["temperature_f"]=temp
   for key,target in (("windSpeed","wind_mph"),("humidity","humidity_pct")):
    if isinstance(weather.get(key),(int,float)):row[target]=weather[key]
   roof=venue.get("roofType")
   if roof:row["stadium_roof"]=str(roof)
   surface=venue.get("grass")
   if isinstance(surface,bool):row["surface"]="grass" if surface else "artificial"
   odds=(comp.get("odds") or [])
   if odds and isinstance(odds[0],dict):
    o=odds[0]
    if isinstance(o.get("overUnder"),(int,float)):row["game_total"]=o["overUnder"]
    if isinstance(o.get("spread"),(int,float)):row["spread"]=o["spread"]
   if len(row)<=3:continue
   existing=[r for r in existing if not (r.get("event_id")==row["event_id"] and r.get("source")=="ESPN scoreboard")]
   existing.append(row)
 FILE.parent.mkdir(exist_ok=True)
 cols=["event_id","player_id","team","observed_at","source"]+list(NUMERIC)+list(CATEGORICAL)
 with FILE.open("w",newline="",encoding="utf-8") as f:
  w=csv.DictWriter(f,fieldnames=list(dict.fromkeys(cols)),extrasaction="ignore")
  w.writeheader();w.writerows(existing)
 print("Verified NFL context rows:",len(existing))
if __name__=="__main__":run()
