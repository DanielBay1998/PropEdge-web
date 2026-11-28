"""Unit checks for the research-only scoring-volatility risk evaluator.

Run with: python -m unittest test_nba_volatility_risk_calibration.py
"""
import unittest
from nba_volatility_risk_calibration import evaluate

class CalibrationChecks(unittest.TestCase):
    def test_constant_perfect_risk(self):
        rows=[{"error":6},{"error":1}]
        result=evaluate(rows,lambda r: 1.0 if r["error"]>5 else 0.0)
        self.assertEqual(result["n"],2)
        self.assertEqual(result["brier"],0.0)
        self.assertEqual(result["miss_rate_pct"],50.0)

    def test_baseline_brier(self):
        rows=[{"error":6},{"error":1}]
        result=evaluate(rows,lambda r:0.5)
        self.assertEqual(result["brier"],0.25)

    def test_strictly_greater_than_five(self):
        result=evaluate([{"error":5.0}],lambda r:0.0)
        self.assertEqual(result["miss_rate_pct"],0.0)

    def test_empty_sample(self):
        self.assertEqual(evaluate([],lambda r:0.5),{"n":0})

if __name__=="__main__":
    unittest.main()
