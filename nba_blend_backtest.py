"""Compare captured primary NBA picks with time-safe challenger and 50/50 blend."""
import json, math
from pathlib import Path
from nba_challenger_model import load, predict
from nba_matchup_features import load_team_games, prior_allowances

ROOT=Path(__file__).resolve().parent
def run():
    history=load([str(ROOT/"data/propedge_historical_2025_26_minutes.csv"),
                  str(ROOT/"data/nba_2026_27.csv")])
    opponent_games=load_team_games([str(ROOT/"data/propedge_historical_2025_26_minutes.csv"),
                                    str(ROOT/"data/nba_2026_27.csv")])
    tracking=json.loads((ROOT/"data/model_tracking.json").read_text())
    totals={name:{"n":0,"wins":0,"losses":0,"pushes":0,"absolute_error":0.0}
            for name in ("primary","challenger","blend","defense_challenger")}
    matched=0
    defense_matched=0
    for row in tracking:
        if row.get("status") not in ("win","loss","push"):continue
        name=" ".join(str(row.get("player") or "").casefold().split())
        day=str(row.get("captured_at") or "")[:10].replace("-","")
        prior=[g for g in history.get(name,[]) if g["date"]<day]
        alt=predict(prior)
        market=row.get("market")
        if not alt or market not in alt:continue
        try:
            actual=float(row["actual"]);primary=float(row["projection"])
            line=float(row["line"])
        except (TypeError,ValueError,KeyError):continue
        if not all(math.isfinite(x) for x in (actual,primary,line)):continue
        matched+=1
        predictions={"primary":primary,"challenger":alt[market],
                     "blend":(primary+alt[market])/2}
        for model,estimate in predictions.items():
            item=totals[model]
            item["n"]+=1
            item["absolute_error"]+=abs(estimate-actual)
            direction=1 if estimate>line else -1 if estimate<line else 0
            result=direction*(actual-line)
            if direction==0 or result==0:item["pushes"]+=1
            elif result>0:item["wins"]+=1
            else:item["losses"]+=1
    for item in totals.values():
        item["mae"]=round(item.pop("absolute_error")/item["n"],3) if item["n"] else None
        resolved=item["wins"]+item["losses"]
        item["hit_rate"]=round(100*item["wins"]/resolved,2) if resolved else None
    result={"status":"ONE_CAPTURE_DATE_RETROSPECTIVE_NOT_OUT_OF_SAMPLE",
            "graded_total":sum(r.get("status") in ("win","loss","push") for r in tracking),
            "matched":matched,"defense_matched":defense_matched,"models":totals,
            "limitations":["Only one capture date; not an independent holdout",
                           "Original predictions are captured; challenger reconstructed from prior box scores",
                           "Odds, market eligibility and selection policy are not held constant",
                           "Missing challenger history reduces matched sample"]}
    (ROOT/"reports").mkdir(exist_ok=True)
    (ROOT/"reports/nba_blend_comparison.json").write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(result,indent=2))
    if matched==0:
        raise SystemExit("No matched graded picks with challenger history; cannot compare models")
if __name__=="__main__":run()
