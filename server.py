from pathlib import Path
import os,csv,math,time,threading,json,re
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from datetime import datetime,timezone,timedelta
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

ROOT=Path(__file__).resolve().parent
app=FastAPI(title="PropEdge Live Validated")
from nfl import router as nfl_router
app.include_router(nfl_router)
app.mount("/static",StaticFiles(directory=str(ROOT/"static")),name="static")
MARKETS={"player_points":"PTS","player_rebounds":"REB","player_assists":"AST","player_threes":"3PM","player_points_rebounds_assists":"PRA"}
_cache={"until":0,"result":None};_lock=threading.Lock()
_rate_limit={"until":0,"failures":0}
class ProviderRateLimited(Exception):pass

def is_rate_limited(exc):
    return ("ratelimit" in type(exc).__name__.lower() or
            "rate limit" in str(exc).lower() or
            "429" in str(exc))

def number(value):
    try:
        v=float(value)
        return v if math.isfinite(v) else None
    except (TypeError,ValueError):return None

def norm(s):return " ".join(str(s or "").casefold().split())

# Verified, narrowly scoped naming variants. Do not fuzzy-merge other players.
PLAYER_ALIASES={"ace bailey (uta)":"ace bailey","airious bailey":"ace bailey","airious ace bailey":"ace bailey"}
def canonical_player(name):
    raw=norm(name)
    # Strip only a terminal parenthesized NBA team abbreviation, not arbitrary nicknames.
    raw=re.sub(r"\s*\((?:ATL|BOS|BKN|CHA|CHI|CLE|DAL|DEN|DET|GSW|HOU|IND|LAC|LAL|MEM|MIA|MIL|MIN|NOP|NYK|OKC|ORL|PHI|PHX|POR|SAC|SAS|TOR|UTA|WAS)\)$", "", raw, flags=re.I)
    raw=re.sub(r"[’‘ʼ`´']", "", raw)
    raw=re.sub(r"[^\w\s-]", "", raw)
    return PLAYER_ALIASES.get(raw,raw)

def projection_freshness():
    paths={"projection_csv":ROOT/"data"/"propedge_shadow_projections.csv",
           "current_game_logs":ROOT/"data"/"nba_2026_27.csv"}
    output={}
    for label,path in paths.items():
        output[label]={"present":path.is_file(),"modified_utc":datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat() if path.is_file() else None}
    return output

def projections():
    p=ROOT/"data"/"propedge_shadow_projections.csv"
    if not p.exists():return {}
    with p.open(encoding="utf-8-sig",newline="") as f: rows=list(csv.DictReader(f))
    result={}
    for r in rows:
        if r.get("MinutesModelStatus")!="MODELED":continue
        n=number(r.get("ShadowProjection"))
        if n is None or n<0:continue
        result[(canonical_player(r.get("ResolvedPlayer")),r.get("MarketCode"))]=r
    return result


# Historical statistics are descriptive, not calibrated win probabilities.
# The current-season source has no season-type or minutes field. To avoid
# mixing preseason with regular-season stats, include only dates from
# the first known regular-season week (Oct 20, 2026 onward).
from functools import lru_cache

@lru_cache(maxsize=1)
def load_game_logs():
    logs=defaultdict(list)
    sources=[(ROOT/'data'/'nba_2026_27.csv','current'),
             (ROOT/'data'/'propedge_historical_2025_26_minutes.csv','historical')]
    for path,season in sources:
        if not path.exists():continue
        with path.open(encoding='utf-8-sig',newline='') as handle:
            for row in csv.DictReader(handle):
                player=canonical_player(row.get('Player'))
                date=str(row.get('Date') or '').strip()
                if not player or len(date)!=8 or not date.isdigit():continue
                if season=='current' and date<'20261020':continue
                if season=='historical' and norm(row.get('Season Type'))!='regular':continue
                values={k:number(row.get(k)) for k in ('PTS','REB','AST','3PM','MIN')}
                if any(values[k] is None or values[k]<0 for k in ('PTS','REB','AST','3PM')):continue
                logs[player].append({'date':date,'game_id':str(row.get('Game ID') or ''),
                                     'team':row.get('Team') or '', 'season':season, **values})
    for rows in logs.values():
        # Most recent first; ignore duplicated player/game rows across sources.
        rows.sort(key=lambda r:(r['date'],r['game_id']),reverse=True)
        seen=set();rows[:]=[r for r in rows if not ((r['game_id'],r['date']) in seen or seen.add((r['game_id'],r['date'])))]
    return logs

def early_sample_estimates(logs,model,min_games=3):
    """Explicitly descriptive, non-validated estimates for missing model players.

    These are recent regular-season sample means, never MODELED projections.
    They cannot enter Top Picks or the qualification engine.
    """
    existing={name for name,market in model}
    result={}
    for player,rows in logs.items():
        if player in existing:continue
        current=[r for r in rows if r['season']=='current']
        if len(current)<min_games:continue
        recent=current[:5]
        for code in ('PTS','REB','AST','3PM','PRA'):
            def stat(r):return sum(r[k] for k in ('PTS','REB','AST')) if code=='PRA' else r[code]
            vals=[stat(r) for r in recent]
            if all(v is not None for v in vals):
                result[(player,code)]={'estimate':round(sum(vals)/len(vals),2),
                                      'games':len(recent),'latest_game_date':recent[0]['date']}
    return result

def provisional_projections(logs,model,min_games=3):
    """Experimental empirical-Bayes projections; never eligible for qualifying picks.

    Shrinks a player's recent per-game production toward the across-player
    current-season baseline. No minutes or injury adjustments are available.
    """
    existing={name for name,market in model}
    codes=('PTS','REB','AST','3PM')
    current={name:[r for r in rows if r['season']=='current'] for name,rows in logs.items()}
    eligible={name:rows for name,rows in current.items() if len(rows)>=min_games}
    # Equal weight per player so stars with more games do not dominate the prior.
    priors={code:sum(sum(r[code] for r in rows)/len(rows) for rows in eligible.values())/len(eligible)
            for code in codes} if eligible else {}
    result={}
    for player,rows in eligible.items():
        if player in existing:continue
        recent=rows[:10]
        # A fixed prior strength makes short samples explicitly conservative.
        shrink_games=5
        components={}
        for code in codes:
            sample=sum(r[code] for r in recent)/len(recent)
            prior=priors[code]
            components[code]=max(0,(len(recent)*sample+shrink_games*prior)/(len(recent)+shrink_games)) if code=='3PM' else max(0,sample)
        components['PRA']=sum(components[k] for k in ('PTS','REB','AST'))
        for code,val in components.items():
            result[(player,code)]={'projection':round(val,2),'games':len(recent),
                                    'latest_game_date':recent[0]['date'],
                                    'method':'experimental recent-form baseline (PTS/REB/AST); shrunk 3PM',
                                    'status':'PROVISIONAL_NOT_VALIDATED'}
    return result

def add_game_analysis(signal,logs):
    rows=logs.get(canonical_player(signal.get('player')),[])
    code=signal['market'];line=signal['line'];side=signal['side']
    def val(row):
        return sum(row[k] for k in ('PTS','REB','AST')) if code=='PRA' else row.get(code)
    def summarize(items):
        values=[val(r) for r in items if val(r) is not None]
        if not values:return {'games':0,'average':None,'hits':None,'hit_rate':None,'avg_minutes':None}
        hits=sum((v>line if side=='OVER' else v<line) for v in values)
        mins=[r['MIN'] for r in items if r['MIN'] is not None]
        return {'games':len(values),'average':round(sum(values)/len(values),2),
                'hits':hits,'hit_rate':round(100*hits/len(values),1),
                'avg_minutes':round(sum(mins)/len(mins),1) if mins else None}
    current=[r for r in rows if r['season']=='current']
    historical=[r for r in rows if r['season']=='historical']
    # Keep current-season and previous-season samples separate and explicit.
    signal['analysis']={
        'current':summarize(current),
        'previous_season':summarize(historical),
        'last5':summarize(rows[:5]),'last10':summarize(rows[:10]),'last20':summarize(rows[:20]),
        'recent_games':[{'date':r['date'],'season':r['season'],'stat':val(r),'minutes':r['MIN']}
                        for r in rows[:10]],
        'sample_warning':('No regular-season current-year game logs available.' if not current else
                          'Only '+str(len(current))+' current-season regular-season games available.' if len(current)<5 else None),
        'minutes_warning':'Current-season source does not include minutes; displayed minute averages use historical records only.',
        'note':'Historical hit rates are descriptive and not estimated probabilities or EV.'
    }
    return signal

def refresh():
    now=datetime.now(timezone.utc).isoformat()
    base={"signals":[],"live":False,"verified":False,"as_of":now,"players":0,"quotes":0,"error":None,"event_diagnostics":[],"total_signals":0,"game_props":[],"game_prop_rows":0,"game_coverage":[],"data_freshness":projection_freshness()}
    key=os.getenv("PROPLINE_API_KEY","").strip()
    if not key:
        base["error"]="PROPLINE_API_KEY not configured in Render environment. No live picks shown."
        return base
    try:
        from propline import PropLine
        client=PropLine(key)
        try:
            events=client.get_events("basketball_nba")
        except Exception as exc:
            if is_rate_limited(exc):raise ProviderRateLimited() from exc
            raise
        model=projections();base["players"]=len({p for p,m in model})
        exploratory=early_sample_estimates(load_game_logs(),model)
        provisional=provisional_projections(load_game_logs(),model)
        candidates=defaultdict(list)
        raw_props=defaultdict(lambda: {"books":set(),"prices":{},"model":None,"raw_names":set()})
        upcoming=[]
        # Check every upcoming event in a bounded 36-hour window, not just
        # the first eight. The window prevents a provider's long-term event
        # archive from consuming API quota on every refresh.
        cutoff=datetime.now(timezone.utc)+timedelta(hours=36)
        skipped_outside_window=0
        for event in events:
            eid=event.get("id")
            if not eid:continue
            # Avoid completed and in-progress events; use provider status where available.
            status=norm(event.get("status"))
            if status in {"final","completed","live","in_progress","in progress","closed"}:continue
            commence=event.get("commence_time") or event.get("start_time")
            if commence:
                try:
                    dt=datetime.fromisoformat(str(commence).replace("Z","+00:00"))
                    if dt.tzinfo:
                        if dt<=datetime.now(timezone.utc):continue
                        if dt>cutoff:
                            skipped_outside_window+=1
                            continue
                except ValueError:continue
            upcoming.append(event)
        upcoming=sorted(upcoming,key=lambda e: str(e.get("commence_time") or e.get("start_time") or "9999"))
        base["events_available_in_window"]=len(upcoming)
        base["events_skipped_outside_window"]=skipped_outside_window
        # Configurable safety ceiling, higher than a full NBA slate. Expose
        # truncation explicitly so unchecked games aren't mislabeled.
        max_events=max(8,min(60,int(os.getenv("PROPEDGE_MAX_ODDS_EVENTS","30"))))
        base["events_skipped_by_cap"]=max(0,len(upcoming)-max_events)
        upcoming=upcoming[:max_events]
        event_stats={str(e["id"]):{"event_id":str(e["id"]),"game":f'{e.get("away_team", "Away")} @ {e.get("home_team", "Home")}',"status":"pending","matched_model_quotes":0,"candidate_groups":0,"qualified_signals":0} for e in upcoming}
        base["events_checked"]=len(upcoming)
        base["odds_fetch_mode"]="per_event_player_props"
        base["refresh_interval_seconds"]=600
        def fetch(event):
            # Retry transient provider failures with bounded backoff and jitter.
            last_error=None
            for attempt in range(4):
                try:
                    sdk=PropLine(key)
                    response=sdk.get_odds("basketball_nba",event_id=event["id"],markets=list(MARKETS))
                    if not isinstance(response,dict) or not isinstance(response.get("bookmakers"),list):
                        raise ValueError("Malformed odds response")
                    return response
                except Exception as exc:
                    if is_rate_limited(exc):raise ProviderRateLimited() from exc
                    last_error=exc
                    if attempt<3:time.sleep(min(9,2**attempt)+(hash(str(event["id"]))%4)*0.25)
            raise last_error
        successful=0
        failures=[]
        failed_event_ids=[]
        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks={pool.submit(fetch,event):event for event in upcoming}
            for task in as_completed(tasks):
                event=tasks[task]
                eid=event["id"]
                try:
                    odds=task.result()
                    successful+=1
                    event_stats[str(eid)]["status"]="checked"
                except Exception as exc:
                    if isinstance(exc,ProviderRateLimited):
                        failures.append("RateLimitError")
                        _rate_limit["until"]=max(_rate_limit["until"],time.monotonic()+900)
                    else:failures.append(type(exc).__name__)
                    failed_event_ids.append(eid)
                    event_stats[str(eid)]["status"]="rate_limited" if isinstance(exc,ProviderRateLimited) else "odds_request_failed"
                    logging.warning("Live odds failed for event %s: %s",eid,type(exc).__name__)
                    continue
                game=f'{event.get("away_team", "Away")} @ {event.get("home_team", "Home")}'
                for book in odds.get("bookmakers",[]):
                    bk=norm(book.get("key"))
                    if not bk or bk in {"prizepicks","underdog","pick6"}:continue
                    for market in book.get("markets",[]):
                        code=MARKETS.get(market.get("key"))
                        if not code or market.get("line_type")!="main":continue
                        period=norm(market.get("period"))
                        if period not in {"","full","full_game","game"}:continue
                        for o in market.get("outcomes",[]):
                            if norm(o.get("name")) not in {"over","under"}:continue
                            line=number(o.get("point")); player=canonical_player(o.get("description"))
                            if line is None or line<0.5 or not player:continue
                            side=norm(o.get("name"))
                            # Keep an independent, bounded browse-only inventory. These
                            # lines are NOT qualifying signals and do not affect Top Picks.
                            inventory_key=(str(eid),player,code,side,line)
                            inv=raw_props[inventory_key]
                            inv["books"].add(bk)
                            inv["raw_names"].add(str(o.get("description") or "").strip())
                            inv["prices"][bk]=o.get("price")
                            inv["game"]=game
                            inv["player_name"]= "Ace Bailey" if player=="ace bailey" else re.sub(r"\s*\([A-Z]{2,3}\)$", "", str(o.get("description") or "").strip())
                            inv["event_id"]=str(eid)
                            m=model.get((player,code))
                            if m is None:continue
                            proj=number(m.get("ShadowProjection"))
                            if proj is None:continue
                            side=norm(o.get("name"))
                            gap=proj-line if side=="over" else line-proj
                            if gap<=0:continue
                            candidates[(eid,player,code,side,line)].append((bk,o,game,m,proj,gap))
                            base["quotes"]+=1
                            event_stats[str(eid)]["matched_model_quotes"]+=1
        base["events_succeeded"]=successful
        base["events_failed"]=len(failures)
        base["failure_types"]=sorted(set(failures))
        base["failed_event_ids"]=failed_event_ids
        base["coverage"]="partial" if failures or base["events_skipped_by_cap"] else "complete"
        if upcoming and successful==0:
            base["error"]="All live odds requests failed: "+", ".join(sorted(set(failures)))
            return base
        if not upcoming:
            base["error"]="No upcoming NBA games returned by the events feed."
            return base
        signals=[]
        for (eid,player,code,side,line),quotes in candidates.items():
            event_stats[str(eid)]["candidate_groups"]+=1
            distinct={q[0] for q in quotes}
            # Two independent books must quote the exact same standard main line.
            # DFS platforms are not counted as independent sportsbook confirmations.
            sportsbooks=distinct-{"prizepicks","underdog","pick6"}
            if len(sportsbooks)<2:continue
            # Require a minimum absolute gap; do not call this EV/probability.
            q=next((q for q in quotes if q[0] in sportsbooks),quotes[0])
            bk,o,game,m,proj,gap=q
            if gap<max(0.5,0.08*max(line,1)):continue
            event_stats[str(eid)]["qualified_signals"]+=1
            signals.append({"player":re.sub(r"\s*\([A-Z]{2,3}\)$", "", str(m.get("ResolvedPlayer") or "").strip()),"market":code,"side":side.upper(),"line":line,"projection":round(proj,2),"gap":round(gap,2),"book":bk,"books":sorted(distinct),"book_count":len(sportsbooks),"game":game,"odds":o.get("price"),"history":m.get("HistoricalGames"),"current":m.get("CurrentGames"),"minutes":m.get("ProjectedMinutes"),"source":"live main-line feed","event_id":eid,"verified":True,"coverage":"partial" if failures else "complete"})
        # Availability guardrail: a reported OUT player is not an actionable pick.
        # Questionable/doubtful/probable statuses remain visible with warnings.
        # Never adjust statistical projections from an uncalibrated injury assumption.
        try:
            availability=availability_context()
            report=availability.get("injuries",[]) if availability.get("injury_feed_status")=="available" else []
        except Exception:
            availability={"injury_feed_status":"unavailable","lineup_feed_status":"not_connected"}
            report=[]
        injury_by_player=defaultdict(list)
        for injury in report:
            injury_by_player[canonical_player(injury.get("player"))].append(injury)
        def injury_for(player,game):
            entries=injury_by_player.get(canonical_player(player),[])
            teams=str(game or "").split(" @ ")
            if len(teams)!=2:return None
            # Use exact canonical player names only, plus game team validation
            # to avoid applying a same-name injury from a different team.
            return next((i for i in entries if norm(i.get("team")) in {norm(teams[0]),norm(teams[1])}),None)
        # Experimental, non-qualifying teammate on/off scenario.
        logs_for_injuries=load_game_logs()
        def injury_scenario(player,game,market,baseline):
            if baseline is None:return None
            teams=str(game or "").split(" @ ")
            if len(teams)!=2:return None
            rows=logs_for_injuries.get(canonical_player(player),[])
            if not rows:return None
            team=norm(rows[0].get("team"))
            own=[r for r in rows if norm(r.get("team"))==team and r.get("game_id")]
            if len(own)<10:return None
            if market not in ("PTS","REB","AST","3PM","PRA"):return None
            def value(r):
                return sum(r[k] for k in ("PTS","REB","AST")) if market=="PRA" else r[market]
            comparisons=[]
            for injured in report:
                if norm(injured.get("team")) not in {norm(teams[0]),norm(teams[1])}:continue
                if norm(injured.get("status"))!="out":continue
                mate=canonical_player(injured.get("player"))
                if mate==canonical_player(player):continue
                mate_rows=[r for r in logs_for_injuries.get(mate,[]) if norm(r.get("team"))==team and r.get("game_id")]
                if len(mate_rows)<5:continue
                active={r["game_id"] for r in mate_rows if r.get("MIN") is None or r["MIN"]>0}
                with_mate=[value(r) for r in own if r["game_id"] in active]
                without_mate=[value(r) for r in own if r["game_id"] not in active]
                if len(with_mate)<5 or len(without_mate)<5:continue
                on=sum(with_mate)/len(with_mate)
                off=sum(without_mate)/len(without_mate)
                if on<=0.5:continue
                comparisons.append((min(len(with_mate),len(without_mate)),injured.get("player"),len(with_mate),len(without_mate),off/on-1))
            if not comparisons:return None
            _,mate,on_games,off_games,observed=max(comparisons,key=lambda x:x[0])
            # Separate minutes and production-rate effects when minutes exist.
            # Current-season game logs may lack minutes; never fabricate them.
            mate_key=canonical_player(mate)
            mate_ids={r["game_id"] for r in logs_for_injuries.get(mate_key,[])
                      if norm(r.get("team"))==team and r.get("game_id")}
            with_rows=[r for r in own if r["game_id"] in mate_ids and number(r.get("MIN")) is not None and r["MIN"]>0]
            without_rows=[r for r in own if r["game_id"] not in mate_ids and number(r.get("MIN")) is not None and r["MIN"]>0]
            minute_context=None
            if len(with_rows)>=5 and len(without_rows)>=5:
                with_minutes=sum(r["MIN"] for r in with_rows)/len(with_rows)
                without_minutes=sum(r["MIN"] for r in without_rows)/len(without_rows)
                if with_minutes>0:
                    minute_context={"with_teammate":round(with_minutes,1),
                                    "without_teammate":round(without_minutes,1),
                                    "minute_change_pct":round(100*(without_minutes/with_minutes-1),1),
                                    "with_games":len(with_rows),"without_games":len(without_rows)}
            change=max(-0.15,min(0.15,observed*0.35))
            return {"projection":round(max(0,baseline*(1+change)),2),
                    "baseline":round(baseline,2),"adjustment_pct":round(change*100,1),
                    "teammate":mate,"with_games":on_games,"without_games":off_games,
                    "minutes_context":minute_context,"injury_history_verified":False,
                    "method":"experimental teammate on/off; shrunk and capped",
                    "validated":False,"qualifying_eligible":False}
        def attach_availability(row):
            report_row=injury_for(row.get("player"),row.get("game"))
            status=str(report_row.get("status") or "") if report_row else ""
            row["injury_status"]=status or None
            row["injury_source"]="ESPN" if report_row else None
            row["lineup_status"]="not_verified"
            row["availability_checked_at"]=availability.get("checked_at")
            row["availability_warning"]=("Reported out; excluded from qualifying picks" if status.casefold()=="out" else
                                         "Reported "+status+"; availability requires confirmation" if status else
                                         "No injury entry returned; availability not independently verified")
            row['injury_scenario']=injury_scenario(row.get('player'),row.get('game'),row.get('market'),number(row.get('projection'))) if status.casefold()!='out' else None
            return row
        for signal in signals:attach_availability(signal)
        excluded=[r for r in signals if (r.get("injury_status") or "").casefold()=="out"]
        signals=[r for r in signals if (r.get("injury_status") or "").casefold()!="out"]
        base["injury_excluded_signals"]=len(excluded)
        base["injury_feed_status"]=availability.get("injury_feed_status")
        base["lineup_feed_status"]="not_connected"
        # Browse-only rows: one per event/player/market/side/line, including
        # unmatched players and single-book quotes. Never promote to Top Picks.
        qualified_keys={(str(x["event_id"]),canonical_player(x["player"]),x["market"],norm(x["side"]),x["line"]) for x in signals}
        browse=[]
        for (eid,player,code,side,line),inv in raw_props.items():
            m=model.get((player,code))
            proj=number(m.get("ShadowProjection")) if m else None
            gap=(proj-line if side=="over" else line-proj) if proj is not None else None
            sample=exploratory.get((player,code)) if proj is None else None
            provisional_row=provisional.get((player,code)) if proj is None else None
            books=sorted(inv["books"])
            browse.append({"event_id":eid,"game":inv["game"],"player":re.sub(r"\s*\([A-Z]{2,3}\)$", "", str(m.get("ResolvedPlayer") or inv["player_name"]).strip()) if m else ("De'Aaron Fox" if player=="deaaron fox" else inv["player_name"]),
                           "player_key":player,
                           "market":code,"side":side.upper(),"line":line,"projection":round(proj,2) if proj is not None else None,
                           "gap":round(gap,2) if gap is not None else None,"book":books[0] if books else "",
                           "books":books,"book_count":len(books),"source_names":sorted(inv["raw_names"]),"qualified":(eid,player,code,side,line) in qualified_keys,
                           "model_status":"matched" if proj is not None else "early_sample" if sample else "unmatched",
                           "early_sample_estimate":sample["estimate"] if sample else None,
                           "early_sample_games":sample["games"] if sample else None,
                           "provisional_projection":provisional_row["projection"] if provisional_row else None,
                           "provisional_model":provisional_row["method"] if provisional_row else None,
                           "provisional_games":provisional_row["games"] if provisional_row else None,
                           "qualification_reason":("Qualifies" if (eid,player,code,side,line) in qualified_keys else
                               "Experimental projection only; not validated or eligible for qualifying picks" if provisional_row else
                               "Early-season average only; not a validated model or qualifying pick" if sample else
                               "No matching eligible model projection" if proj is None else
                               "Model favors the opposite direction" if gap is not None and gap<=0 else
                               "Fewer than 2 independent sportsbooks on this exact line" if len(books)<2 else
                               "Model edge below 8% of line or 0.5 minimum" if gap is not None and gap<max(0.5,0.08*max(line,1)) else
                               "Not qualified; check source validation"),
                           "required_edge":round(max(0.5,0.08*max(line,1)),2)})
        for row in browse:
            attach_availability(row)
            if (row.get("injury_status") or "").casefold()=="out":
                row["qualified"]=False
                row["qualification_reason"]="Reported OUT on injury feed; excluded from qualifying picks"
        browse.sort(key=lambda r:(r["game"],r["player"],r["market"],r["line"],r["side"]))
        # Fair event distribution: the old global 5,000-row slice could hide
        # later games entirely when earlier games had many alternate lines.
        browse_by_event=defaultdict(list)
        for row in browse:browse_by_event[row["event_id"]].append(row)
        cap=5000; selected=[]
        event_ids=sorted(browse_by_event)
        while len(selected)<cap and event_ids:
            remaining=[]
            for event_id in event_ids:
                if len(selected)>=cap:break
                if browse_by_event[event_id]:
                    selected.append(browse_by_event[event_id].pop(0))
                if browse_by_event[event_id]:remaining.append(event_id)
            event_ids=remaining
        base["game_props"]=selected
        base["game_prop_rows"]=len(browse)
        base["game_props_truncated"]=len(browse)>cap
        event_coverage=defaultdict(lambda:{"players":set(),"books":set(),"rows":0,"unmatched_players":set(),"aliases":defaultdict(set)})
        for row in browse:
            cov=event_coverage[row["event_id"]]
            cov["players"].add(canonical_player(row["player"]))
            cov["books"].update(row["books"])
            cov["rows"]+=1
            if row["model_status"]=="unmatched":cov["unmatched_players"].add(canonical_player(row["player"]))
            for name in row["source_names"]:cov["aliases"][canonical_player(row["player"])].add(name)
        base["game_coverage"]=[{"event_id":eid,"game":stat["game"],"status":stat["status"],
            "players_with_lines":len(event_coverage[eid]["players"]),"captured_directions":event_coverage[eid]["rows"],
            "sportsbooks":sorted(event_coverage[eid]["books"]),"sportsbook_count":len(event_coverage[eid]["books"]),
            "players_without_model":len(event_coverage[eid]["unmatched_players"]),
            "odds_request_succeeded":stat["status"]=="checked",
            "merged_aliases":{p:sorted(names) for p,names in event_coverage[eid]["aliases"].items() if len(names)>1}}
            for eid,stat in event_stats.items()]
        logs=load_game_logs()
        # Descriptive game-log stats for browse-only lines, including nonqualifying props.
        # Compute once per player/market/side/line; preserve current projection logic.
        analysis_cache={}
        for row in base["game_props"]:
            k=(canonical_player(row["player"]),row["market"],row["side"],row["line"])
            if k not in analysis_cache:
                temp={"player":row["player"],"market":row["market"],"side":row["side"],"line":row["line"]}
                add_game_analysis(temp,logs)
                analysis_cache[k]=temp["analysis"]
            row["analysis"]=analysis_cache[k]
        for signal in signals:add_game_analysis(signal,logs)
        signals.sort(key=lambda s:(s["book_count"],s["gap"]/max(s["line"],1)),reverse=True)
        base.update(signals=signals, total_signals=len(signals), event_diagnostics=list(event_stats.values()), live=True,verified=(len(failures)==0 and base["events_skipped_by_cap"]==0))
        if failures or base["events_skipped_by_cap"]:
            base["error"]=(f"Partial live feed: {successful} of {len(upcoming)} games succeeded; "
                           f"{base['events_skipped_by_cap']} events excluded by safety cap. "
                           "Signals from successful games only; coverage is incomplete.")
    except Exception as exc:
        if isinstance(exc,ProviderRateLimited):
            _rate_limit["until"]=max(_rate_limit["until"],time.monotonic()+900)
        # Fail closed: never substitute stale snapshots for live picks.
        base["error"]=f"Live odds unavailable ({type(exc).__name__}). No picks shown. Check Render logs and API limits."
    return base

@app.get("/api/provisional-backtest")
def provisional_backtest(player: str = ""):
    from provisional_backtest import evaluate
    name=player.strip()[:120] or None
    return evaluate(ROOT/"data"/"nba_2026_27.csv",player=name)

@app.get("/api/missing-model-coverage")
def missing_model_coverage(limit: int = 100):
    # Diagnostic only. Never invent projections or promote game averages to model picks.
    from fastapi import HTTPException
    if not 1 <= limit <= 500:
        raise HTTPException(status_code=400,detail="limit must be between 1 and 500")
    model=projections()
    logs=load_game_logs()
    modeled={name for name,code in model}
    current_players={name for name,games in logs.items() if any(g["season"]=="current" for g in games)}
    missing=[]
    for name in sorted(current_players-modeled):
        games=[g for g in logs[name] if g["season"]=="current"]
        missing.append({"player":name,"current_season_games":len(games),
                        "latest_game_date":max(g["date"] for g in games),
                        "reason":"No MODELED projection for any market"})
    missing.sort(key=lambda r:(-r["current_season_games"],r["player"]))
    with _lock:
        cached=_cache.get("result") or {}
        captured={canonical_player(r.get("player")) for r in cached.get("game_props",[])}
        as_of=cached.get("as_of")
    for row in missing:
        row["captured_in_cached_odds"]=row["player"] in captured
    return {"modeled_players":len(modeled),"current_season_players":len(current_players),
            "missing_model_players":len(missing),"missing":missing[:limit],
            "odds_cache_as_of":as_of,
            "note":"Read-only coverage audit. Current-season logs alone do not establish valid model projections; players are not added to picks until the projection pipeline produces validated MODELED rows."}

@app.get("/api/model-coverage")
def model_coverage(player: str = ""):
    # Read-only diagnostics: no odds requests, model changes, or fabricated lines.
    requested=canonical_player(player)
    if not requested or len(requested)>100:
        from fastapi import HTTPException
        raise HTTPException(status_code=400,detail="Provide a player name (up to 100 characters)")
    model=projections()
    markets={code:round(number(row.get("ShadowProjection")),2)
             for (name,code),row in model.items() if name==requested
             and number(row.get("ShadowProjection")) is not None}
    logs=load_game_logs().get(requested,[])
    with _lock:
        cached=_cache.get("result") or {}
        browse=[r for r in cached.get("game_props",[]) if canonical_player(r.get("player"))==requested]
        refreshed=cached.get("as_of") if cached else None
    return {"player":player.strip(),"canonical_player":requested,
            "modeled_markets":markets,"modeled_market_count":len(markets),
            "current_season_game_logs":sum(r["season"]=="current" for r in logs),
            "captured_markets":sorted(set(r["market"] for r in browse)),
            "captured_lines":len(browse),"odds_cache_as_of":refreshed,
            "note":"Captured lines are from the existing board cache only; no live odds refresh is triggered. Missing captured lines do not imply missing projections."}

@app.get("/api/research-backtests")
def research_backtests():
    reports={}
    for name,file in (("injury","injury_backtest_results.json"),
                      ("minutes_usage","minutes_usage_backtest_results.json")):
        try:
            path=ROOT/"data"/file
            reports[name]=json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        except (OSError,ValueError):
            reports[name]=None
    return {"reports":reports,"production_model_affected":False,
            "note":"Retrospective research uses absence proxies and trailing averages, not saved production model predictions."}

@app.get("/api/model-performance")
def model_performance():
    path=ROOT/"data"/"model_tracking.json"
    try:rows=json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except (ValueError,OSError):rows=[]
    completed=[r for r in rows if r.get("status") in ("win","loss","push")]
    wins=sum(r["status"]=="win" for r in completed)
    losses=sum(r["status"]=="loss" for r in completed)
    pushes=sum(r["status"]=="push" for r in completed)
    errors=[abs(float(r["projection"])-float(r["actual"])) for r in completed if number(r.get("projection")) is not None and number(r.get("actual")) is not None]
    by_market={}
    for market in ("PTS","REB","AST","3PM","PRA"):
        subset=[r for r in completed if r.get("market")==market]
        w=sum(r["status"]=="win" for r in subset);l=sum(r["status"]=="loss" for r in subset)
        p=sum(r["status"]=="push" for r in subset)
        valid=[r for r in subset if number(r.get("projection")) is not None and number(r.get("actual")) is not None]
        mae=round(sum(abs(float(r["projection"])-float(r["actual"])) for r in valid)/len(valid),3) if valid else None
        # Flat 1-unit bets at the saved pregame American price; pushes refund stake.
        priced=[r for r in subset if number(r.get("odds")) is not None and abs(float(r["odds"]))>=100]
        profit=sum((float(r["odds"])/100 if float(r["odds"])>0 else 100/abs(float(r["odds"]))) if r["status"]=="win" else (-1 if r["status"]=="loss" else 0) for r in priced)
        by_market[market]={"captured":sum(r.get("market")==market for r in rows),
            "graded":len(subset),"pending":sum(r.get("market")==market and r.get("status")=="pending" for r in rows),
            "wins":w,"losses":l,"pushes":p,"hit_rate":round(100*w/(w+l),1) if w+l else None,
            "mean_absolute_error":mae,"priced_graded":len(priced),
            "flat_profit_units":round(profit,3) if priced else None,
            "flat_roi_pct":round(100*profit/len(priced),1) if priced else None}
    # Distinct, date-scoped snapshots of the actual captured pregame picks.
    # Do not conflate this with historical experimental simulations.
    windows={}
    now=datetime.now(timezone.utc)
    for days in (7,30):
        recent=[]
        for r in rows:
            try:
                stamp=datetime.fromisoformat(str(r.get("captured_at","")).replace("Z","+00:00"))
                if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
                if now-timedelta(days=days)<=stamp<=now:recent.append(r)
            except (ValueError,TypeError):continue
        graded=[r for r in recent if r.get("status") in ("win","loss","push")]
        w=sum(r.get("status")=="win" for r in graded)
        l=sum(r.get("status")=="loss" for r in graded)
        windows[str(days)]={"captured":len(recent),"graded":len(graded),
                            "pending":sum(r.get("status")=="pending" for r in recent),
                            "wins":w,"losses":l,"pushes":sum(r.get("status")=="push" for r in graded),
                            "hit_rate":round(100*w/(w+l),1) if w+l else None}
    return {"total_captured":len(rows),"pending":sum(r.get("status")=="pending" for r in rows),
            "graded":len(completed),"wins":wins,"losses":losses,"pushes":pushes,
            "hit_rate":round(100*wins/(wins+losses),1) if wins+losses else None,
            "mean_absolute_error":round(sum(errors)/len(errors),2) if errors else None,
            "by_market":by_market,"market_ranking_ready":sum(r.get("status") in ("win","loss") for r in rows)>=100,"windows":windows,"recent":list(reversed(rows[-60:])),
            "note":"Tracked pregame signals only. Grading requires completed ESPN games and matching player game logs. Pending is not a loss; no results are backfilled."}

# Supplemental injury/lineup research. Never infer a confirmed starter or silently
# change projections based on a feed that may be missing or stale.
_injury_cache={"until":0,"data":None}
@app.get("/api/availability")
def availability_context():
    import urllib.request
    now=time.time()
    if _injury_cache["data"] is not None and now<_injury_cache["until"]:
        return _injury_cache["data"]
    result={"source":"ESPN NBA injury report","checked_at":datetime.now(timezone.utc).isoformat(),
            "injuries":[],"injury_feed_status":"unavailable",
            "lineups":[],"lineup_feed_status":"not_connected",
            "note":"Injury designations are informational and may change. Starting lineups are not confirmed by this feed. Model projections and qualification rules are unchanged."}
    try:
        request=urllib.request.Request("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries",headers={"User-Agent":"PropEdge/1.0"})
        with urllib.request.urlopen(request,timeout=8) as response:
            payload=json.load(response)
        teams=payload.get("injuries") if isinstance(payload,dict) else None
        if isinstance(teams,list):
            entries=[]
            for team in teams:
                if not isinstance(team,dict):continue
                teamname=(team.get("team") or {}).get("displayName") if isinstance(team.get("team"),dict) else str(team.get("displayName") or "")
                for item in (team.get("injuries") or []):
                    if not isinstance(item,dict):continue
                    athlete=item.get("athlete") or {}
                    if not isinstance(athlete,dict):athlete={}
                    player=athlete.get("displayName") or item.get("name")
                    if not player:continue
                    entries.append({"player":str(player),"team":str(teamname or ""),"status":str(item.get("status") or "Not specified"),
                                    "detail":str((item.get("details") or {}).get("detail") or "") if isinstance(item.get("details"),dict) else "",
                                    "source":"ESPN"})
            result["injuries"]=entries
            result["injury_feed_status"]="available"
    except Exception:
        pass
    _injury_cache.update(until=now+600,data=result)
    return result

@app.get("/api/board")
def board():
    with _lock:
        now=time.monotonic()
        if now<_rate_limit["until"]:
            if _cache["result"] is None or not _cache["result"].get("error"):
                _cache["result"]={"signals":[],"live":False,"verified":False,"error":"Live odds provider rate limited; waiting for cooldown. No picks shown.","coverage":"unavailable"}
            _cache["result"]["retry_after_seconds"]=int(_rate_limit["until"]-now)
            return _cache["result"]
        if now>=_cache["until"]:
            _cache["result"]=refresh()
            limited=_rate_limit["until"]>time.monotonic()
            # Pro-capacity optimization: one server-wide full-slate refresh at most
            # every 10 minutes, shared by every visitor and the tracking worker.
            # On errors, avoid rapid polling that burns the daily request budget.
            _cache["until"]=time.monotonic()+(900 if limited else (300 if _cache["result"].get("error") else 600))
        return _cache["result"]


@app.get("/nfl")
def nfl_home():return HTMLResponse((ROOT/"static"/"nfl.html").read_text(encoding="utf-8"))

@app.get("/healthz")
def health():return {"status":"ok"}

@app.get("/")
def home():return HTMLResponse((ROOT/"static"/"index.html").read_text(encoding="utf-8"))


# Optional player portraits from ESPN NBA team rosters. Failure never affects live picks.
_headshot_cache={"until":0,"headshots":{}}
@app.get("/api/headshots")
def headshots():
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor
    now=time.monotonic()
    if now<_headshot_cache["until"]:return {"headshots":_headshot_cache["headshots"]}
    team_ids=["atl","bos","bkn","cha","chi","cle","dal","den","det","gs","hou","ind","lac","lal","mem","mia","mil","min","no","ny","okc","orl","phi","phx","por","sac","sa","tor","utah","wsh"]
    def get_roster(team):
        url=f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team}/roster"
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"PropEdge/2.0"})
            with urllib.request.urlopen(req,timeout=3) as res: payload=json.load(res)
            athletes=payload.get("athletes",[])
            if isinstance(athletes,dict):athletes=list(athletes.values())
            output={}
            for a in athletes:
                if isinstance(a,dict) and "items" in a: entries=a["items"]
                elif isinstance(a,list):entries=a
                else:entries=[a]
                for p in entries:
                    if not isinstance(p,dict):continue
                    name=p.get("displayName") or p.get("fullName")
                    photo=(p.get("headshot") or {}).get("href")
                    if name and photo and photo.startswith("https://"):output[name.casefold()]=photo
            return output
        except Exception:return {}
    result={}
    with ThreadPoolExecutor(max_workers=12) as pool:
        for chunk in pool.map(get_roster,team_ids):result.update(chunk)
    _headshot_cache.update(until=time.monotonic()+21600,headshots=result)
    return {"headshots":result}


# Complete NBA scoreboard independent of the qualifying prop board.
_schedule_cache={"until":0,"date":None,"result":None}
@app.get("/api/games")
def nba_games(date: str = ""):
    import re
    from fastapi import HTTPException
    if date and not re.fullmatch(r"\d{8}",date):
        raise HTTPException(status_code=400,detail="Date must be YYYYMMDD")
    from zoneinfo import ZoneInfo
    from urllib.request import Request,urlopen
    from urllib.parse import urlencode
    today=datetime.now(ZoneInfo("America/Chicago")).strftime("%Y%m%d")
    requested=date or today
    from datetime import datetime as _dt
    if date:
        try:
            requested_day=_dt.strptime(date,"%Y%m%d").date()
            local_day=_dt.strptime(today,"%Y%m%d").date()
            if abs((requested_day-local_day).days)>7:raise ValueError("out of range")
        except ValueError:raise HTTPException(status_code=400,detail="Date outside supported seven-day range")
    now=time.monotonic()
    if _schedule_cache["date"]==requested and now<_schedule_cache["until"]:
        return _schedule_cache["result"]
    url="https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?"+urlencode({"dates":requested,"limit":100})
    try:
        with urlopen(Request(url,headers={"User-Agent":"PropEdge/2.2"}),timeout=8) as response:
            payload=json.load(response)
        games=[]
        for event in payload.get("events",[]):
            comp=(event.get("competitions") or [{}])[0]
            teams=comp.get("competitors") or []
            away=next((x for x in teams if x.get("homeAway")=="away"),None)
            home=next((x for x in teams if x.get("homeAway")=="home"),None)
            if not away or not home:continue
            def team(t):
                x=t.get("team") or {}
                return {"name":x.get("displayName") or x.get("name") or "Unknown", "abbreviation":x.get("abbreviation") or "", "logo":x.get("logo") or (x.get("logos") or [{}])[0].get("href") or "", "score":t.get("score"),"record":next((v.get("summary") for v in (t.get("records") or []) if v.get("type")=="total"),None) or next((v.get("summary") for v in (t.get("records") or []) if v.get("summary")),None)}
            # Scoreboard odds are optional and may be absent or stale.
            raw_odds=(comp.get("odds") or [])
            quote=next((o for o in raw_odds if isinstance(o,dict) and not o.get("closed")),None)
            if quote is None:quote=next((o for o in raw_odds if isinstance(o,dict)),None)
            quote=quote or {}
            def moneyline(side):
                obj=quote.get(side+"TeamOdds") or {}
                return obj.get("moneyLine") or obj.get("moneyline")
            odds={"provider":(quote.get("provider") or {}).get("name") if isinstance(quote.get("provider"),dict) else None,
                  "spread":quote.get("details"),"total":quote.get("overUnder"),
                  "away_moneyline":moneyline("away"),"home_moneyline":moneyline("home"),
                  "available":bool(quote)}
            games.append({"id":event.get("id"),"date":event.get("date"),"away":team(away),"home":team(home),"odds":odds,"status":((event.get("status") or {}).get("type") or {}).get("shortDetail") or "Scheduled", "state":((event.get("status") or {}).get("type") or {}).get("state") or "pre"})
        result={"date":requested,"games":games,"source":"ESPN NBA scoreboard","as_of":datetime.now(timezone.utc).isoformat()}
        _schedule_cache.update({"until":now+180,"date":requested,"result":result})
        return result
    except Exception as exc:
        # Never claim an incomplete model-signal list is the complete slate.
        return {"date":requested,"games":[],"error":"NBA schedule temporarily unavailable ("+type(exc).__name__+")","source":"ESPN NBA scoreboard"}
