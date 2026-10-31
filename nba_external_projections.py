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

def fetch_dropcoverage(day):
    """Public JSON: only accept explicitly published projections, not lines."""
    url="https://thedropcoverage.com/data/projections.json"
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"PropEdge/1.0"}),timeout=10) as response:
            payload=json.load(response)
    except Exception as exc:
        return {"status":"UNAVAILABLE","source":"Drop Coverage","error":type(exc).__name__,"players":{}}
    if not isinstance(payload,dict) or str(payload.get("slate_date"))!=day:
        return {"status":"STALE_OR_INVALID","source":"Drop Coverage","players":{}}
    players={}
    for row in payload.get("rows",[]):
        if not isinstance(row,dict):continue
        value=row.get("proj")
        if value is None:continue
        market=str(row.get("stat","")).upper()
        if market not in ("PTS","REB","AST","3PM","PRA") or not isinstance(value,(int,float)) or isinstance(value,bool) or value<0:continue
        name=normalize(row.get("player",""))
        if name:players.setdefault(name,{})[market]=round(float(value),2)
    return {"status":"VERIFIED_PARTIAL" if players else "NO_PUBLIC_PROJECTIONS",
            "source":"Drop Coverage","players":players}

def fetch_dropcoverage_full(day):
    """Optional authorized full-board feed; never scrape tokens or bypass access controls.

    Drop Coverage's publicly reusable JSON is handled by fetch_dropcoverage().
    The full members endpoint is not a redistributable public data feed.
    """
    url=os.getenv("DROP_COVERAGE_FULL_URL","").strip()
    token=os.getenv("DROP_COVERAGE_AUTHORIZED_TOKEN","").strip()
    if not url or not token:
        return {"status":"NOT_CONFIGURED","source":"Drop Coverage Full","players":{}}
    if not url.startswith("https://"):
        return {"status":"INVALID_CONFIG","source":"Drop Coverage Full","players":{}}
    try:
        request=urllib.request.Request(url,headers={"User-Agent":"PropEdge/1.0",
            "Accept":"application/json","Authorization":"Bearer "+token})
        with urllib.request.urlopen(request,timeout=8) as response:
            payload=json.load(response)
    except Exception as exc:
        return {"status":"UNAVAILABLE","source":"Drop Coverage Full","error":type(exc).__name__,"players":{}}
    if not isinstance(payload,dict) or str(payload.get("slate_date",""))!=day:
        return {"status":"STALE_OR_INVALID","source":"Drop Coverage Full","players":{}}
    rows=payload.get("rows")
    if not isinstance(rows,list):
        return {"status":"INVALID_RESPONSE","source":"Drop Coverage Full","players":{}}
    players={}
    for row in rows:
        if not isinstance(row,dict):continue
        name=normalize(row.get("player",""))
        market=str(row.get("stat","")).upper()
        value=row.get("proj")
        if name and market in ("PTS","REB","AST","3PM","PRA") and isinstance(value,(int,float)) and not isinstance(value,bool) and 0<=value<=200:
            players.setdefault(name,{})[market]=round(float(value),2)
    for stats in players.values():
        if "PRA" not in stats and all(k in stats for k in ("PTS","REB","AST")):
            stats["PRA"]=round(stats["PTS"]+stats["REB"]+stats["AST"],2)
    return {"status":"VERIFIED" if players else "EMPTY","source":"Drop Coverage Full",
            "players":players,"fetched_at":datetime.now(timezone.utc).isoformat()}

def fetch_public_propedge(day):
    """Independent third-party service sharing a name; fail closed on unknown schema."""
    url="https://propedge.today/api/projections?sport=NBA"
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"PropEdgeComparison/1.0"}),timeout=10) as response:
            payload=json.load(response)
    except Exception as exc:
        return {"status":"UNAVAILABLE","source":"propedge.today (unaffiliated)","error":type(exc).__name__,"players":{}}
    if not isinstance(payload,dict) or payload.get("slate_date")!=day:
        return {"status":"UNVERIFIED_DATE","source":"propedge.today (unaffiliated)","players":{}}
    rows=payload.get("projections")
    if not isinstance(rows,list):
        return {"status":"UNVERIFIED_SCHEMA","source":"propedge.today (unaffiliated)","players":{}}
    players={}
    for row in rows:
        if not isinstance(row,dict):continue
        name=normalize(row.get("player"))
        market=str(row.get("market","")).upper()
        value=row.get("projection")
        if name and market in ("PTS","REB","AST","3PM","PRA") and isinstance(value,(float,int)) and not isinstance(value,bool) and value>=0:
            players.setdefault(name,{})[market]=round(float(value),2)
    return {"status":"VERIFIED_PARTIAL" if players else "EMPTY","source":"propedge.today (unaffiliated)","players":players}
