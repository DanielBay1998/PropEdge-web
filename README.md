# PropEdge custom mobile web app (beta)

This is a **separate FastAPI web app**, not a Streamlit skin. It uses real HTML navigation buttons and mobile cards.

## Deploy on Render
1. Create a new GitHub repository, upload the **contents** of this folder (not the folder itself).
2. On Render choose **New > Blueprint**, connect the repo, and apply `render.yaml`.
3. Open the generated Render URL. Do not delete the existing Streamlit app until you verify this one.

## Data / limitations
`data/propedge_shadow_projections.csv` and `data/saved_odds_snapshot.csv` are copied from an **older repository archive** (before the later roster-repair commit). Replace both with your newest versions before deployment. The site labels all quotes as saved snapshots, **not live**. No odds refresh, PrizePicks integration, authentication, betting placement, calibrated probabilities or EV computation is implemented. The top cards are ranked by relative model-line gap, not profitability. All Props only lists matched signals, not the entire NBA slate. For live data, a server-side authenticated provider integration and cache/refresh jobs are needed.

Run locally: `pip install -r requirements.txt && uvicorn server:app --reload`.
