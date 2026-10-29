"""Filter exhibition teams from NBA research histories without altering raw data."""
from nba_challenger_model import load as base_load

def load(paths):
    histories=base_load(paths)
    return {name:clean for name,rows in histories.items() if (clean:=[r for r in rows if not r.get('team','').startswith('team ') and r.get('team') not in ('world','usa')])}
