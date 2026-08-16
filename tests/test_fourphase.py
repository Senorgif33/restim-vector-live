import math
import unittest

from vector1a.fourphase import (adaptive_crossover_width, apply_group_delay, crossover_blend,
                                depth_spread, directed_signed, directional_crossover_profile,
                                map_electrode_order,
                                interpolate_electrodes, morph_electrode_order, moving_sequence_window,
                                normalize_signed, pair_morph,
                                potential_roles, pair_swapped_order, restim_crossfade,
                                proportional_reversal_boost, reversal_emphasis_envelope,
                                sequence_cycle_stage, spatial_response,
                                stroke_phase_crossover, vertical_crossfade)


class FourPhaseCommissioningTests(unittest.TestCase):
    def assert_restim_constraints(self, values):
        self.assertAlmostEqual(max(values), 1.0)
        anchor = values.index(max(values))
        self.assertGreaterEqual(
            sum(value for index, value in enumerate(values) if index != anchor),
            1.0 - 1e-12)

    def test_depth_spread_has_conservative_endpoints(self):
        tip = depth_spread(0.0, .80, .20)
        full = depth_spread(1.0, .80, .20)
        self.assertEqual(tip, (1.0, 1 / 3, 1 / 3, 1 / 3))
        self.assertEqual(full, (.80, 1.0, 1.0, 1.0))

    def test_depth_spread_accumulates_reached_electrodes(self):
        shallow = depth_spread(1 / 6, .80, .20)
        middle = depth_spread(.5, .80, .20)
        deep = depth_spread(5 / 6, .80, .20)
        self.assertGreater(shallow[1], shallow[2])
        self.assertGreater(middle[2], shallow[2])
        self.assertGreater(deep[3], middle[3])
        self.assertGreaterEqual(middle[1], shallow[1])
        self.assertGreaterEqual(deep[1], middle[1])
        self.assertGreaterEqual(deep[2], middle[2])

    def test_depth_spread_is_continuous_at_region_boundaries(self):
        for boundary in (1 / 3, 2 / 3):
            before = depth_spread(boundary - 1e-7, .80, .20)
            at = depth_spread(boundary, .80, .20)
            after = depth_spread(boundary + 1e-7, .80, .20)
            self.assertLess(max(abs(a - b) for a, b in zip(before, at)), 1e-5)
            self.assertLess(max(abs(a - b) for a, b in zip(at, after)), 1e-5)

    def test_depth_spread_withdrawal_retraces_penetration(self):
        penetration = [depth_spread(step / 100, .73, .64) for step in range(101)]
        withdrawal = [depth_spread(step / 100, .73, .64) for step in range(100, -1, -1)]
        self.assertEqual(withdrawal, list(reversed(penetration)))

    def test_depth_spread_mapping_happens_after_logical_profile(self):
        logical = depth_spread(.45, .80, .20)
        self.assertEqual(map_electrode_order(logical, "ABDC"),
                         (logical[0], logical[1], logical[3], logical[2]))
        self.assertEqual(map_electrode_order(logical, "BACD"),
                         (logical[1], logical[0], logical[2], logical[3]))

    def test_depth_spread_always_satisfies_restim_constraints(self):
        for retention in (0.0, .2, .8, 1.0):
            for softness in (0.0, .2, .7, 1.0):
                previous = depth_spread(0.0, retention, softness)
                self.assert_restim_constraints(previous)
                for step in range(1, 1001):
                    current = depth_spread(step / 1000, retention, softness)
                    self.assert_restim_constraints(current)
                    self.assertLess(max(abs(a - b) for a, b in zip(previous, current)), .02)
                    previous = current

    def test_moving_sequence_window_returns_to_base_at_endpoints(self):
        for progress in (0.0, 1.0):
            source, target, amount = moving_sequence_window("ABDC", 1, progress, .8, 1.0)
            self.assertEqual((source, target), ("ABDC", "BACD"))
            self.assertEqual(amount, 0.0)

    def test_moving_sequence_window_peaks_at_midstroke(self):
        source, target, amount = moving_sequence_window("ABDC", 1, .5, .6, .8)
        self.assertEqual((source, target), ("ABDC", "BACD"))
        self.assertAlmostEqual(amount, .6)

    def test_moving_sequence_window_reverses_neighbour_by_direction(self):
        self.assertEqual(moving_sequence_window("ABDC", 1, .5)[:2], ("ABDC", "BACD"))
        self.assertEqual(moving_sequence_window("ABDC", -1, .5)[:2], ("ABDC", "ABCD"))

    def test_moving_sequence_window_is_continuous_and_bounded(self):
        amounts = [moving_sequence_window("ABCD", 1, step / 100, .75, .6)[2]
                   for step in range(101)]
        self.assertTrue(all(0.0 <= amount <= .75 for amount in amounts))
        self.assertLess(max(abs(a - b) for a, b in zip(amounts, amounts[1:])), .05)

    def test_electrode_history_interpolates_and_clamps(self):
        history = [(0.0, (0.0, .1, .2, .3)),
                   (1.0, (1.0, .9, .8, .7))]
        self.assertEqual(interpolate_electrodes(history, -1), history[0][1])
        self.assertEqual(interpolate_electrodes(history, 2), history[-1][1])
        self.assertEqual(interpolate_electrodes(history, .5), (.5, .5, .5, .5))

    def test_positive_group_delay_delays_only_ab(self):
        history = [(0.0, (0.0, 0.0, 0.0, 0.0)),
                   (1.0, (1.0, 1.0, 1.0, 1.0))]
        self.assertEqual(apply_group_delay((1.0, 1.0, 1.0, 1.0), history, 1.0, .5),
                         (.5, .5, 1.0, 1.0))

    def test_negative_group_delay_delays_only_cd(self):
        history = [(0.0, (0.0, 0.0, 0.0, 0.0)),
                   (1.0, (1.0, 1.0, 1.0, 1.0))]
        self.assertEqual(apply_group_delay((1.0, 1.0, 1.0, 1.0), history, 1.0, -.5),
                         (1.0, 1.0, .5, .5))

    def test_stroke_phase_crossover_blends_smoothly_and_is_bounded(self):
        self.assertEqual(stroke_phase_crossover(.5, 0, False, .5, 1.5), (.5, "off"))
        start, start_name = stroke_phase_crossover(.5, 0, True, .5, 1.5)
        middle, _ = stroke_phase_crossover(.5, .5, True, .5, 1.5)
        end, end_name = stroke_phase_crossover(.5, 1, True, .5, 1.5)
        self.assertEqual((start_name, end_name), ("accelerating", "decelerating"))
        self.assertAlmostEqual(start, .25)
        self.assertAlmostEqual(middle, .5)
        self.assertAlmostEqual(end, .75)
        values = [stroke_phase_crossover(.5, step / 100, True, .5, 1.5)[0]
                  for step in range(101)]
        self.assertTrue(all(.05 <= value <= 1 for value in values))
        self.assertTrue(all(a <= b for a, b in zip(values, values[1:])))

    def test_reversal_emphasis_is_symmetric_bounded_and_local(self):
        self.assertEqual(reversal_emphasis_envelope(0, .4), 1.0)
        self.assertEqual(reversal_emphasis_envelope(.4, .4), 0.0)
        self.assertEqual(reversal_emphasis_envelope(math.inf, .4), 0.0)
        self.assertAlmostEqual(reversal_emphasis_envelope(-.1, .4),
                               reversal_emphasis_envelope(.1, .4))
        values = [reversal_emphasis_envelope(step / 100, .4) for step in range(41)]
        self.assertTrue(all(0 <= value <= 1 for value in values))
        self.assertTrue(all(a >= b for a, b in zip(values, values[1:])))

    def test_reversal_boost_is_proportional_to_current_volume(self):
        self.assertAlmostEqual(proportional_reversal_boost(.40, 1.0, .20), .48)
        self.assertAlmostEqual(proportional_reversal_boost(.80, .5, .20), .88)
        self.assertEqual(proportional_reversal_boost(.95, 1.0, .20), 1.0)

    def test_spatial_curves_preserve_endpoints_and_centre(self):
        for curve in ("Linear", "S-curve", "Endpoint emphasis", "Centre emphasis"):
            self.assertEqual(spatial_response(0, curve), 0.0)
            self.assertEqual(spatial_response(.5, curve), .5)
            self.assertEqual(spatial_response(1, curve), 1.0)

    def test_spatial_curve_blend_and_character(self):
        self.assertEqual(spatial_response(.25, "Endpoint emphasis", 0), .25)
        self.assertLess(spatial_response(.25, "Endpoint emphasis", 1), .25)
        self.assertGreater(spatial_response(.25, "Centre emphasis", 1), .25)

    def test_spatial_curves_are_monotonic_and_bounded(self):
        for curve in ("Linear", "S-curve", "Endpoint emphasis", "Centre emphasis"):
            values = [spatial_response(step / 100, curve, .8) for step in range(101)]
            self.assertTrue(all(0 <= value <= 1 for value in values))
            self.assertTrue(all(a <= b for a, b in zip(values, values[1:])))

    def test_adaptive_crossover_uses_slow_and_fast_endpoints(self):
        self.assertEqual(adaptive_crossover_width(0, .9, .35), .9)
        self.assertEqual(adaptive_crossover_width(100, .9, .35), .35)
        self.assertAlmostEqual(adaptive_crossover_width(50, .9, .35), .625)

    def test_adaptive_crossover_is_bounded_and_monotonic(self):
        values = [adaptive_crossover_width(speed, .9, .35)
                  for speed in range(101)]
        self.assertTrue(all(.35 <= value <= .9 for value in values))
        self.assertTrue(all(a >= b for a, b in zip(values, values[1:])))

    def test_directional_profile_uses_forward_settings_when_disabled(self):
        profile = directional_crossover_profile(
            -1, .6, "Cosine", 1.2, False, .5, "Ease Out", .4)
        self.assertEqual(profile, (.6, "Cosine", 1.2, "forward"))

    def test_directional_profile_selects_reverse_texture(self):
        profile = directional_crossover_profile(
            -1, .6, "Cosine", 1.2, True, .5, "Ease Out", .4)
        self.assertEqual(profile, (.3, "Ease Out", .4, "reverse"))
        forward = directional_crossover_profile(
            1, .6, "Cosine", 1.2, True, .5, "Ease Out", .4)
        self.assertEqual(forward, (.6, "Cosine", 1.2, "forward"))

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

    def test_pair_morph_preserves_energy_and_has_exact_swapped_endpoints(self):
        values = (.1, .2, .3, .4)
        self.assertEqual(pair_morph(values, 0), values)
        self.assertEqual(pair_morph(values, 1), (.2, .1, .4, .3))
        midpoint = pair_morph(values, .5)
        self.assertGreater(max(midpoint) - min(midpoint), 0.0)

    def test_pair_morph_does_not_stop_when_only_one_pair_is_active(self):
        values = (1.0, 0.0, .5, .5)
        for step in range(101):
            morphed = pair_morph(values, step / 100.0)
            self.assertGreater(max(morphed) - min(morphed), .49)

    def test_pair_swapped_sequence_names_match_full_morph_destination(self):
        self.assertEqual(pair_swapped_order("ABCD"), "BADC")
        self.assertEqual(pair_swapped_order("ABDC"), "BACD")
        self.assertEqual(pair_swapped_order("BACD"), "ABDC")
        self.assertEqual(pair_swapped_order("ACBD"), "BDAC")

    def test_automatic_sequence_carousel_uses_requested_order(self):
        expected = (("ABCD", "ABDC"), ("ABDC", "BACD"),
                    ("BACD", "ACBD"), ("ACBD", "ABCD"))
        for index, pair in enumerate(expected):
            source, destination, amount = sequence_cycle_stage("ABCD", index / 4)
            self.assertEqual((source, destination), pair)
            self.assertEqual(amount, 0.0)

    def test_sequence_carousel_holds_then_transitions_briefly(self):
        source, destination, amount = sequence_cycle_stage("ABCD", .20, .10)
        self.assertEqual((source, destination), ("ABCD", "ABDC"))
        self.assertEqual(amount, 0.0)
        _, _, amount = sequence_cycle_stage("ABCD", .24, .10)
        self.assertGreater(amount, 0.0)
        self.assertLess(amount, 1.0)

    def test_sequence_carousel_is_continuous_at_stage_boundaries(self):
        values = (1.0, .2, .6, 0.0)
        for boundary in (.25, .5, .75):
            before_source, before_target, before_amount = sequence_cycle_stage(
                "ABCD", boundary - 1e-8)
            after_source, after_target, after_amount = sequence_cycle_stage(
                "ABCD", boundary + 1e-8)
            before = morph_electrode_order(
                values, before_source, before_target, before_amount)
            after = morph_electrode_order(
                values, after_source, after_target, after_amount)
            self.assertLess(max(abs(a - b) for a, b in zip(before, after)), 1e-5)

    def test_sequence_carousel_preserves_differential(self):
        values = (1.0, 0.0, .5, .5)
        for step in range(401):
            source, destination, amount = sequence_cycle_stage("ABCD", step / 401)
            morphed = morph_electrode_order(values, source, destination, amount)
            self.assertGreater(max(morphed) - min(morphed), .49)


if __name__ == "__main__":
    unittest.main()
