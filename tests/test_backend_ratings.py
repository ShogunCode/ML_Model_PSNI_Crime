import unittest

from backend.app.domain import ratings


class TestRatings(unittest.TestCase):
    def test_percentile_empty(self):
        self.assertIsNone(ratings.percentile([], 5))
        self.assertIsNone(ratings.percentile([1, 2, 3], None))

    def test_percentile_rank(self):
        values = [1, 2, 3, 4]
        self.assertAlmostEqual(ratings.percentile(values, 1), 1 / 4)
        self.assertAlmostEqual(ratings.percentile(values, 3), 3 / 4)

    def test_normalize_percentile(self):
        self.assertEqual(ratings.normalize_percentile(0.5), 0.5)
        self.assertEqual(ratings.normalize_percentile(50), 0.5)
        self.assertIsNone(ratings.normalize_percentile(None))

    def test_rating_score_fraction_or_percent(self):
        score_fraction = ratings.rating_score(0.8, 0.2)
        score_percent = ratings.rating_score(80, 20)
        self.assertEqual(score_fraction, score_percent)
        self.assertAlmostEqual(score_fraction, round((0.7 * 0.8 + 0.3 * 0.2) * 100, 1))

    def test_rating_band(self):
        self.assertEqual(ratings.rating_band(None), "Unknown")
        self.assertEqual(ratings.rating_band(90), "High")
        self.assertEqual(ratings.rating_band(72), "Elevated")
        self.assertEqual(ratings.rating_band(56), "Watch")
        self.assertEqual(ratings.rating_band(40), "Stable")

    def test_trend_direction(self):
        self.assertEqual(ratings.trend_direction(None), "flat")
        self.assertEqual(ratings.trend_direction(0.1), "up")
        self.assertEqual(ratings.trend_direction(-0.1), "down")
        self.assertEqual(ratings.trend_direction(0.0), "flat")


if __name__ == "__main__":
    unittest.main()
