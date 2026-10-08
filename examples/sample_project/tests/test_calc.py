import unittest

from calc import average


class AverageTests(unittest.TestCase):
    def test_mean_keeps_fractional_part(self):
        self.assertAlmostEqual(average([1, 2, 3, 4]), 2.5)

    def test_integer_mean_is_float(self):
        self.assertIsInstance(average([2, 4]), float)

    def test_empty_input_returns_zero(self):
        self.assertEqual(average([]), 0.0)
