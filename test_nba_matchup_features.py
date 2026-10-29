import unittest
from nba_matchup_features import prior_allowances,load_team_games

class MatchupFeatureTests(unittest.TestCase):
    def test_future_games_excluded(self):
        rows={"atlanta hawks":[
            {"date":"20261020","allowed":(100,40,20,10)},
            {"date":"20261021","allowed":(100,40,20,10)},
            {"date":"20261022","allowed":(100,40,20,10)},
            {"date":"20261023","allowed":(100,40,20,10)},
            {"date":"20261024","allowed":(100,40,20,10)},
            {"date":"20261025","allowed":(100,40,20,10)},
            {"date":"20261026","allowed":(100,40,20,10)},
            {"date":"20261027","allowed":(100,40,20,10)},
            {"date":"20261029","allowed":(300,90,60,25)}]}
        result=prior_allowances(rows,"Atlanta Hawks","20261028")
        self.assertEqual(result["PTS"],100)
        self.assertEqual(result["REB"],40)
    def test_insufficient_history_fails_closed(self):
        self.assertIsNone(prior_allowances({"team":[{"date":"20261020","allowed":(100,40,20,10)}]},"team","20261028"))
    def test_missing_file(self):
        self.assertEqual(load_team_games(["/nonexistent/propedge_missing.csv"]),{})
if __name__=="__main__":unittest.main()
