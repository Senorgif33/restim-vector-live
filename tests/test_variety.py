import unittest

from vector1a.variety import (fit_range_for_travel, rolling_offset, rolling_value,
                              speed_linked_depth)


class VarietyTests(unittest.TestCase):
    def test_speed_linked_depth_is_smooth_bounded_and_reaches_endpoints(self):
        self.assertEqual(speed_linked_depth(0, 40), 0.0)
        self.assertAlmostEqual(speed_linked_depth(20, 40), .5)
        self.assertEqual(speed_linked_depth(40, 40), 1.0)
        self.assertEqual(speed_linked_depth(100, 40), 1.0)
        values = [speed_linked_depth(speed, 40) for speed in range(101)]
        self.assertEqual(values, sorted(values))
    def test_frequency_cycle_has_soft_expected_extents(self):
        self.assertAlmostEqual(rolling_value(0, 4, .5, 1), 1.0)
        self.assertAlmostEqual(rolling_value(120, 4, .5, 1), .5)
        self.assertAlmostEqual(rolling_value(240, 4, .5, 1), 1.0)

    def test_range_offset_starts_at_baseline_and_cycles(self):
        self.assertAlmostEqual(rolling_offset(0, 4), 0.0)
        self.assertAlmostEqual(rolling_offset(60, 4), 1.0)
        self.assertAlmostEqual(rolling_offset(180, 4), -1.0)

    def test_range_is_fitted_to_allow_full_symmetric_travel(self):
        self.assertEqual(fit_range_for_travel(.4, .95), (.25, .8))
        self.assertEqual(fit_range_for_travel(.1, .9), (.2, .8))
        self.assertEqual(fit_range_for_travel(.2, .55), (.2, .55))

    def test_independent_cycles_reach_peaks_at_different_times(self):
        self.assertAlmostEqual(rolling_offset(60, 4), 1.0)
        self.assertAlmostEqual(rolling_offset(45, 3), 1.0)
        self.assertAlmostEqual(rolling_offset(30, 2), 1.0)
        self.assertAlmostEqual(rolling_offset(15, 1), 1.0)
