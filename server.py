from pathlib import Path
import os, csv, math
from collections import defaultdict
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'data'
app=FastAPI(title='PropEdge Custom')
app.mount('/static',StaticFiles(directory=str(ROOT/'static')),name='static')

def rows(path):
    if not path.exists(): return []
    with path.open(newline='',encoding='utf-8-sig') as f:return list(csv.DictReader(f))
def num(x):
    try:
        v=float(x)
        return v if math.isfinite(v) else None
    except (TypeError,ValueError):return None

def build():
    projections=rows(DATA/'propedge_shadow_projections.csv')
    odds=rows(DATA/'saved_odds_snapshot.csv')
    by_player=defaultdict(list)
    for r in projections:by_player[r.get('ResolvedPlayer','')].append(r)
    grouped=defaultdict(list)
    for r in odds:
        line=num(r.get('Line'))
        if line is None:continue
        key=(r.get('Player','').casefold().strip(),r.get('Market',''),r.get('Game',''),r.get('Side',''))
        grouped[key].append(r)
    market_codes={'player_points':'PTS','player_rebounds':'REB','player_assists':'AST','player_threes':'3PM','player_threes_made':'3PM','player_points_rebounds_assists':'PRA','player_points_rebounds_assists_combined':'PRA'}
    player_lookup={p.casefold().strip():p for p in by_player}
    signals=[]
    for (name,market,game,side),books in grouped.items():
        actual=player_lookup.get(name)
        if not actual:continue
        code=market_codes.get(market)
        if not code:continue
        matched=next((r for r in by_player[actual] if r.get('MarketCode')==code),None)
        if not matched or matched.get('MinutesModelStatus')!='MODELED':continue
        projection=num(matched.get('ShadowProjection'))
        if projection is None:continue
        # Lines differ across books. Show only a specific quoted line and its associated book.
        for quote in books:
            line=num(quote.get('Line'))
            if line is None:continue
            gap=(projection-line) if side.lower()=='over' else (line-projection)
            if gap<=0:continue
            signals.append({'player':actual,'market':code,'side':side.upper(),'line':line,'projection':round(projection,2),'gap':round(gap,2),'book':quote.get('Sportsbook','Unknown'),'game':game,'odds':quote.get('Odds',''),'status':matched.get('MinutesModelStatus'),'history':matched.get('HistoricalGames'),'current':matched.get('CurrentGames'),'minutes':matched.get('ProjectedMinutes'),'source':'saved snapshot'})
    # Rank by normalized difference, deduplicate player/market/side/line; this is NOT EV.
    best={}
    for s in signals:
        key=(s['player'],s['market'],s['side'],s['line'])
        if key not in best or s['gap']>best[key]['gap']:best[key]=s
    ranked=sorted(best.values(),key=lambda s:s['gap']/max(s['line'],1),reverse=True)
    return {'signals':ranked[:200],'players':len(by_player),'quotes':len(odds),'snapshot_modified':datetime.fromtimestamp((DATA/'saved_odds_snapshot.csv').stat().st_mtime,timezone.utc).isoformat() if (DATA/'saved_odds_snapshot.csv').exists() else None,'live':False}

@app.get('/api/board')
def board():return build()
@app.get('/')
def home():return HTMLResponse((ROOT/'static'/'index.html').read_text(encoding='utf-8'))
