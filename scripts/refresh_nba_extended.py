"""Backfill and refresh NBA regular-season player box scores without inventing missing stats.
Run: python scripts/refresh_nba_extended.py
"""
import csv, os, sys, time
from pathlib import Path
from datetime import datetime, timezone
from nba_api.stats.endpoints import leaguegamefinder

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"data"/"nba_extended_gamelogs.csv"
SEASONS=("2025-26","2026-27")
FIELDS=("SEASON","GAME_ID","GAME_DATE","PLAYER_ID","PLAYER_NAME","TEAM_ABBREVIATION","MIN","PTS","REB","AST","FG3M","FG3A","FGM","FGA","FTM","FTA","OREB","DREB","TOV","STL","BLK","2PM","2PA")
def num(row,key):
    v=row.get(key)
    if v is None or str(v).strip()=="":
        return None
    try: return float(v)
    except (ValueError,TypeError): return None
def normalize(row,season):
    fgm,fga,fg3m,fg3a=(num(row,k) for k in ("FGM","FGA","FG3M","FG3A"))
    result={"SEASON":season}
    for key in FIELDS:
        if key not in ("SEASON","2PM","2PA"): result[key]=row.get(key,"")
    result["2PM"]=round(fgm-fg3m,3) if fgm is not None and fg3m is not None and fgm>=fg3m else ""
    result["2PA"]=round(fga-fg3a,3) if fga is not None and fg3a is not None and fga>=fg3a else ""
    if not result.get("GAME_ID") or not result.get("PLAYER_ID") or not result.get("GAME_DATE"):
        raise ValueError("Missing game/player/date identity")
    return result
def fetch(season):
    last=None
    for attempt in range(3):
        try:
            response=leaguegamefinder.LeagueGameFinder(
                player_or_team_abbreviation="P",league_id_nullable="00",
                season_nullable=season,season_type_nullable="Regular Season",
                timeout=45)
            rows=response.get_normalized_dict().get("LeagueGameFinderResults",[])
            if not rows: raise RuntimeError("NBA endpoint returned zero player games")
            required={"GAME_ID","GAME_DATE","PLAYER_ID","PLAYER_NAME","FGM","FGA","FG3M","FG3A","FTM","FTA","TOV","STL","BLK"}
            if not required.issubset(rows[0]): raise RuntimeError("NBA response missing required fields: "+str(sorted(required-set(rows[0]))))
            return [normalize(r,season) for r in rows]
        except Exception as e:
            last=e
            if attempt<2: time.sleep(4*(attempt+1))
    raise RuntimeError(f"{season} fetch failed: {last}")
def main():
    merged={}
    if OUT.exists():
        with OUT.open(newline="",encoding="utf-8") as file:
            for row in csv.DictReader(file):
                if row.get("GAME_ID") and row.get("PLAYER_ID"):
                    merged[(row.get("SEASON"),row["GAME_ID"],row["PLAYER_ID"])]=row
    for season in SEASONS:
        fresh=fetch(season)
        for row in fresh: merged[(row["SEASON"],str(row["GAME_ID"]),str(row["PLAYER_ID"]))]=row
        print(f"{season}: {len(fresh)} player-game rows")
        time.sleep(2)
    if len(merged)<100: raise RuntimeError("Too few player-game rows; refusing overwrite")
    OUT.parent.mkdir(parents=True,exist_ok=True)
    temp=OUT.with_suffix(".tmp")
    with temp.open("w",newline="",encoding="utf-8") as file:
        writer=csv.DictWriter(file,fieldnames=FIELDS,extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted(merged.values(),key=lambda r:(r["GAME_DATE"],str(r["GAME_ID"]),str(r["PLAYER_ID"]))))
    os.replace(temp,OUT)
    print(f"Saved {len(merged)} rows to {OUT}")
if __name__=="__main__": main()
