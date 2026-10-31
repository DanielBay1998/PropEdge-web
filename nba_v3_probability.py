"""Transparent historical-residual probability estimates.
NOT calibrated sportsbook probabilities. No fabricated certainty or EV.
"""
from statistics import mean
def empirical_probability(history,market,line,projection,side):
    if market not in ("PTS","REB","AST","3PM","PRA"):return None
    try:
        rows=history[-30:]
        if len(rows)<12:return None
        values=[float(r[market]) for r in rows]
        center=mean(values)
        outcomes=[max(0,round(float(projection)+v-center)) for v in values]
        threshold=float(line)
        wins=sum((v>threshold if str(side).upper()=="OVER" else v<threshold) for v in outcomes)
        pushes=sum(v==threshold for v in outcomes)
        # Jeffreys smoothing reduces pathological 0%/100% small-sample estimates.
        probability=(wins+.5)/(len(outcomes)+1)
        return {"win_probability_estimate":round(probability,3),
                "push_frequency":round(pushes/len(outcomes),3),
                "observations":len(outcomes),
                "status":"UNCALIBRATED_EMPIRICAL_RESIDUAL_NOT_BETTING_CONFIDENCE"}
    except (ValueError,TypeError,KeyError,ZeroDivisionError):
        return None
