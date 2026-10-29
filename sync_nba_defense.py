"""Fetch official NBA 2026-27 regular-season team defensive ratings.

Run: python sync_nba_defense.py
Fail closed: keep last valid snapshot, never synthesize ratings.
"""
import json,urllib.request,datetime,pathlib,sys
ROOT=pathlib.Path(__file__).resolve().parent
URL="https://stats.nba.com/stats/leaguedashteamstats"
PARAMS={"Conference":"","DateFrom":"","DateTo":"","Division":"","GameScope":"","GameSegment":"",
"LastNGames":"0","LeagueID":"00","Location":"","MeasureType":"Advanced","Month":"0",
"OpponentTeamID":"0","Outcome":"","PORound":"0","PaceAdjust":"N","PerMode":"PerGame",
"Period":"0","PlayerExperience":"","PlayerPosition":"","PlusMinus":"N","Rank":"N",
"Season":"2026-27","SeasonSegment":"","SeasonType":"Regular Season","ShotClockRange":"",
"StarterBench":"","TeamID":"0","VsConference":"","VsDivision":""}
def main():
 from urllib.parse import urlencode
 request=urllib.request.Request(URL+"?"+urlencode(PARAMS),headers={
 "User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
 "Referer":"https://www.nba.com/","Origin":"https://www.nba.com",
 "x-nba-stats-origin":"stats","x-nba-stats-token":"true","Accept":"application/json"})
 with urllib.request.urlopen(request,timeout=24) as response: data=json.load(response)
 sets=data.get("resultSets") or data.get("resultSet")
 if isinstance(sets,dict):sets=[sets]
 result=next((x for x in sets or [] if "DEF_RATING" in x.get("headers",[])),None)
 if result is None:raise ValueError("Official NBA response lacks DEF_RATING")
 headers=result["headers"];teams={}
 for row in result["rowSet"]:
  r=dict(zip(headers,row));name=str(r.get("TEAM_NAME") or "").strip()
  if not name:continue
  dr=r.get("DEF_RATING");gp=r.get("GP")
  if dr is None or gp is None or float(dr)<=0 or int(gp)<1:continue
  teams[name]={"team_id":r.get("TEAM_ID"),"def_rating":round(float(dr),2),"games":int(gp)}
 if len(teams)!=30:raise ValueError("Incomplete official team data: "+str(len(teams)))
 ranked=sorted(teams,key=lambda n:teams[n]["def_rating"])
 for i,name in enumerate(ranked,1):teams[name]["rank"]=i
 snapshot={"source":"NBA.com Stats — LeagueDashTeamStats Advanced","source_url":"https://www.nba.com/stats/teams/advanced?Season=2026-27&SeasonType=Regular+Season",
 "season":"2026-27","season_type":"Regular Season","metric":"points allowed per 100 possessions (lower is better)",
 "fetched_at":datetime.datetime.now(datetime.timezone.utc).isoformat(),"teams":teams}
 path=ROOT/"data"/"nba_defense_2026_27.json";path.parent.mkdir(exist_ok=True)
 path.write_text(json.dumps(snapshot,indent=2)+"\n")
 print("Verified NBA defensive ratings for",len(teams),"teams")
if __name__=="__main__":main()
