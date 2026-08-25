import unittest

from vector1a.tcode import TCodeCommand
from vector1a.timeline import (
    MediaTimeline,
    RAMP_CURVE_NAMES,
    TIMELINE_FRESHNESS_SECONDS,
    TIMELINE_HOLD_SECONDS,
    TIMELINE_SCALE_SECONDS,
    TIMELINE_TCODE_DIGITS,
    decode_timeline_seconds,
    media_volume_gain,
    ramp_curve,
)


class TimelineDecodeTests(unittest.TestCase):
    def test_decode_round_trip_scale(self):
        self.assertEqual(TIMELINE_TCODE_DIGITS, 5)
        self.assertAlmostEqual(decode_timeline_seconds(0.0), 0.0)
        self.assertAlmostEqual(decode_timeline_seconds(1.0), TIMELINE_SCALE_SECONDS)
        self.assertAlmostEqual(decode_timeline_seconds(0.1234), 1234.0)

    def test_snapshot_decodes_absolute_seconds_and_progress(self):
        timeline = MediaTimeline(clock=lambda: 0.0)
        # 1250 s position, 5000 s duration on scale 10000
        timeline.receive(TCodeCommand("T0", 0.1250), 1.0)
        timeline.receive(TCodeCommand("T1", 0.5000), 1.0)
        state = timeline.snapshot(1.0)
        self.assertTrue(state.fresh)
        self.assertAlmostEqual(state.position_s, 1250.0)
        self.assertAlmostEqual(state.duration_s, 5000.0)
        self.assertAlmostEqual(state.progress, 0.25)
        self.assertEqual(state.position_ms, 1_250_000)

    def test_missing_duration_yields_no_progress(self):
        timeline = MediaTimeline(clock=lambda: 0.0)
        timeline.receive(TCodeCommand("T0", 0.1), 1.0)
        state = timeline.snapshot(1.0)
        self.assertTrue(state.fresh)
        self.assertAlmostEqual(state.position_s, 1000.0)
        self.assertIsNone(state.duration_s)
        self.assertIsNone(state.progress)

    def test_seek_jumps_on_delayed_timeline(self):
        timeline = MediaTimeline(clock=lambda: 10.0)
        timeline.receive(TCodeCommand("T0", 0.0100), 5.0)  # 100 s
        timeline.receive(TCodeCommand("T1", 0.2000), 5.0)  # 2000 s
        timeline.receive(TCodeCommand("T0", 0.1500), 5.5)  # seek to 1500 s
        early = timeline.snapshot(5.2)
        self.assertAlmostEqual(early.position_s, 100.0)
        late = timeline.snapshot(5.5)
        self.assertAlmostEqual(late.position_s, 1500.0)

    def test_stale_position_is_not_fresh(self):
        timeline = MediaTimeline(clock=lambda: 0.0)
        timeline.receive(TCodeCommand("T0", 0.1), 1.0)
        timeline.receive(TCodeCommand("T1", 0.5), 1.0)
        state = timeline.snapshot(3.0, freshness_seconds=1.0, hold_seconds=1.0)
        self.assertFalse(state.fresh)
        self.assertFalse(state.held)
        self.assertFalse(state.usable)
        self.assertIsNone(state.progress)

    def test_five_digit_position_resolves_to_tenth_second(self):
        timeline = MediaTimeline(clock=lambda: 0.0)
        # 125.3 s position, 5000 s duration — 5-digit wire value 01253
        timeline.receive(TCodeCommand("T0", 0.01253), 1.0)
        timeline.receive(TCodeCommand("T1", 0.5000), 1.0)
        state = timeline.snapshot(1.0)
        self.assertAlmostEqual(state.position_s, 125.3, places=1)
        self.assertAlmostEqual(state.progress, 125.3 / 5000.0, places=4)

    def test_sparse_t0_updates_remain_fresh_within_default_window(self):
        # 5-digit T0 at scale 10000 can change every ~0.1 media-second; gaps
        # longer than freshness should still read as live within the window.
        timeline = MediaTimeline(clock=lambda: 0.0)
        timeline.receive(TCodeCommand("T0", 0.0414), 1.0)
        timeline.receive(TCodeCommand("T1", 0.5000), 1.0)
        state = timeline.snapshot(1.0 + TIMELINE_FRESHNESS_SECONDS)
        self.assertTrue(state.fresh)
        self.assertAlmostEqual(state.progress, 0.0414 / 0.5)

    def test_brief_gap_holds_progress_instead_of_clearing(self):
        timeline = MediaTimeline(clock=lambda: 0.0)
        timeline.receive(TCodeCommand("T0", 0.2500), 1.0)
        timeline.receive(TCodeCommand("T1", 0.5000), 1.0)
        held_at = 1.0 + TIMELINE_FRESHNESS_SECONDS + 0.5
        self.assertLess(held_at - 1.0, TIMELINE_HOLD_SECONDS)
        state = timeline.snapshot(held_at)
        self.assertFalse(state.fresh)
        self.assertTrue(state.held)
        self.assertTrue(state.usable)
        self.assertAlmostEqual(state.progress, 0.5)
        gain = media_volume_gain(
            state.progress, 0.4, 1.0, "Linear")
        self.assertAlmostEqual(gain, 0.7)

    def test_past_hold_clears_progress_for_floor_fallback(self):
        timeline = MediaTimeline(clock=lambda: 0.0)
        timeline.receive(TCodeCommand("T0", 0.2500), 1.0)
        timeline.receive(TCodeCommand("T1", 0.5000), 1.0)
        state = timeline.snapshot(1.0 + TIMELINE_HOLD_SECONDS + 0.1)
        self.assertFalse(state.usable)
        self.assertIsNone(state.progress)
        self.assertAlmostEqual(media_volume_gain(state.progress, 0.4, 1.0), 0.4)

    def test_non_timeline_axes_are_ignored(self):
        timeline = MediaTimeline(clock=lambda: 0.0)
        timeline.receive(TCodeCommand("L0", 0.5), 1.0)
        timeline.receive(TCodeCommand("V0", 0.7), 1.0)
        state = timeline.snapshot(1.0)
        self.assertIsNone(state.position_s)
        self.assertFalse(state.fresh)
        self.assertFalse(state.usable)


class MediaVolumeRampTests(unittest.TestCase):
    def test_linear_floor_ceiling(self):
        self.assertAlmostEqual(media_volume_gain(0.0, 0.4, 1.0, "Linear"), 0.4)
        self.assertAlmostEqual(media_volume_gain(0.5, 0.4, 1.0, "Linear"), 0.7)
        self.assertAlmostEqual(media_volume_gain(1.0, 0.4, 1.0, "Linear"), 1.0)

    def test_missing_progress_uses_floor(self):
        self.assertAlmostEqual(media_volume_gain(None, 0.4, 1.0, "Linear"), 0.4)

    def test_ceiling_not_below_floor(self):
        self.assertAlmostEqual(media_volume_gain(1.0, 0.6, 0.2, "Linear"), 0.6)

    def test_named_curves_preserve_endpoints_and_are_monotonic(self):
        for name in RAMP_CURVE_NAMES:
            self.assertAlmostEqual(ramp_curve(0.0, name), 0.0)
            self.assertAlmostEqual(ramp_curve(1.0, name), 1.0)
            values = [ramp_curve(step / 100.0, name) for step in range(101)]
            for left, right in zip(values, values[1:]):
                self.assertLessEqual(left, right + 1e-12)
            self.assertTrue(all(0.0 <= value <= 1.0 for value in values))

    def test_unknown_curve_falls_back_to_linear(self):
        self.assertAlmostEqual(ramp_curve(0.3, "nope"), 0.3)

    def test_exponential_rises_late_relative_to_linear(self):
        self.assertLess(ramp_curve(0.5, "Exponential"), ramp_curve(0.5, "Linear"))

    def test_logarithmic_rises_early_relative_to_linear(self):
        self.assertGreater(ramp_curve(0.5, "Logarithmic"), ramp_curve(0.5, "Linear"))

    def test_power2_is_quadratic(self):
        self.assertAlmostEqual(ramp_curve(0.5, "Power2"), 0.25)

    def test_late_kick_stays_soft_then_climbs(self):
        self.assertLess(ramp_curve(0.5, "Late Kick"), 0.15)
        self.assertGreater(ramp_curve(0.9, "Late Kick"), ramp_curve(0.75, "Late Kick"))

    def test_plateau_rise_holds_then_climbs(self):
        self.assertLess(ramp_curve(0.2, "Plateau Rise"), 0.06)
        self.assertGreater(ramp_curve(0.8, "Plateau Rise"), ramp_curve(0.4, "Plateau Rise"))


if __name__ == "__main__":
    unittest.main()
