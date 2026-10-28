#!/usr/bin/env python3
"""Build a non-publishing PropEdge review candidate from Release 3 research.

No changes to production. No automatic approvals. Name/market matching is a
review hint, not proof of identity. Output candidates are deliberately unchanged
unless --apply-unapproved-for-review is explicitly supplied; even then the
publisher must reject changes without independent approval.
"""
import argparse, csv, json, math
from pathlib import Path

FIELDS=['ResolvedPlayer','Market','MarketCode','OriginalBlendedProjection','MinutesProjection','ShadowProjection','ShadowModel','ShadowChange','ShadowChangePct','HistoricalGames','CurrentGames','ProjectedMinutes','MinutesModelStatus']

def read(path):
    with open(path, newline='', encoding='utf-8-sig') as f:
        reader=csv.DictReader(f)
        if reader.fieldnames is None: raise ValueError(f'Empty CSV: {path}')
        return reader.fieldnames,list(reader)

def write(path, fields, rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with open(path,'w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore'); w.writeheader();w.writerows(rows)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--production',required=True,type=Path)
    p.add_argument('--research',required=True,type=Path,help='Release 3 research_projection_comparison.csv')
    p.add_argument('--candidate',default='data/propedge_candidate_projections.csv',type=Path)
    p.add_argument('--report',default='reports/candidate_review.csv',type=Path)
    a=p.parse_args()
    cols,prod=read(a.production)
    if cols!=FIELDS: raise ValueError('Production columns/order do not match guarded publisher')
    if not prod: raise ValueError('Production is empty')
    keys=[(r['ResolvedPlayer'].strip(),r['MarketCode'].strip()) for r in prod]
    if len(set(keys))!=len(keys):raise ValueError('Duplicate production keys')
    rc,rows=read(a.research)
    required={'Player ID','Player','Market','ResearchProjection'}
    if not required.issubset(rc):raise ValueError(f'Research missing {sorted(required-set(rc))}')
    index={}; ambiguous=set()
    for r in rows:
        key=(r['Player'].strip(),r['Market'].strip())
        if key in index: ambiguous.add(key)
        index[key]=r
    report=[]
    for row in prod:
        key=(row['ResolvedPlayer'].strip(),row['MarketCode'].strip())
        r=index.get(key)
        if not r:continue
        reason='REVIEW_REQUIRED_ID_NOT_IN_PRODUCTION'
        if key in ambiguous: reason='AMBIGUOUS_NAME_MARKET'
        elif not r['Player ID'].strip().isdigit():reason='INVALID_ESPN_ID'
        else:
            try:
                new=float(r['ResearchProjection']);old=float(row['ShadowProjection'])
                if not math.isfinite(new) or new<0:reason='INVALID_RESEARCH_PROJECTION'
                elif abs(new-old)<=1e-6:reason='UNCHANGED_PROJECTION'
            except ValueError:reason='INVALID_RESEARCH_PROJECTION'
        report.append({'ResolvedPlayer':key[0],'MarketCode':key[1],
                       'ESPNPlayerID':r['Player ID'].strip(),
                       'ExistingProjection':row['ShadowProjection'],
                       'ResearchProjection':r['ResearchProjection'],
                       'ReviewStatus':reason,'Approved': 'NO'})
    # Exact copy is intentional: never imply an unapproved research estimate is publishable.
    write(a.candidate,FIELDS,prod)
    write(a.report,['ResolvedPlayer','MarketCode','ESPNPlayerID','ExistingProjection','ResearchProjection','ReviewStatus','Approved'],report)
    print(json.dumps({'production_rows':len(prod),'candidate_rows':len(prod),
                      'matched_research_rows':len(report),'unmatched_research_rows':len(rows)-len(report),
                      'candidate_changes':0,'approvals_created':0,
                      'candidate':str(a.candidate),'review_report':str(a.report)},indent=2))

if __name__=='__main__':main()
