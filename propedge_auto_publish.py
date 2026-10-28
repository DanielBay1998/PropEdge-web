#!/usr/bin/env python3
"""Opt-in automatic PropEdge CSV publishing using unambiguous name+market matches.
This is intentionally separate from the approval-only guarded publisher.
"""
import argparse, csv, datetime as dt, json, math
from pathlib import Path

FIELDS = ['ResolvedPlayer','Market','MarketCode','OriginalBlendedProjection','MinutesProjection','ShadowProjection','ShadowModel','ShadowChange','ShadowChangePct','HistoricalGames','CurrentGames','ProjectedMinutes','MinutesModelStatus']
MARKETS={'PTS','REB','AST','3PM','PRA'}

def read(path):
    with open(path, newline='', encoding='utf-8-sig') as f:
        r=csv.DictReader(f)
        return r.fieldnames, list(r)

def number(value):
    x=float(value)
    if not math.isfinite(x): raise ValueError('nonfinite number')
    return x

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--production',required=True,type=Path)
    p.add_argument('--research',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--report',required=True,type=Path)
    p.add_argument('--max-age-days',type=int,default=7)
    p.add_argument('--min-current-games',type=int,default=2)
    a=p.parse_args()
    cols,base=read(a.production)
    if cols!=FIELDS or not base: raise SystemExit('Unexpected/empty production CSV schema')
    rcols,research=read(a.research)
    required={'Player ID','Player','Market','ResearchProjection','ExpectedMinutes','CurrentGames','LastGameDate','Status'}
    if not required.issubset(set(rcols or [])): raise SystemExit(f'Missing research columns: {required-set(rcols or [])}')
    counts={}; research_by_key={}
    for r in research:
        k=(r['Player'].strip().casefold(),r['Market'].strip().upper())
        counts[k]=counts.get(k,0)+1
        research_by_key[k]=r
    base_keys=[(r['ResolvedPlayer'].strip().casefold(),r['MarketCode'].strip().upper()) for r in base]
    if len(set(base_keys))!=len(base_keys): raise SystemExit('Duplicate production player-market keys')
    today=dt.datetime.now(dt.timezone.utc).date()
    changed=[]; skipped={}; result=[]; identities={}
    for r in research:
        name=r['Player'].strip().casefold(); pid=r['Player ID'].strip()
        if pid.isdigit(): identities.setdefault(name,set()).add(pid)
    for old,k in zip(base,base_keys):
        row=old.copy(); r=research_by_key.get(k)
        reason=None
        if r is None: reason='NO_RESEARCH_MATCH'
        elif counts[k]!=1: reason='DUPLICATE_RESEARCH_KEY'
        elif k[1] not in MARKETS: reason='UNSUPPORTED_MARKET'
        elif not r['Player ID'].strip().isdigit() or len(identities.get(k[0],set()))!=1: reason='AMBIGUOUS_RESEARCH_ID'
        elif r['Status'].strip()!='RESEARCH_ONLY': reason='RESEARCH_FLAGGED'
        else:
            try:
                projection=number(r['ResearchProjection']); mins=number(r['ExpectedMinutes'])
                games=int(r['CurrentGames']); date=dt.date.fromisoformat(r['LastGameDate'])
                if projection<0 or mins<=0 or mins>60: reason='INVALID_VALUES'
                elif games<a.min_current_games: reason='INSUFFICIENT_GAMES'
                elif date>today or (today-date).days>a.max_age_days: reason='STALE_GAME_LOG'
                else:
                    prior=number(old['OriginalBlendedProjection'])
                    row['ShadowProjection']=str(projection)
                    row['MinutesProjection']=str(projection)
                    row['ShadowModel']='ResearchAutoNameMatch'
                    row['ShadowChange']=str(projection-prior)
                    row['ShadowChangePct']=str(100*(projection-prior)/prior) if prior else '0'
                    row['CurrentGames']=str(games)
                    row['ProjectedMinutes']=str(mins)
                    row['MinutesModelStatus']='MODELED'
            except (ValueError,OverflowError,TypeError): reason='INVALID_RESEARCH_DATA'
        if reason: skipped[reason]=skipped.get(reason,0)+1
        if row!=old: changed.append({'Player':old['ResolvedPlayer'],'MarketCode':old['MarketCode'],'ESPNPlayerID':r['Player ID'],'OldProjection':old['ShadowProjection'],'NewProjection':row['ShadowProjection']})
        result.append(row)
    if len(result)!=len(base) or [tuple(r[c] for c in ('ResolvedPlayer','MarketCode')) for r in result]!=[tuple(r[c] for c in ('ResolvedPlayer','MarketCode')) for r in base]:
        raise SystemExit('Production keys changed unexpectedly')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(result)
    a.report.parent.mkdir(parents=True,exist_ok=True)
    a.report.write_text(json.dumps({'rows':len(result),'updated_rows':len(changed),'skipped':skipped,'changes':changed,'identity_caveat':'Research ESPN IDs are not independently linked to production rows; publication uses unambiguous player-name+market matching as explicitly requested.'},indent=2)+'\n')
    print(json.dumps({'rows':len(result),'updated_rows':len(changed),'skipped':skipped},indent=2))

if __name__=='__main__':main()
