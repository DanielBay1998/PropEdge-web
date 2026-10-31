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
    opponents=defaultdict(set)
    for rows in logs.values():
        for r in rows:
            if r['game_id'] and r['team']:opponents[(r['season'],r['game_id'],r['date'])].add(r['team'])
    for rows in logs.values():
        for r in rows:
            other=opponents.get((r['season'],r['game_id'],r['date']),set())-{r['team']}
            r['opponent']=next(iter(other)) if len(other)==1 else None
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
        'last5':summarize(current[:5]),'last10':summarize(current[:10]),'last20':summarize(current[:20]),
        'recent_games':[{'date':r['date'],'season':r['season'],'stat':val(r),'minutes':r['MIN']}
                        for r in current[:20]],
        'sample_warning':('No regular-season current-year game logs available.' if not current else
                          'Only '+str(len(current))+' current-season regular-season games available.' if len(current)<5 else None),
        'minutes_warning':'Current-season source does not include minutes; displayed minute averages use historical records only.',
        'note':'Historical hit rates are descriptive and not estimated probabilities or EV.'
    }
    return signal

def verified_nba_schedule(days=3):
    """Authoritative ESPN scoreboard fixtures; fail closed if unavailable."""
    import urllib.request
    now=datetime.now(timezone.utc)
    fixtures=set()
    checked=0
    for offset in range(-1,days):
        day=(now+timedelta(days=offset)).strftime("%Y%m%d")
        url="https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates="+day
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"PropEdge-Schedule-Verification/1.0"})
            with urllib.request.urlopen(req,timeout=6) as resp:data=json.load(resp)
            checked+=1
            for event in data.get("events",[]):
                comp=(event.get("competitions") or [{}])[0]
                teams=comp.get("competitors") or []
                away=next((x for x in teams if x.get("homeAway")=="away"),{})
                home=next((x for x in teams if x.get("homeAway")=="home"),{})
                a=(away.get("team") or {}).get("displayName")
                h=(home.get("team") or {}).get("displayName")
                if a and h:
                    fixtures.add((norm(a),norm(h)))
        except Exception as exc:
            logging.warning("NBA schedule verification unavailable for %s: %s",day,type(exc).__name__)
    return fixtures,checked

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
        # A sportsbook event feed is not proof a matchup is on the NBA schedule.
        # Check ESPN before requesting props; never feature unmatched or unverified games.
        official_fixtures,schedule_days_checked=verified_nba_schedule()
        base["official_schedule_days_checked"]=schedule_days_checked
        base["official_schedule_fixture_count"]=len(official_fixtures)
        if not official_fixtures:
            base["error"]="Official NBA schedule unavailable; withholding picks until games can be verified."
            base["schedule_guard"]="FAIL_CLOSED_ESPN"
            return base
        unchecked=len(upcoming)
        upcoming=[e for e in upcoming if
                  (norm(e.get("away_team")),norm(e.get("home_team"))) in official_fixtures]
        base["official_schedule_events_filtered"]=unchecked-len(upcoming)
        base["schedule_guard"]="ESPN_FIXTURE_MATCH_REQUIRED"
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
        # Shadow challenger: connected to live NBA board, but never changes
        # production projections, qualifying picks, odds, or ranking.
        # Historical minutes are used only where genuinely available.
        try:
            from nba_challenger_model import predict as challenger_predict
            challenger_logs=load_game_logs()
            challenger_cache={}
            for signal in signals:
                player_key=canonical_player(signal.get("player"))
                if player_key not in challenger_cache:
                    player_history=[]
                    for game_row in reversed(challenger_logs.get(player_key,[])):
                        minute_value=game_row.get("MIN")
                        player_history.append({
                            "date":game_row["date"],
                            "minutes":minute_value if minute_value is not None and 1<=minute_value<=60 else None,
                            **{k:game_row[k] for k in ("PTS","REB","AST","3PM")},
                        })
                    for game_row in player_history:
                        game_row["PRA"]=game_row["PTS"]+game_row["REB"]+game_row["AST"]
                    challenger_cache[player_key]=challenger_predict(player_history)
                challenger=challenger_cache[player_key]
                market=signal.get("market")
                signal["challenger_projection"]=round(challenger[market],2) if challenger and market in challenger else None
                signal["challenger_status"]="SHADOW_NOT_VALIDATED" if challenger else "INSUFFICIENT_HISTORY"
            base["challenger_status"]="SHADOW_CONNECTED_NOT_VALIDATED"
        except Exception as challenger_error:
            base["challenger_status"]="SHADOW_UNAVAILABLE"
            base["challenger_error"]=type(challenger_error).__name__
        # V4 coefficients are trained offline from completed games only.
        # Missing/stale coefficients are never fabricated or presented as V4.
        v4_coefficients={}
        v4_meta={}
        try:
            from nba_v4_baseline_audit import features as v4_features, estimate as v4_estimate
            from nba_challenger_model import load as v4_load
            v4_path=ROOT/"data/nba_v4_live_coefficients.json"
            v4_meta=json.loads(v4_path.read_text()) if v4_path.exists() else {}
            v4_coefficients=v4_meta.get("markets",{})
            v4_histories=v4_load([str(ROOT/"data"/f"propedge_historical_{season}_minutes.csv") for season in ("2023_24","2024_25","2025_26")]+[str(ROOT/"data/nba_2026_27.csv")]) if v4_coefficients else {}
        except (OSError,ValueError,ImportError,KeyError,TypeError) as v4_error:
            base["v4_error"]=type(v4_error).__name__
            v4_coefficients={}
            v4_histories={}
        for signal in signals:
            primary=number(signal.get("projection"))
            challenger=number(signal.get("challenger_projection"))
            v4=None
            market=signal.get("market")
            history=v4_histories.get(norm(signal.get("player")),[])
            params=v4_coefficients.get(market)
            if params and len(history)>=8:
                try:
                    v4=v4_estimate((params["weights"],params["means"],params["scales"]),v4_features(history,market))
                    if not math.isfinite(v4):v4=None
                except (KeyError,ValueError,TypeError,ZeroDivisionError):
                    v4=None
            signal["v4_projection"]=round(v4,2) if v4 is not None else None
            signal["consensus_components"]={"primary":primary,"challenger":challenger,"v4":signal["v4_projection"]}
            valid=[x for x in (primary,challenger,v4) if x is not None and math.isfinite(x) and x>=0]
            signal["consensus_projection"]=round(sum(valid)/len(valid),2) if valid else None
            signal["consensus_model_count"]=len(valid)
            line=number(signal.get("line"))
            consensus=signal["consensus_projection"]
            signal["consensus_edge"]=round(consensus-line,2) if consensus is not None and line is not None else None
            signal["consensus_side"]="OVER" if signal["consensus_edge"] is not None and signal["consensus_edge"]>0 else ("UNDER" if signal["consensus_edge"] is not None and signal["consensus_edge"]<0 else None)
            signal["consensus_method"]="THREE_MODEL_EQUAL_WEIGHT_RESEARCH" if len(valid)==3 else "PARTIAL_MODEL_RESEARCH"
        # New distribution engine is evaluated alongside live signals only.
        # Never use uncalibrated probabilities for ranking or qualifying bets.
        try:
            from nba_distribution_engine import project as distribution_project, simulate as distribution_simulate
            distribution_logs=load_game_logs()
            distribution_cache={}
            for signal in signals:
                player_key=canonical_player(signal.get("player"))
                if player_key not in distribution_cache:
                    history=[]
                    for game_row in reversed(distribution_logs.get(player_key,[])):
                        minutes=game_row.get("MIN")
                        if minutes is None or minutes<8:
                            continue
                        item={"minutes":minutes}
                        item.update({k:game_row[k] for k in ("PTS","REB","AST","3PM")})
                        item["PRA"]=item["PTS"]+item["REB"]+item["AST"]
                        history.append(item)
                    distribution_cache[player_key]=(history,distribution_project(history))
                history,estimate=distribution_cache[player_key]
                signal["distribution_status"]="INSUFFICIENT_HISTORY"
                if estimate is None:
                    continue
                # Only one quoted side is available on each qualifying signal.
                # Do not invent the opposite-side odds or report EV.
                market=signal.get("market")
                signal["distribution_projection"]=round(estimate["projections"][market],2)
                signal["distribution_minutes"]=round(estimate["minutes"],2)
                signal["distribution_status"]="SHADOW_UNCALIBRATED"
            base["distribution_status"]="SHADOW_ONLY_NO_PRODUCTION_RANKING"
        except Exception as distribution_error:
            logging.warning("NBA distribution shadow unavailable: %s",type(distribution_error).__name__)
            base["distribution_status"]="SHADOW_UNAVAILABLE"
            base["distribution_error"]=type(distribution_error).__name__
        base["consensus_status"]="RESEARCH_ONLY_NOT_PRODUCTION"
        base["v4_trained_at"]=v4_meta.get("as_of_utc")
        # Position-specific defensive matchup adjustments from verified ESPN data.
        matchup_snapshot={}
        try:
            matchup_snapshot=json.loads((ROOT/"data"/"nba_dvp_2026_27.json").read_text())
            stamp=datetime.fromisoformat(matchup_snapshot["fetched_at"].replace("Z","+00:00"))
            if (datetime.now(timezone.utc)-stamp).total_seconds()>129600:matchup_snapshot={}
        except (OSError,ValueError,KeyError,TypeError):pass
        from nba_v3_context import matchup_factor
        # Production v3: independent opportunity/minutes-based projections.
        # Uses the chronologically held-out distribution candidate rather than
        # the old arbitrary OVER deductions, three-way consensus, or v2 overlay.
        # Preserve original projections for grading and audit attribution.
        production=[]
        replaced=0
        missing=0
        rejected=0
        for signal in signals:
            signal["legacy_projection"]=signal.get("projection")
            estimate=number(signal.get("distribution_projection"))
            line=number(signal.get("line"))
            if estimate is None or line is None:
                missing+=1
                # No validated minutes input: do not fabricate a v3 projection.
                signal["projection_method"]="LEGACY_FALLBACK_MISSING_MINUTES_HISTORY"
                production.append(signal)
                continue
            # A quoted sportsbook price is necessary for a pick; reject
            # extreme prices so the ranking isn't dominated by heavy favorites.
            price=number(signal.get("odds"))
            if price is None or price==0 or price < -130 or price > 250:
                rejected+=1
                continue
            estimate,context=matchup_factor(signal,estimate,matchup_snapshot)
            if context:signal["matchup_context"]=context
            direction=1 if signal.get("side")=="OVER" else -1
            edge=direction*(estimate-line)
            threshold=max(0.5,0.08*max(line,1))
            if edge<threshold:
                rejected+=1
                continue
            signal["projection"]=estimate
            signal["gap"]=round(edge,2)
            signal["calibrated_edge"]=round(edge,2)
            signal["projection_method"]="PRODUCTION_V3_OPPORTUNITY_MINUTES_DISTRIBUTION"
            signal["qualification_reason"]="V3 minutes-rate edge, not calibrated win probability"
            signal["distribution_status"]="PRODUCTION_POINT_PROJECTION_UNCALIBRATED_PROBABILITIES"
            replaced+=1
            production.append(signal)
        signals=production
        # Empirical distribution estimates are explicitly uncalibrated;
        # use only to expose uncertainty, never to rank by invented confidence.
        from nba_v3_probability import empirical_probability
        for signal in production:
            if signal.get("projection_method")!="PRODUCTION_V3_OPPORTUNITY_MINUTES_DISTRIBUTION":
                continue
            history=distribution_cache.get(canonical_player(signal.get("player")),(None,None))[0]
            if history:
                estimate=empirical_probability(history,signal.get("market"),signal.get("line"),signal.get("projection"),signal.get("side"))
                if estimate:signal["distribution_estimate"]=estimate
        base["projection_method"]="V3_OPPORTUNITY_MINUTES_DISTRIBUTION_WITH_LEGACY_FALLBACK"
        base["production_model"]="nba_distribution_v3"
        base["production_model_replaced"]=replaced
        base["production_model_fallback"]=missing
        base["production_model_rejected"]=rejected
        base["production_model_warning"]="New projection is holdout-improved versus challenger only; odds EV and win probabilities not validated."
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
        # Verified teammate-absence context: use observed on/off game samples,
        # shrink and cap effect. Never infer minutes or invent lineup confirmations.
        injury_adjusted=0
        for signal in signals:
            attach_availability(signal)
            scenario=signal.get("injury_scenario")
            if not scenario or signal.get("injury_status","").casefold()=="out":
                continue
            if signal.get("projection_method")!="PRODUCTION_V3_OPPORTUNITY_MINUTES_DISTRIBUTION":
                continue
            if scenario.get("with_games",0)<5 or scenario.get("without_games",0)<5:
                continue
            # This is a scenario conditioned on a teammate's current OUT status,
            # not an established causal injury effect.
            original=number(signal.get("projection"))
            line=number(signal.get("line"))
            estimate=number(scenario.get("projection"))
            if original is None or line is None or estimate is None:
                continue
            direction=1 if signal.get("side")=="OVER" else -1
            signal["pre_injury_projection"]=original
            signal["projection"]=estimate
            signal["gap"]=round(abs(estimate-line),2)
            signal["calibrated_edge"]=round(direction*(estimate-line),2)
            signal["projection_method"]="PRODUCTION_V3_OBSERVED_TEAMMATE_ON_OFF"
            signal["injury_adjustment_status"]="OBSERVED_SMALL_SAMPLE_SHRUNK_NOT_CAUSAL"
            injury_adjusted+=1
        signals=[s for s in signals if s.get("calibrated_edge") is None or
                 s["calibrated_edge"]>=max(.5,.08*max(number(s.get("line")) or 1,1))]
        base["production_injury_context_adjusted"]=injury_adjusted

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
        # Current-season sanity guard: do not promote large edges that rely on
        # materially higher production than recent observed games.
        # This is a conservative data-quality screen, NOT calibrated EV.
        screened=[]
        rejected=0
        for signal in signals:
            rows=[g for g in logs.get(canonical_player(signal.get("player")),[])
                  if g.get("season")=="current"]
            rows=sorted(rows,key=lambda g:g.get("date",""),reverse=True)[:8]
            market=signal.get("market")
            if len(rows)>=4 and market in ("PTS","REB","AST","3PM","PRA"):
                values=[sum(g[k] for k in ("PTS","REB","AST")) if market=="PRA"
                        else g.get(market) for g in rows]
                if all(v is not None for v in values):
                    avg=sum(values)/len(values)
                    signal["current_season_avg"]=round(avg,2)
                    signal["current_season_sample"]=len(values)
                    proj=number(signal.get("projection"))
                    if proj is not None and proj>avg*1.4+2 and signal.get("side")=="OVER":
                        signal["qualification_reason"]="Projection substantially exceeds observed current-season production"
                        rejected+=1
                        continue
            screened.append(signal)
        signals=screened
        base["current_season_sanity_filtered"]=rejected
        base["current_season_sanity_mode"]="DESCRIPTIVE_GUARD_NOT_BACKTESTED"
        # For very small current-season samples, flag aggressive projections
        # rather than assuming the historical baseline still reflects the role.
        conservative=[]
        role_rejections=0
        for signal in signals:
            current=sorted((g for g in logs.get(canonical_player(signal.get("player")),[])
                            if g.get("season")=="current"),
                           key=lambda g:g.get("date",""),reverse=True)[:8]
            if len(current)>=3 and signal.get("side")=="OVER":
                market=signal.get("market")
                if market in ("PTS","REB","AST","3PM","PRA"):
                    actual=[sum(g[k] for k in ("PTS","REB","AST")) if market=="PRA"
                            else g.get(market) for g in current]
                    if all(v is not None for v in actual):
                        recent_mean=sum(actual)/len(actual)
                        projection=number(signal.get("projection"))
                        if projection is not None and projection>recent_mean*1.35+0.5:
                            role_rejections+=1
                            continue
            conservative.append(signal)
        signals=conservative
        base["small_sample_role_filtered"]=role_rejections
        # Early-season role uncertainty: do not rank a large projection leap as
        # a qualified pick until more actual regular-season games exist.
        # This is a conservative eligibility gate, not a probability estimate.
        early_safe=[]
        early_rejected=0
        for signal in signals:
            current=sorted((g for g in logs.get(canonical_player(signal.get("player")),[])
                            if g.get("season")=="current"),
                           key=lambda g:g.get("date",""),reverse=True)[:8]
            market=signal.get("market")
            projection=number(signal.get("projection"))
            if 3<=len(current)<=5 and projection is not None and market in ("PTS","REB","AST","3PM","PRA"):
                values=[sum(g[k] for k in ("PTS","REB","AST")) if market=="PRA"
                        else g.get(market) for g in current]
                if all(v is not None for v in values):
                    observed=sum(values)/len(values)
                    # Compare like-for-like production, not the betting line.
                    if observed>0 and abs(projection-observed)/observed>=0.25:
                        signal["qualification_reason"]="Small-sample role uncertainty: projection differs by at least 25% from observed season production"
                        early_rejected+=1
                        continue
            early_safe.append(signal)
        signals=early_safe
        base["early_season_uncertainty_filtered"]=early_rejected
        base["early_season_uncertainty_mode"]="CONSERVATIVE_NOT_BACKTESTED"
        # Fail closed on stale or mismatched player-to-game identities.
        # Historical team alone cannot verify a new-season trade.
        identity_checked=[]
        identity_filtered=0
        for signal in signals:
            player_rows=logs.get(canonical_player(signal.get("player")),[])
            matchup={norm(t) for t in str(signal.get("game") or "").split(" @ ")}
            current_rows=[g for g in player_rows if g.get("season")=="current"]
            # Only the latest observed team can verify the player's current matchup.
            # Checking ANY season team lets a traded player appear in an old matchup.
            latest_team=norm(current_rows[0].get("team")) if current_rows else ""
            if len(matchup)!=2 or not latest_team or latest_team not in matchup:
                identity_filtered+=1
                continue
            signal["identity_verified_by_current_season_logs"]=True
            signal["identity_latest_team"]=latest_team
            identity_checked.append(signal)
        signals=identity_checked
        base["identity_filtered_signals"]=identity_filtered
        base["identity_guard"]="LATEST_CURRENT_SEASON_PLAYER_TEAM_MATCH_REQUIRED"

        # Surface verifiable pregame context on every qualified NBA signal.
        # Never present unavailable advanced inputs as model-adjusted.
        for signal in signals:
            rows=sorted((g for g in logs.get(canonical_player(signal.get("player")),[])
                         if g.get("season")=="current"),key=lambda g:g["date"],reverse=True)
            latest=rows[0] if rows else None
            game_day=datetime.now(timezone.utc).date()
            rest_days=None
            if latest:
                try:
                    observed=datetime.strptime(latest["date"],"%Y%m%d").date()
                    rest_days=max(0,(game_day-observed).days-1)
                except ValueError:pass
            teams=str(signal.get("game") or "").split(" @ ")
            player_team=norm(latest.get("team")) if latest else ""
            home=(player_team==norm(teams[1])) if len(teams)==2 and player_team else None
            if home is False and len(teams)==2 and player_team!=norm(teams[0]):home=None
            mins=[number(g.get("MIN")) for g in rows[:5]]
            mins=[v for v in mins if v is not None and v>0]
            metrics={"minutes":round(sum(mins)/len(mins),2) if mins else None,
                     "usage_rate":None,"pace":None,"opponent_defense":None,
                     "rest_days":rest_days,"back_to_back":rest_days==0 if rest_days is not None else None,
                     "home":home,"travel_miles":None,"team_total":None}
            signal["advanced_metrics"]=metrics
            signal["advanced_metrics_coverage"]={k:v is not None for k,v in metrics.items()}
            signal["advanced_metrics_model_adjusted"]=False
        base["advanced_metrics_mode"]="PREGAME_CONTEXT_ONLY_UNCALIBRATED"

        # Defensive context derived only from completed prior NBA game logs.
        # This is opponent points allowed, NOT a possession-adjusted defensive rating.
        game_team_points=defaultdict(float)
        game_team_players=defaultdict(int)
        for player_games in logs.values():
            for g in player_games:
                if g.get("season")!="current" or not g.get("game_id"):continue
                k=(g["game_id"],norm(g.get("team")),g["date"])
                if number(g.get("PTS")) is not None:
                    game_team_points[k]+=float(g["PTS"])
                    game_team_players[k]+=1
        opponents=defaultdict(list)
        by_fixture=defaultdict(list)
        for (gid,team,day),pts in game_team_points.items():
            if game_team_players[(gid,team,day)]>=5:
                by_fixture[(gid,day)].append((team,pts))
        for (gid,day),teams_played in by_fixture.items():
            if len(teams_played)==2:
                for team,points in teams_played:
                    opponents[team].append((day,next(p for t,p in teams_played if t!=team)))
        for signal in signals:
            teams=str(signal.get("game") or "").split(" @ ")
            team=norm(signal.get("identity_latest_team"))
            if len(teams)!=2 or team not in {norm(t) for t in teams}:continue
            opponent=norm(teams[0] if team==norm(teams[1]) else teams[1])
            samples=sorted(opponents.get(opponent,[]),reverse=True)[:10]
            if len(samples)>=3:
                signal["advanced_metrics"]["opponent_points_allowed_per_game"]=round(sum(v for _,v in samples)/len(samples),2)
                signal["advanced_metrics_coverage"]["opponent_points_allowed_per_game"]=True
            else:
                signal["advanced_metrics"]["opponent_points_allowed_per_game"]=None
                signal["advanced_metrics_coverage"]["opponent_points_allowed_per_game"]=False
        base["defensive_context_source"]="PRIOR_COMPLETED_TEAM_BOXSCORES_NOT_POSSESSION_ADJUSTED"


        # Research-backed selection assistance from settled historical bets.
        # This is a PRIORITY LABEL, not a trained win probability or a
        # replacement for the primary/challenger projection disagreement veto.
        # Do not hide other markets or change eligibility from this small sample.
        for signal in signals:
            market=signal.get("market")
            if market in ("AST","PRA"):
                signal["historical_priority"]="RESEARCH_PRIORITY"
                signal["historical_priority_reason"]="Assists and PRA were positive ROI in the initial 294-pick audit and selected in an exploratory chronological holdout; not forward validated."
            else:
                signal["historical_priority"]="RESEARCH_CAUTION"
                signal["historical_priority_reason"]="This market was not selected by the exploratory historical filter; review carefully, not a proven negative expectation."
            signal["historical_priority_validated"]=False
        base["historical_priority_mode"]="ASSISTIVE_RANKING_NOT_VALIDATED"
        # Expose provenance and enforce a minimum of recent verified observations
        # for featured picks. Historical-only signals remain browseable but not
        # qualified as actionable Top Picks.
        supported=[]
        withheld=0
        for signal in signals:
            player_rows=logs.get(canonical_player(signal.get("player")),[])
            current_rows=[g for g in player_rows if g.get("season")=="current"]
            historical_rows=[g for g in player_rows if g.get("season")=="historical"]
            signal["projection_evidence"]={
                "current_season_games":len(current_rows),
                "historical_games":len(historical_rows),
                "total_unique_game_logs":len(player_rows),
                "latest_game_date":current_rows[0]["date"] if current_rows else None,
                "primary_projection_source":"saved shadow projections CSV",
                "challenger_projection_source":"historical game logs and minutes where available",
                "validation":"NOT_INDEPENDENTLY_VALIDATED",
            }
            if len(current_rows)<4:
                signal["qualification_reason"]="Fewer than 4 verified current-season game logs; excluded from featured picks"
                withheld+=1
                continue
            supported.append(signal)
        signals=supported
        base["insufficient_current_season_evidence_filtered"]=withheld
        base["featured_min_current_season_games"]=4
        signals.sort(key=lambda s:(s["historical_priority"]=="RESEARCH_PRIORITY",s["book_count"],s["gap"]/max(s["line"],1)),reverse=True)
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

@app.get("/api/nba-accuracy-trends")
def nba_accuracy_trends():
    path = ROOT / "reports" / "nba_accuracy_trends.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "REPORT_UNAVAILABLE", "eligible_paired_snapshots": 0}

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
    versions={}
    for row in rows:
        key=str(row.get("production_model_version") or "legacy_unversioned")
        versions.setdefault(key,[]).append(row)
    version_stats={}
    for key,items in versions.items():
        settled=[r for r in items if r.get("status") in ("win","loss","push")]
        wins_v=sum(r["status"]=="win" for r in settled)
        losses_v=sum(r["status"]=="loss" for r in settled)
        priced_v=[r for r in settled if number(r.get("odds")) is not None and abs(float(r["odds"]))>=100]
        pnl=sum((float(r["odds"])/100 if float(r["odds"])>0 else 100/abs(float(r["odds"]))) if r["status"]=="win" else (-1 if r["status"]=="loss" else 0) for r in priced_v)
        version_stats[key]={"captured":len(items),"graded":len(settled),
            "pending":sum(r.get("status")=="pending" for r in items),
            "wins":wins_v,"losses":losses_v,
            "hit_rate":round(100*wins_v/(wins_v+losses_v),1) if wins_v+losses_v else None,
            "priced_graded":len(priced_v),
            "flat_profit_units":round(pnl,3),
            "flat_roi_pct":round(100*pnl/len(priced_v),1) if priced_v else None}
    pending_audit={}
    for r in rows:
        if r.get("status")!="pending":continue
        reason=str(r.get("validation_status") or "awaiting_final_or_matching_boxscore")
        pending_audit[reason]=pending_audit.get(reason,0)+1
    model_disagreement={}
    for market in ("PTS","REB","AST","3PM","PRA"):
        sample=[r for r in completed if r.get("market")==market
                and number(r.get("pregame_primary_projection")) is not None
                and number(r.get("pregame_challenger_projection")) is not None
                and number(r.get("actual")) is not None]
        if not sample:continue
        baseline=sum(abs(float(r["pregame_primary_projection"])-float(r["actual"])) for r in sample)/len(sample)
        challenger=sum(abs(float(r["pregame_challenger_projection"])-float(r["actual"])) for r in sample)/len(sample)
        model_disagreement[market]={"paired":len(sample),"primary_mae":round(baseline,3),
            "challenger_mae":round(challenger,3),
            "note":"Descriptive paired results; not an independent holdout."}
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
            "by_market":by_market,"by_model_version":version_stats,"pending_audit":pending_audit,"paired_projection_audit":model_disagreement,"market_ranking_ready":sum(r.get("status") in ("win","loss") for r in rows)>=100,"windows":windows,"recent":list(reversed(rows[-60:])),
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

@app.get("/api/nba-player-profile/{player_name}")
def nba_player_profile(player_name:str):
    """Current-season and prior-season game logs for every modeled stat."""
    from urllib.parse import unquote
    name=canonical_player(unquote(player_name))
    rows=load_game_logs().get(name,[])
    def pack(r):
        return {"date":r["date"],"season":r["season"],"team":r["team"],"opponent":r.get("opponent"),
                "minutes":r.get("MIN"),**{k:r[k] for k in ("PTS","REB","AST","3PM")},
                "PRA":sum(r[k] for k in ("PTS","REB","AST"))}
    position=None
    try:
        snapshot=json.loads((ROOT/"data"/"nba_dvp_2026_27.json").read_text())
        position=snapshot.get("player_positions",{}).get(name)
    except (OSError,ValueError,TypeError):pass
    return {"player":player_name,"position":position,"current":[pack(r) for r in rows if r["season"]=="current"],
            "previous":[pack(r) for r in rows if r["season"]=="historical"]}

@app.get("/api/nba-defense-position")
def nba_defense_position():
    path=ROOT/"data"/"nba_dvp_2026_27.json"
    if not path.exists():return {"available":False,"teams":{},"reason":"Position-specific verified dataset not synced"}
    try:
        data=json.loads(path.read_text())
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(data["fetched_at"])).total_seconds()/3600
        return {**data,"available":True,"stale":age>36,"age_hours":round(age,1)}
    except (ValueError,KeyError,OSError,TypeError):
        return {"available":False,"teams":{},"reason":"Invalid dataset"}

@app.get("/api/nba-defense")
def nba_defense():
    """Last verified official NBA.com team defensive ratings; no fabricated fallback."""
    path=ROOT/"data"/"nba_defense_2026_27.json"
    if not path.exists():
        return {"available":False,"reason":"Official NBA defensive ratings have not synced yet.","teams":{}}
    try:
        snapshot=json.loads(path.read_text())
        fetched=datetime.fromisoformat(snapshot["fetched_at"].replace("Z","+00:00"))
        age=(datetime.now(timezone.utc)-fetched).total_seconds()/3600
        return {**snapshot,"available":True,"age_hours":round(age,1),
                "stale":age>36}
    except (OSError,ValueError,KeyError,TypeError):
        return {"available":False,"reason":"Defensive ratings snapshot invalid.","teams":{}}

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
