"""Build position-specific defensive allowances from completed ESPN player boxscores.

Position assignments come from ESPN roster API, not inferred from points or
player names. Only games with both teams and mapped players count.
"""
import csv,json,urllib.request,datetime,collections,pathlib,statistics
ROOT=pathlib.Path(__file__).resolve().parent
API="https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams"
def fetch(url):
 req=urllib.request.Request(url,headers={"User-Agent":"PropEdgeResearch/1.0"})
 with urllib.request.urlopen(req,timeout=15) as response:return json.load(response)
def main():
 teams=fetch(API+"?limit=40")["sports"][0]["leagues"][0]["teams"]
 positions={};roster_count=0
 for entry in teams:
  t=entry["team"];tid=t["id"]
  try:roster=fetch(API+"/"+str(tid)+"/roster")
  except Exception as e:print("Roster unavailable",tid,type(e).__name__);continue
  for group in roster.get("athletes",[]):
   athletes=group.get("items",[]) if isinstance(group,dict) and "items" in group else [group]
   for p in athletes:
    name=str(p.get("displayName") or p.get("fullName") or "").strip().casefold()
    pos=(p.get("position") or {}).get("abbreviation","").upper()
    if name and pos in ("PG","SG","SF","PF","C"):
     positions[name]=pos;roster_count+=1
 if len(positions)<250:raise RuntimeError("Incomplete ESPN roster coverage: "+str(len(positions)))
 # Position-specific prior is built from completed regular-season boxscores.
 # Current roster position is an imperfect historical proxy and is disclosed.
 def collect(path,season):
  games=collections.defaultdict(lambda:collections.defaultdict(list))
  if not path.exists():return {}
  with path.open(newline="",encoding="utf-8-sig") as f:
   for row in csv.DictReader(f):
    date=str(row.get("Date") or "")
    if season=="current" and date<"20261020":continue
    if season=="prior" and str(row.get("Season Type") or "").casefold()!="regular":continue
    pos=positions.get(str(row.get("Player") or "").strip().casefold())
    if not pos:continue
    try:stats={k:float(row[k]) for k in ("PTS","REB","AST","3PM")}
    except (KeyError,ValueError,TypeError):continue
    if min(stats.values())<0:continue
    games[(date,str(row.get("Game ID") or ""))][str(row.get("Team") or "")].append((pos,stats))
  allowances=collections.defaultdict(lambda:collections.defaultdict(lambda:collections.defaultdict(list)))
  for (date,gid),pair in games.items():
   if len(pair)!=2:continue
   a,b=list(pair)
   if min(len(pair[a]),len(pair[b]))<5:continue
   for defense,offense in ((a,b),(b,a)):
    for pos,stats in pair[offense]:
     for stat,value in stats.items():allowances[defense][pos][stat].append(value)
  return allowances
 prior=collect(ROOT/"data"/"propedge_historical_2025_26_minutes.csv","prior")
 current=collect(ROOT/"data"/"nba_2026_27.csv","current")
 # Prior strength is 20 equivalent player appearances per position/stat.
 # The current-season contribution increases naturally with each completed game.
 prior_weight=20
 out={}
 for team in set(prior)|set(current):
  out[team]={}
  for pos in ("PG","SG","SF","PF","C"):
   stats={}
   for stat in ("PTS","REB","AST","3PM"):
    old=prior.get(team,{}).get(pos,{}).get(stat,[])
    new=current.get(team,{}).get(pos,{}).get(stat,[])
    if not old and len(new)<5:continue
    old_avg=statistics.mean(old) if old else None
    new_avg=statistics.mean(new) if new else None
    weight=min(len(old),prior_weight) if old else 0
    if old_avg is None:blended=new_avg
    else:blended=(old_avg*weight+sum(new))/(weight+len(new))
    stats[stat]={"blended_allowed":round(blended,2),
     "prior_allowed":round(old_avg,2) if old_avg is not None else None,
     "current_allowed":round(new_avg,2) if new_avg is not None else None,
     "prior_player_games":len(old),"current_player_games":len(new),
     "prior_equivalent_weight":weight,
     "current_weight_pct":round(100*len(new)/(weight+len(new)),1) if weight+len(new) else 0}
   if stats:out[team][pos]=stats
 out={t:p for t,p in out.items() if p}
 if len(out)<20:raise RuntimeError("Insufficient verified DvP team coverage: "+str(len(out)))
 result={"source":"ESPN completed NBA boxscores + ESPN current roster positions",
 "season":"2026-27","historical_season":"2025-26",
 "blend_method":"20 prior equivalent player appearances per position and stat, with all verified current-season appearances; current weight rises automatically",
 "metric":"opposing player boxscore stat per appearance, grouped by listed roster position",
 "warning":"Historical player positions use current roster assignments; traded players and small samples can distort allowances. Not defender assignments or per-possession rating.",
 "fetched_at":datetime.datetime.now(datetime.timezone.utc).isoformat(),"teams":out,"position_players":len(positions)}
 path=ROOT/"data"/"nba_dvp_2026_27.json";path.write_text(json.dumps(result,indent=2)+"\n")
 print("DvP teams",len(out),"roster players",len(positions))
if __name__=="__main__":main()
