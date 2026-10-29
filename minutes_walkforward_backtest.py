"""Walk-forward minute-aware research backtest. No production changes or future leakage."""
import argparse
from pathlib import Path
import numpy as np
import pandas as pd

MARKETS=("PTS","REB","AST","3PM","PRA")
def run(args):
    frames=[]
    for path in (args.historical,args.current):
        df=pd.read_csv(path,dtype={"Game ID":str,"Date":str})
        if "MIN" not in df: raise ValueError(f"{path}: minutes column missing")
        frames.append(df)
    df=pd.concat(frames,ignore_index=True)
    df["Date"]=pd.to_datetime(df["Date"],format="%Y%m%d",errors="coerce")
    for col in ("PTS","REB","AST","3PM","MIN"):
        df[col]=pd.to_numeric(df[col],errors="coerce")
    df=df.dropna(subset=["Player","Date","PTS","REB","AST","3PM"])
    df=df.drop_duplicates(["Player","Game ID","Date"],keep="last")
    df["PRA"]=df["PTS"]+df["REB"]+df["AST"]
    df.loc[~df["MIN"].between(1,60),"MIN"]=np.nan
    df=df.sort_values(["Player","Date","Game ID"])
    rows=[]
    for player,g in df.groupby("Player",sort=False):
        g=g.reset_index(drop=True)
        for i in range(args.min_history,len(g)):
            # Only games strictly before this game's date; exclude same-day entries.
            prior=g.iloc[:i]
            prior=prior[prior["Date"]<g.iloc[i]["Date"]]
            if len(prior)<args.min_history:continue
            valid=prior.dropna(subset=["MIN"]).tail(20)
            if len(valid)<3:continue
            last10=prior.tail(10)
            projected_min=float(np.clip(.7*valid.tail(5)["MIN"].mean()+.3*valid["MIN"].mean(),8,42))
            for market in MARKETS:
                actual=float(g.iloc[i][market])
                baseline=float(last10[market].mean())
                recent_rate=float((valid.tail(10)[market]/valid.tail(10)["MIN"]).mean())
                long_rate=float((valid[market]/valid["MIN"]).mean())
                minute_based=projected_min*(.65*recent_rate+.35*long_rate)
                # Same conservative weighting as the candidate, with a historical-only baseline.
                candidate=.75*baseline+.25*minute_based
                rows.append(dict(Player=player,Date=g.iloc[i]["Date"].strftime("%Y-%m-%d"),
                    Market=market,Actual=actual,Baseline=baseline,Candidate=candidate,
                    BaselineAE=abs(baseline-actual),CandidateAE=abs(candidate-actual),
                    MinutesSample=len(valid),ProjectedMinutes=projected_min,
                    ActualMinutes=g.iloc[i]["MIN"]))
    if not rows:raise RuntimeError("No eligible historical evaluations")
    out=pd.DataFrame(rows).sort_values(["Date","Player","Market"])
    dates=sorted(out["Date"].unique())
    cut=dates[max(0,int(len(dates)*.8)-1)]
    out["Split"]=np.where(out["Date"]>cut,"HOLDOUT","DEVELOPMENT")
    summary=out.groupby(["Split","Market"]).agg(
        Games=("Actual","size"),BaselineMAE=("BaselineAE","mean"),
        CandidateMAE=("CandidateAE","mean")).reset_index()
    summary["MAEImprovement"]=summary["BaselineMAE"]-summary["CandidateMAE"]
    summary["ImprovementPct"]=100*summary["MAEImprovement"]/summary["BaselineMAE"].replace(0,np.nan)
    # No betting hit rate or ROI: historical pregame lines/odds are not supplied here.
    summary["Comparison"]="historical rolling-average proxy; NOT production-model head-to-head"
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    out.to_csv(path,index=False)
    summary.to_csv(path.with_name("minutes_walkforward_summary.csv"),index=False)
    print(summary.to_string(index=False))
    print(f"Evaluations: {len(out)}; holdout begins after {cut}; no production model changes")
if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--historical",default="data/propedge_historical_2025_26_minutes.csv")
    p.add_argument("--current",default="data/nba_2026_27.csv")
    p.add_argument("--output",default="reports/minutes_walkforward_predictions.csv")
    p.add_argument("--min-history",type=int,default=10)
    run(p.parse_args())
