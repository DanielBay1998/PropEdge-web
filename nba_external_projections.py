"""Verified external daily projections adapter. No key = no external claims."""
import os,json,urllib.request
from datetime import datetime,timezone

FIELDS={"PTS":("Points","points","pts"),"REB":("Rebounds","rebounds","reb"),
"AST":("Assists","assists","ast"),"3PM":("ThreePointersMade","three_pointers_made","3pm")}
def normalize(name):
    return "".join(c for c in str(name).casefold() if c.isalnum())
def fetch_sportsdataio(day):
    key=os.getenv("SPORTSDATAIO_API_KEY","").strip()
    if not key:return {"status":"NOT_CONFIGURED","source":"SportsDataIO","players":{}}
    url="https://api.sportsdata.io/v3/nba/projections/json/PlayerGameProjectionStatsByDate/"+day
    request=urllib.request.Request(url,headers={"Ocp-Apim-Subscription-Key":key,"Accept":"application/json"})
    try:
        with urllib.request.urlopen(request,timeout=12) as response:
            payload=json.load(response)
    except Exception as exc:
        return {"status":"UNAVAILABLE","source":"SportsDataIO","error":type(exc).__name__,"players":{}}
    if not isinstance(payload,list):
        return {"status":"INVALID_RESPONSE","source":"SportsDataIO","players":{}}
    players={}
    for row in payload:
        if not isinstance(row,dict):continue
        name=row.get("Name") or row.get("PlayerName")
        if not name:continue
        values={}
        for market,keys in FIELDS.items():
            for field in keys:
                value=row.get(field)
                if isinstance(value,(float,int)) and not isinstance(value,bool) and value>=0:
                    values[market]=round(float(value),2);break
        if all(k in values for k in ("PTS","REB","AST")):
            values["PRA"]=round(sum(values[k] for k in ("PTS","REB","AST")),2)
        if values:players[normalize(name)]=values
    return {"status":"VERIFIED" if players else "EMPTY","source":"SportsDataIO",
            "fetched_at":datetime.now(timezone.utc).isoformat(),"players":players}
