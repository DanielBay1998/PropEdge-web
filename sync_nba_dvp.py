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
 games=collections.defaultdict(lambda:collections.defaultdict(list))
 with (ROOT/"data"/"nba_2026_27.csv").open(newline="",encoding="utf-8-sig") as f:
  for r in csv.DictReader(f):
   if r["Date"]<"20261020":continue
   pos=positions.get(r["Player"].strip().casefold())
   if not pos:continue
   try:stats={k:float(r[k]) for k in ("PTS","REB","AST","3PM")}
   except (KeyError,ValueError,TypeError):continue
   games[(r["Date"],r["Game ID"])][r["Team"]].append((pos,stats))
 allowances=collections.defaultdict(lambda:collections.defaultdict(lambda:collections.defaultdict(list)))
 for (date,gid),pair in games.items():
  if len(pair)!=2:continue
  a,b=list(pair)
  for defense,offense in ((a,b),(b,a)):
   for pos,stats in pair[offense]:
    for stat,value in stats.items():allowances[defense][pos][stat].append(value)
 out={}
 for team,positions_data in allowances.items():
  out[team]={}
  for pos,stats in positions_data.items():
   out[team][pos]={k:{"allowed_per_player_game":round(statistics.mean(v),2),"player_games":len(v)}
     for k,v in stats.items() if len(v)>=8}
 out={t:p for t,p in out.items() if p}
 if len(out)<20:raise RuntimeError("Insufficient verified DvP team coverage: "+str(len(out)))
 result={"source":"ESPN completed NBA boxscores + ESPN current roster positions",
 "season":"2026-27","metric":"opposing player boxscore stat per appearance, grouped by listed roster position",
 "warning":"Position is current roster assignment; traded players and small samples may distort history. Not defender assignments or per-possession rating.",
 "fetched_at":datetime.datetime.now(datetime.timezone.utc).isoformat(),"teams":out,"position_players":len(positions)}
 path=ROOT/"data"/"nba_dvp_2026_27.json";path.write_text(json.dumps(result,indent=2)+"\n")
 print("DvP teams",len(out),"roster players",len(positions))
if __name__=="__main__":main()
