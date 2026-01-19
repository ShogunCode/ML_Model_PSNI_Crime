import os
import sys
import unittest

import numpy as np
import pandas as pd


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from utils import (
    calculate_crime_rate,
    handle_missing_values,
    haversine_distance,
    optimise_data_types,
    extract_coordinates_from_geometry,
)


class DummyGeometry:
    def __init__(self, x, y):
        self.x = x
        self.y = y


class TestUtils(unittest.TestCase):
    def test_calculate_crime_rate_handles_zero_and_nan(self):
        self.assertEqual(calculate_crime_rate(10, 0), 0.0)
        self.assertEqual(calculate_crime_rate(10, float("nan")), 0.0)
        self.assertEqual(calculate_crime_rate(200, 100000), 200.0)

    def test_handle_missing_values_mean(self):
        df = pd.DataFrame({"a": [1.0, np.nan, 3.0], "b": [1, 2, 3]})
        result = handle_missing_values(df.copy(), strategy="mean")
        self.assertAlmostEqual(result.loc[1, "a"], 2.0)

    def test_handle_missing_values_mode(self):
        df = pd.DataFrame({"a": ["x", "y", None, "x"]})
        result = handle_missing_values(df.copy(), strategy="mode")
        self.assertEqual(result.loc[2, "a"], "x")

    def test_optimise_data_types_downcasts(self):
        df = pd.DataFrame(
            {
                "floats": pd.Series([1.5, 2.5], dtype="float64"),
                "ints": pd.Series([1, 2], dtype="int64"),
                "cats": ["a", "a"],
            }
        )
        result = optimise_data_types(df.copy())
        self.assertTrue(pd.api.types.is_float_dtype(result["floats"]))
        self.assertLessEqual(result["floats"].dtype.itemsize, 4)
        self.assertTrue(pd.api.types.is_integer_dtype(result["ints"]))
        self.assertLessEqual(result["ints"].dtype.itemsize, 4)
        self.assertTrue(isinstance(result["cats"].dtype, pd.CategoricalDtype))

    def test_haversine_distance_same_point(self):
        distance = haversine_distance((0.0, 0.0), (0.0, 0.0))
        self.assertAlmostEqual(distance, 0.0, places=6)

    def test_haversine_distance_one_degree_lat(self):
        distance = haversine_distance((0.0, 0.0), (0.0, 1.0))
        self.assertTrue(110.0 <= distance <= 112.5)

    def test_extract_coordinates_from_geometry(self):
        geom = DummyGeometry(5.0, -3.0)
        self.assertEqual(extract_coordinates_from_geometry(geom), (5.0, -3.0))


if __name__ == "__main__":
    unittest.main()
