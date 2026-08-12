import unittest

from vector1a.variety import rolling_offset, rolling_value


class VarietyTests(unittest.TestCase):
    def test_frequency_cycle_has_soft_expected_extents(self):
        self.assertAlmostEqual(rolling_value(0, 4, .5, 1), 1.0)
        self.assertAlmostEqual(rolling_value(120, 4, .5, 1), .5)
        self.assertAlmostEqual(rolling_value(240, 4, .5, 1), 1.0)

    def test_range_offset_starts_at_baseline_and_cycles(self):
        self.assertAlmostEqual(rolling_offset(0, 4), 0.0)
        self.assertAlmostEqual(rolling_offset(60, 4), 1.0)
        self.assertAlmostEqual(rolling_offset(180, 4), -1.0)
