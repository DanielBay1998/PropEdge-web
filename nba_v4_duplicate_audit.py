"""Audit duplicate player-date rows without silently deleting valid doubleheaders."""
import csv,json
from collections import defaultdict
from pathlib import Path
from nba_v4_baseline_audit import PATHS,ROOT

def run():
    seen=defaultdict(list)
    for path in PATHS:
        if not Path(path).exists():continue
        with open(path,encoding="utf-8-sig",newline="") as f:
            for line,row in enumerate(csv.DictReader(f),2):
                player=" ".join((row.get("Player") or "").casefold().split())
                date=str(row.get("Date") or "")
                if player and len(date)==8 and date.isdigit():
                    seen[(player,date)].append({"file":Path(path).name,"line":line,
                        "game_id":str(row.get("Game ID") or ""),"team":row.get("Team"),
                        "pts":row.get("PTS"),"reb":row.get("REB"),"ast":row.get("AST"),
                        "three_pm":row.get("3PM"),"min":row.get("MIN")})
    groups=[]
    for (player,date),items in sorted(seen.items()):
        if len(items)<2:continue
        ids={r["game_id"] for r in items}
        same_game=len(ids)==1
        groups.append({"player":player,"date":date,"classification":
            "same_game_duplicate" if same_game else "multiple_game_ids_review",
            "rows":items})
    counts={"same_game_duplicate":sum(g["classification"]=="same_game_duplicate" for g in groups),
            "multiple_game_ids_review":sum(g["classification"]=="multiple_game_ids_review" for g in groups)}
    report={"status":"REVIEW_ONLY_NO_DATA_MUTATION","duplicate_player_date_groups":len(groups),
            "classification_counts":counts,"groups":groups,
            "note":"Different game IDs may be valid multiple games; do not auto-drop. Same game IDs across files may have differing statistics and require source reconciliation."}
    out=ROOT/"reports/nba_v4_duplicate_audit.json";out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({"status":report["status"],"groups":len(groups),"counts":counts,
                      "sample":groups[:5]},indent=2))
if __name__=="__main__":run()
