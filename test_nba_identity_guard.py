"""Regression tests for NBA player-to-game identity safety."""
import unittest
import re

def canonical_player(name):
    return re.sub(r'[^a-z0-9]', '', str(name).lower())

def norm(name):
    return re.sub(r'[^a-z0-9]', '', str(name).lower())

def latest_team_matches(logs, player, matchup):
    current=[g for g in logs.get(canonical_player(player),[]) if g.get("season")=="current"]
    latest=norm(current[0].get("team")) if current else ""
    teams={norm(t) for t in matchup.split(" @ ")}
    return len(teams)==2 and bool(latest) and latest in teams

class IdentityTests(unittest.TestCase):
    def test_traded_player_old_team_is_rejected(self):
        logs={"example player":[{"season":"current","team":"Atlanta Hawks"},{"season":"current","team":"Portland Trail Blazers"}]}
        self.assertFalse(latest_team_matches(logs,"example player","Portland Trail Blazers @ Memphis Grizzlies"))
        self.assertTrue(latest_team_matches(logs,"example player","Atlanta Hawks @ Memphis Grizzlies"))
    def test_historical_only_is_rejected(self):
        self.assertFalse(latest_team_matches({"example player":[{"season":"historical","team":"Atlanta Hawks"}]},"example player","Atlanta Hawks @ Memphis Grizzlies"))
    def test_no_matchup_is_rejected(self):
        self.assertFalse(latest_team_matches({},"example player",""))
if __name__=="__main__":unittest.main()
