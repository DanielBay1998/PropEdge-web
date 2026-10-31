"""Conservative, source-identified NBA positional matchup overlay.
Not a per-possession pace model. Never invents player team assignments.
"""
import re
def _key(s):
    return re.sub(r"[^a-z0-9 ]","",str(s or "").lower()).strip()
def matchup_factor(signal,projection,snapshot):
    if not snapshot or not snapshot.get("teams"):return projection,None
    positions=snapshot.get("player_positions",{})
    player=_key(signal.get("player"))
    position=positions.get(player) or positions.get(signal.get("player"))
    game=str(signal.get("game") or "")
    teams=game.split(" @ ")
    if len(teams)!=2 or not position:return projection,None
    team_data=snapshot["teams"]
    # Opponent identity must be explicit. Do not guess based on recent stats.
    own=signal.get("team") or signal.get("player_team")
    if not own:return projection,None
    opponent=next((x for x in teams if _key(x)!=_key(own)),None) if any(_key(x)==_key(own) for x in teams) else None
    if not opponent:return projection,None
    market=signal.get("market")
    metric=team_data.get(opponent,{}).get(position,{}).get(market,{})
    if not isinstance(metric,dict):return projection,None
    allowed=metric.get("blended_allowed")
    try:allowed=float(allowed)
    except (TypeError,ValueError):return projection,None
    baselines=[]
    for row in team_data.values():
        v=row.get(position,{}).get(market,{})
        if isinstance(v,dict):
            try:
                n=float(v.get("blended_allowed"))
                if n>0:baselines.append(n)
            except (TypeError,ValueError):pass
    if len(baselines)<10 or allowed<=0:return projection,None
    league=sum(baselines)/len(baselines)
    factor=max(.94,min(1.06,1+.20*(allowed/league-1)))
    return round(float(projection)*factor,2),{"factor":round(factor,4),"opponent":opponent,"position":position,"source":"ESPN DVP per appearance"}
