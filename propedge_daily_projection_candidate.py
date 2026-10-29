"""Generate a non-production projection candidate and audit. Never overwrites production."""
import argparse
from pathlib import Path
import pandas as pd
import numpy as np

MARKETS={'PTS':'PTS','REB':'REB','AST':'AST','3PM':'3PM','PRA':'PRA'}

def run(args):
    logs=pd.read_csv(args.logs,dtype={'Game ID':str,'Date':str})
    base=pd.read_csv(args.projections)
    required={'Game ID','Date','Player','PTS','REB','AST','3PM'}
    assert required.issubset(logs.columns),f'Missing game-log columns: {required-set(logs.columns)}'
    assert {'ResolvedPlayer','MarketCode','ShadowProjection','OriginalBlendedProjection'}.issubset(base.columns)
    assert not logs.duplicated(['Game ID','Player']).any(),'Duplicate player-game records'
    assert not base.duplicated(['ResolvedPlayer','MarketCode']).any(),'Duplicate player-market projections'
    for c in ('PTS','REB','AST','3PM'):
        logs[c]=pd.to_numeric(logs[c],errors='raise')
        assert logs[c].notna().all() and (logs[c]>=0).all(),f'Invalid {c}'
    logs['PRA']=logs['PTS']+logs['REB']+logs['AST']
    logs['MIN']=pd.to_numeric(logs['MIN'],errors='coerce') if 'MIN' in logs.columns else np.nan
    logs.loc[~logs['MIN'].between(1,60),'MIN']=np.nan
    logs['Date']=pd.to_datetime(logs['Date'],format='%Y%m%d',errors='raise')
    logs=logs.sort_values(['Player','Date','Game ID'])
    # Exact-name matches only; intentionally avoid fuzzy joins and unsupported injury assumptions.
    groups={name:g for name,g in logs.groupby('Player')}
    rows=[]
    for r in base.itertuples(index=False):
        name=r.ResolvedPlayer; market=r.MarketCode
        prior=float(r.ShadowProjection) if pd.notna(r.ShadowProjection) else np.nan
        g=groups.get(name)
        status='RETAIN_UNCHANGED'
        candidate=prior
        count=0;recent=np.nan;last=''
        recent_minutes=np.nan;projected_minutes=np.nan;minutes_samples=0;minutes_adjustment=np.nan
        if g is not None:
            count=len(g);last=g['Date'].max().strftime('%Y-%m-%d')
        if market in MARKETS and g is not None and count>=args.min_games and np.isfinite(prior) and prior>=0:
            last_games=g.tail(10)
            recent=float(last_games[market].mean())
            valid=g[g['MIN'].notna()].tail(20)
            minutes_samples=len(valid)
            # Require sufficient minutes observations; never infer minutes from missing rows.
            if minutes_samples>=3:
                recent_minutes=float(valid.tail(5)['MIN'].mean())
                long_minutes=float(valid['MIN'].mean())
                projected_minutes=float(np.clip(0.7*recent_minutes+0.3*long_minutes,8,42))
                # Per-minute rate shrunk toward the player's full available sample.
                rate_recent=float((valid.tail(10)[market]/valid.tail(10)['MIN']).mean())
                rate_long=float((valid[market]/valid['MIN']).mean())
                minute_based=projected_minutes*(0.65*rate_recent+0.35*rate_long)
                minutes_adjustment=minute_based-recent
                recent=0.75*recent+0.25*minute_based
            # Conservative research candidate; production remains untouched.
            raw=0.8*prior+0.2*recent
            cap=max(1.0,abs(prior)*args.max_change_pct/100)
            candidate=max(0.,float(np.clip(raw,prior-cap,prior+cap)))
            status='CANDIDATE_ONLY'
        rows.append({'ResolvedPlayer':name,'MarketCode':market,'PreviousProjection':prior,
                     'CandidateProjection':round(candidate,5) if np.isfinite(candidate) else np.nan,
                     'Change':round(candidate-prior,5) if np.isfinite(candidate) and np.isfinite(prior) else np.nan,
                     'CurrentGames':count,'Recent10Mean':recent,'LastGameDate':last,'Status':status,
                     'MinutesSamples':minutes_samples,'RecentMinutes':recent_minutes,
                     'ProjectedMinutes':projected_minutes,'MinutesRateAdjustment':minutes_adjustment})
    out=pd.DataFrame(rows)
    # PRA must equal the sum of candidate PTS, REB and AST, when all four markets exist.
    # Keep the baseline model untouched; this is an audit-only candidate.
    wide=out.pivot(index='ResolvedPlayer',columns='MarketCode',values='CandidateProjection')
    if {'PTS','REB','AST','PRA'}.issubset(wide.columns):
        valid=wide[['PTS','REB','AST','PRA']].notna().all(axis=1)
        sums=wide.loc[valid,['PTS','REB','AST']].sum(axis=1).round(5)
        pra_mask=out['MarketCode'].eq('PRA') & out['ResolvedPlayer'].isin(sums.index)
        out.loc[pra_mask,'CandidateProjection']=out.loc[pra_mask,'ResolvedPlayer'].map(sums)
        out.loc[pra_mask,'Change']=(out.loc[pra_mask,'CandidateProjection']-out.loc[pra_mask,'PreviousProjection']).round(5)
        out.loc[pra_mask,'Status']='PRA_RECONCILED_CANDIDATE'
        # Report candidates exceeding the normal per-market movement cap; do not publish.
        caps=out['PreviousProjection'].abs().mul(args.max_change_pct/100).clip(lower=1.0)
        out['ExceedsChangeCap']=out['Change'].abs().gt(caps+0.00001)
        check=out.pivot(index='ResolvedPlayer',columns='MarketCode',values='CandidateProjection')
        delta=(check.loc[valid,'PRA']-check.loc[valid,['PTS','REB','AST']].sum(axis=1)).abs()
        assert (delta<0.00002).all(),'PRA mismatch after reconciliation'
    else:
        out['ExceedsChangeCap']=False
    assert len(out)==len(base) and not out.duplicated(['ResolvedPlayer','MarketCode']).any()
    assert out.CandidateProjection.notna().sum()==base.ShadowProjection.notna().sum()
    assert (out.CandidateProjection.dropna()>=0).all()
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    out.to_csv(path,index=False)
    print(f'Candidate report: {path}; {len(out)} rows; {sum(out.Status=="CANDIDATE_ONLY")} eligible; production files unchanged.')

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--logs',default='data/nba_2026_27.csv')
    p.add_argument('--projections',default='data/propedge_shadow_projections.csv')
    p.add_argument('--output',default='reports/projection_candidate_audit.csv')
    p.add_argument('--min-games',type=int,default=3)
    p.add_argument('--max-change-pct',type=float,default=15)
    a=p.parse_args()
    if a.min_games<3 or not 0<a.max_change_pct<=20:p.error('Unsafe configuration')
    run(a)
