#!/usr/bin/env python3
"""Guarded, manual CSV publisher for PropEdge-web. Default: validation/dry run only."""
import argparse, csv, json, math, os, shutil, tempfile
from pathlib import Path

KEY=('ResolvedPlayer','MarketCode')
FIELDS=['ResolvedPlayer','Market','MarketCode','OriginalBlendedProjection','MinutesProjection','ShadowProjection','ShadowModel','ShadowChange','ShadowChangePct','HistoricalGames','CurrentGames','ProjectedMinutes','MinutesModelStatus']
MARKETS={'PTS','REB','AST','3PM','PRA'}
APPROVAL_FIELDS={'ResolvedPlayer','MarketCode','ESPNPlayerID','ExpectedOldProjection','ApprovedNewProjection','Reviewer','ReviewDate','IdentityEvidence'}

def read_csv(path):
    with open(path,encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if reader.fieldnames is None: raise ValueError(f'Empty CSV: {path}')
        return reader.fieldnames,list(reader)

def num(value,label):
    try: x=float(value)
    except (ValueError,TypeError): raise ValueError(f'Invalid {label}: {value!r}')
    if not math.isfinite(x) or x<0: raise ValueError(f'Invalid {label}: {value!r}')
    return x

def validate(path):
    cols,rows=read_csv(path)
    if cols!=FIELDS: raise ValueError(f'CSV columns/order differ from website schema: {path}')
    if not rows: raise ValueError(f'No rows in {path}')
    seen=set()
    for i,r in enumerate(rows,2):
        k=tuple(r[v].strip() for v in KEY)
        if not all(k) or k in seen or k[1] not in MARKETS: raise ValueError(f'Invalid/duplicate player-market at line {i}: {k}')
        seen.add(k)
        num(r['ShadowProjection'],f'ShadowProjection line {i}')
        if r['MinutesModelStatus'] not in {'MODELED','UNMODELED','INSUFFICIENT_HISTORY'}: raise ValueError(f'Unexpected status at line {i}')
    return rows,{tuple(r[v].strip() for v in KEY):r for r in rows}

def run(args):
    old_rows,old=validate(args.production)
    candidate_rows,candidates=validate(args.candidate)
    if set(old)!=set(candidates): raise ValueError('Candidate roster/market keys differ from production; additions/removals are blocked')
    cols,approvals=read_csv(args.approvals)
    if not APPROVAL_FIELDS.issubset(cols): raise ValueError('Approval CSV missing required fields: '+', '.join(sorted(APPROVAL_FIELDS-set(cols))))
    approved={}
    for r in approvals:
        k=tuple(r[v].strip() for v in KEY)
        if k in approved or k not in old: raise ValueError(f'Duplicate or unknown approval: {k}')
        if not r['ESPNPlayerID'].strip().isdigit() or not all(r[v].strip() for v in ('Reviewer','ReviewDate','IdentityEvidence')):
            raise ValueError(f'Approval missing ESPN ID/reviewer/date/identity evidence: {k}')
        if abs(num(r['ExpectedOldProjection'],'old')-num(old[k]['ShadowProjection'],'production'))>1e-6:
            raise ValueError(f'Production changed since approval: {k}')
        if abs(num(r['ApprovedNewProjection'],'new')-num(candidates[k]['ShadowProjection'],'candidate'))>1e-6:
            raise ValueError(f'Candidate differs from approved projection: {k}')
        if candidates[k]['MinutesModelStatus']!='MODELED': raise ValueError(f'Approved candidate is not MODELED: {k}')
        approved[k]=r
    changed={k for k in old if old[k]!=candidates[k]}
    unapproved=changed-set(approved)
    # Fail closed: only approved rows may be changed, and no additions/removals.
    if unapproved: raise ValueError(f'{len(unapproved)} changed candidate rows lack explicit approval; first: {sorted(unapproved)[:5]}')
    output=[]
    for r in old_rows:
        k=tuple(r[v].strip() for v in KEY)
        output.append(candidates[k] if k in approved else r)
    report={'mode':'publish' if args.publish else 'dry_run','production_rows':len(old),'candidate_rows':len(candidates),'approved_rows':len(approved),'changed_rows':len(changed),'production_modified':False}
    if args.publish:
        if not approved: raise ValueError('Refusing publish with zero approvals')
        dst=args.production
        backup=dst.with_name(dst.name+'.backup')
        if backup.exists(): raise ValueError(f'Backup exists; resolve before publishing: {backup}')
        shutil.copy2(dst,backup)
        fd,tmp=tempfile.mkstemp(prefix='.propedge_',suffix='.csv',dir=dst.parent)
        try:
            with os.fdopen(fd,'w',newline='',encoding='utf-8') as f:
                w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(output)
            os.replace(tmp,dst)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        report.update(production_modified=True,backup=str(backup))
    print(json.dumps(report,indent=2))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--production',type=Path,required=True)
    p.add_argument('--candidate',type=Path,required=True)
    p.add_argument('--approvals',type=Path,required=True)
    p.add_argument('--publish',action='store_true',help='Explicitly replace production after backup; never used in GitHub Actions example')
    args=p.parse_args()
    try:run(args)
    except (ValueError,OSError) as e:p.error(str(e))
if __name__=='__main__':main()
