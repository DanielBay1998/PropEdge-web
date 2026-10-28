#!/usr/bin/env python3
"""Read-only ESPN ID/name/market reconciliation for PropEdge review."""
import argparse,csv,collections,json,pathlib
MARKETS={'PTS','REB','AST','3PM','PRA'}
def read(path):
 with open(path,encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def main():
 p=argparse.ArgumentParser();p.add_argument('--production',required=True);p.add_argument('--research',required=True);p.add_argument('--output',required=True);p.add_argument('--summary',required=True);a=p.parse_args()
 prod=read(a.production);res=read(a.research)
 prod_names=collections.defaultdict(list);research_names=collections.defaultdict(list);ids=collections.defaultdict(set)
 for r in prod:prod_names[r['ResolvedPlayer'].strip().casefold()].append(r)
 for r in res:
  name=r['Player'].strip().casefold();research_names[name].append(r);ids[name].add(r['Player ID'].strip())
 out=[]
 for name,rows in research_names.items():
  p_rows=prod_names.get(name,[]);pmarkets=[r['MarketCode'] for r in p_rows];rmarkets=[r['Market'] for r in rows];idset=ids[name]
  consistent=(len(idset)==1 and next(iter(idset)).isdigit())
  complete=(len(rows)==5 and set(rmarkets)==MARKETS and len(set(rmarkets))==5 and len(p_rows)==5 and set(pmarkets)==MARKETS and len(set(pmarkets))==5)
  for r in rows:
   matches=[x for x in p_rows if x['MarketCode']==r['Market']]
   status=('MISSING_PRODUCTION_PLAYER' if not p_rows else 'AMBIGUOUS_ESPN_ID' if not consistent else 'INCOMPLETE_OR_DUPLICATE_MARKETS' if not complete else 'MISSING_OR_DUPLICATE_MARKET' if len(matches)!=1 else 'NAME_MARKET_MATCH_ID_UNVERIFIED')
   out.append({'ESPNPlayerID':r['Player ID'],'Player':r['Player'],'MarketCode':r['Market'],'ResearchStatus':r['Status'],'IdentityStatus':status,'ProductionPlayer':matches[0]['ResolvedPlayer'] if len(matches)==1 else '', 'ResearchProjection':r['ResearchProjection'],'ProductionProjection':matches[0]['ShadowProjection'] if len(matches)==1 else '', 'CurrentGames':r['CurrentGames'],'Approval':'NO','Note':'ESPN ID exists in research, but production has no ESPN ID; independent identity proof required' if status=='NAME_MARKET_MATCH_ID_UNVERIFIED' else 'Resolve exception before approval'})
 fields=list(out[0]) if out else ['ESPNPlayerID','Player','MarketCode','ResearchStatus','IdentityStatus','ProductionPlayer','ResearchProjection','ProductionProjection','CurrentGames','Approval','Note']
 pathlib.Path(a.output).parent.mkdir(parents=True,exist_ok=True)
 with open(a.output,'w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(out)
 counts=dict(collections.Counter(x['IdentityStatus'] for x in out));summary={'research_rows':len(res),'production_rows':len(prod),'unique_research_players':len(research_names),'status_counts':counts,'approved':0,'production_modified':False}
 pathlib.Path(a.summary).write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
