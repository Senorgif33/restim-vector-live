import math
import unittest

from vector1a.fourphase import (crossover_blend, directed_signed, map_electrode_order,
                                normalize_signed, pair_morph, potential_roles,
                                pair_swapped_order, restim_crossfade, vertical_crossfade)


class FourPhaseCommissioningTests(unittest.TestCase):
    def test_electrode_centres(self):
        self.assertEqual(vertical_crossfade(0.0), (1.0, 0.0, 0.0, 0.0))
        self.assertAlmostEqual(vertical_crossfade(1 / 3)[1], 1.0)
        self.assertAlmostEqual(vertical_crossfade(2 / 3)[2], 1.0)
        self.assertAlmostEqual(vertical_crossfade(1.0)[3], 1.0)

    def test_only_adjacent_electrodes_overlap_at_constant_power(self):
        for position, pair in ((1 / 6, (0, 1)), (0.5, (1, 2)), (5 / 6, (2, 3))):
            weights = vertical_crossfade(position)
            self.assertEqual({i for i, value in enumerate(weights) if value > 1e-9}, set(pair))
            self.assertAlmostEqual(sum(value * value for value in weights), 1.0)
            self.assertAlmostEqual(weights[pair[0]], math.sqrt(0.5))
            self.assertAlmostEqual(weights[pair[1]], math.sqrt(0.5))

    def test_input_is_clamped(self):
        self.assertEqual(vertical_crossfade(-1), vertical_crossfade(0))
        self.assertEqual(vertical_crossfade(2), vertical_crossfade(1))

    def test_signed_model_has_exact_directed_states_at_nodes(self):
        expected = ((1, -.3, 0, 0), (0, 1, -.3, 0),
                    (0, 0, 1, -.3), (0, 0, -.3, 1))
        for position, target in zip((0, 1/3, 2/3, 1), expected):
            signed, _, _ = directed_signed(position, 1, .3)
            for actual, wanted in zip(signed, target):
                self.assertAlmostEqual(actual, wanted)

    def test_forward_handover_never_reports_non_adjacent_primary_return(self):
        for step in range(301):
            _, primary, preferred_return = directed_signed(step / 300, 1, .3)
            self.assertLessEqual(abs(primary - preferred_return), 1)

    def test_reverse_handover_never_reports_non_adjacent_primary_return(self):
        for step in range(301):
            _, primary, preferred_return = directed_signed(step / 300, -1, .3)
            self.assertLessEqual(abs(primary - preferred_return), 1)

    def test_first_section_reports_only_adjacent_roles(self):
        signed, primary, preferred_return = directed_signed(.097, 1, .3)
        self.assertEqual((primary, preferred_return), (0, 1))
        self.assertEqual(potential_roles(signed, primary, preferred_return), ("A", "B"))
        normalized = normalize_signed(signed)
        self.assertEqual(max(normalized), 1.0)
        self.assertEqual(min(normalized), 0.0)

    def test_reverse_stroke_reverses_directed_pair(self):
        _, primary, preferred_return = directed_signed(.8, -1)
        self.assertLessEqual(abs(primary - preferred_return), 1)
        _, primary, preferred_return = directed_signed(.5, -1)
        self.assertLessEqual(abs(primary - preferred_return), 1)

    def test_crossfade_is_continuous_at_internal_boundaries(self):
        for boundary in (1/3, 2/3):
            before = directed_signed(boundary - 1e-7, 1, .3)[0]
            at = directed_signed(boundary, 1, .3)[0]
            after = directed_signed(boundary + 1e-7, 1, .3)[0]
            for left, centre, right in zip(before, at, after):
                self.assertAlmostEqual(left, centre, places=10)
                self.assertAlmostEqual(right, centre, places=10)

    def test_reversal_is_continuous_at_endpoints(self):
        for upward, downward in ((directed_signed(1, 1, .3)[0], directed_signed(1, -1, .3)[0]),
                                 (directed_signed(0, 1, .3)[0], directed_signed(0, -1, .3)[0])):
            for a, b in zip(upward, downward):
                self.assertAlmostEqual(a, b)

    def test_restim_crossfade_is_continuous_and_always_has_primary(self):
        previous = restim_crossfade(0, 1, .3)
        for step in range(1, 1001):
            current = restim_crossfade(step / 1000, 1, .3)
            self.assertAlmostEqual(max(current), 1.0)
            self.assertLess(max(abs(a - b) for a, b in zip(previous, current)), .02)
            previous = current

    def test_unused_electrodes_are_not_promoted_after_first_boundary(self):
        before = restim_crossfade(1/3 - 1e-6, 1, .3)
        at = restim_crossfade(1/3, 1, .3)
        after = restim_crossfade(1/3 + 1e-6, 1, .3)
        for a, b, c in zip(before, at, after):
            self.assertAlmostEqual(a, b, places=5)
            self.assertAlmostEqual(b, c, places=5)
        self.assertEqual(at.index(max(at)), 1)

    def test_default_crossover_remains_cosine(self):
        for progress in (0, .1, .5, .9, 1):
            expected = (1.0 - math.cos(progress * math.pi)) / 2.0
            self.assertAlmostEqual(crossover_blend(progress), expected)

    def test_narrow_crossover_adds_stable_endpoint_regions(self):
        self.assertEqual(crossover_blend(.2, .5), 0.0)
        self.assertEqual(crossover_blend(.8, .5), 1.0)
        self.assertAlmostEqual(crossover_blend(.5, .5), .5)

    def test_all_curves_are_monotonic_and_bounded(self):
        for curve in ("Cosine", "Linear", "Ease In", "Ease Out", "S-curve"):
            values = [crossover_blend(i / 100, .8, curve, 1.7) for i in range(101)]
            self.assertEqual(values[0], 0.0)
            self.assertEqual(values[-1], 1.0)
            self.assertTrue(all(a <= b for a, b in zip(values, values[1:])))

    def test_electrode_order_maps_logical_path_to_physical_channels(self):
        values = (.1, .2, .3, .4)
        self.assertEqual(map_electrode_order(values, "ABCD"), values)
        self.assertEqual(map_electrode_order(values, "ABDC"), (.1, .2, .4, .3))
        self.assertEqual(map_electrode_order(values, "BACD"), (.2, .1, .3, .4))
        self.assertEqual(map_electrode_order(values, "ACBD"), (.1, .3, .2, .4))

    def test_pair_morph_has_exact_normal_midpoint_and_swapped_endpoints(self):
        values = (.1, .2, .3, .4)
        self.assertEqual(pair_morph(values, 0), values)
        self.assertEqual(pair_morph(values, 1), (.2, .1, .4, .3))
        for actual, expected in zip(pair_morph(values, .5), (.15, .15, .35, .35)):
            self.assertAlmostEqual(actual, expected)

    def test_pair_swapped_sequence_names_match_full_morph_destination(self):
        self.assertEqual(pair_swapped_order("ABCD"), "BADC")
        self.assertEqual(pair_swapped_order("ABDC"), "BACD")
        self.assertEqual(pair_swapped_order("BACD"), "ABDC")
        self.assertEqual(pair_swapped_order("ACBD"), "BDAC")


if __name__ == "__main__":
    unittest.main()
