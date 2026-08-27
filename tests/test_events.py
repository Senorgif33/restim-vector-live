"""Tests for live custom events."""
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from vector1a.events import (
    EventEngine,
    EventError,
    _derive_orgasm_countdown_params,
    apply_linear_change,
    apply_modulation,
    expand_named_event,
    expand_user_events,
    normalize_value,
    parse_yaml_subset,
)
from vector1a.tcode import TCodeCommand
from vector1a.timeline import MediaTimeline, media_volume_gain


DEFINITIONS_YAML = """
normalization:
  pulse_frequency:
    max: 120.0
  pulse_width:
    max: 100.0
  frequency:
    max: 1200.0
  volume:
    max: 1.0
  sensor_suppression:
    max: 100.0
definitions:
  volume_boost:
    default_params:
      duration_ms: 1000
      amount: 0.2
      ramp_ms: 0
    steps:
      - operation: apply_linear_change
        axis: volume
        params:
          start_value: $amount
          end_value: $amount
          duration_ms: $duration_ms
          ramp_in_ms: $ramp_ms
          mode: additive
  dual_with_alpha:
    default_params:
      duration_ms: 500
      boost: 0.1
    steps:
      - operation: apply_linear_change
        axis: volume,volume-prostate
        params:
          start_value: $boost
          end_value: $boost
          duration_ms: $duration_ms
          mode: additive
      - operation: apply_modulation
        axis: alpha
        params:
          waveform: sin
          frequency: 1.0
          amplitude: 0.2
          duration_ms: $duration_ms
          mode: additive
  pulse_overwrite:
    default_params:
      duration_ms: 1000
      rate: 60
    steps:
      - operation: apply_linear_change
        axis: pulse_frequency
        params:
          start_value: $rate
          end_value: $rate
          duration_ms: $duration_ms
          mode: overwrite
  alpha_boost:
    default_params:
      duration_ms: 1000
      amount: 0.2
      ramp_ms: 0
    steps:
      - operation: apply_linear_change
        axis: alpha
        params:
          start_value: $amount
          end_value: $amount
          duration_ms: $duration_ms
          ramp_in_ms: $ramp_ms
          mode: additive
  e1_square:
    default_params:
      duration_ms: 1000
      switch_freq: 1.0
      ramp_ms: 0
    steps:
      - operation: apply_modulation
        axis: e1
        params:
          waveform: square
          frequency: $switch_freq
          duty_cycle: 0.5
          amplitude: 0.5
          max_level_offset: 0.5
          phase: 0
          duration_ms: $duration_ms
          ramp_in_ms: $ramp_ms
          mode: overwrite
  suppress_edge:
    default_params:
      duration_ms: 1000
      ramp_ms: 0
    steps:
      - operation: apply_linear_change
        axis: sensor_suppression
        params:
          start_value: 50
          end_value: 50
          duration_ms: $duration_ms
          ramp_in_ms: $ramp_ms
          mode: overwrite
  suppress_cum:
    default_params:
      duration_ms: 1000
      ramp_ms: 0
    steps:
      - operation: apply_linear_change
        axis: sensor_suppression
        params:
          start_value: 100
          end_value: 100
          duration_ms: $duration_ms
          ramp_in_ms: $ramp_ms
          mode: overwrite
  buzz:
    default_params:
      duration_ms: 1000
      buzz_freq: 2.0
      buzz_amp: 0.1
      offset: 0.0
    steps:
      - operation: apply_modulation
        axis: volume
        params:
          waveform: sin
          frequency: $buzz_freq
          amplitude: $buzz_amp
          max_level_offset: $offset
          duration_ms: $duration_ms
          mode: additive
"""


class YamlSubsetTests(unittest.TestCase):
    def test_parse_maps_lists_scalars_comments(self):
        data = parse_yaml_subset("""
# comment
events:
  - time: 1000
    name: volume_boost
    params:
      amount: 0.25
""")
        self.assertEqual(data["events"][0]["time"], 1000)
        self.assertEqual(data["events"][0]["params"]["amount"], 0.25)


class ExpandAndNormalizeTests(unittest.TestCase):
    def setUp(self):
        parsed = parse_yaml_subset(DEFINITIONS_YAML)
        self.definitions = parsed["definitions"]
        self.normalization = parsed["normalization"]

    def test_token_merge_includes_alpha_without_warning(self):
        user = parse_yaml_subset("""
events:
  - time: 0
    name: dual_with_alpha
    params:
      boost: 0.15
""")
        loaded = expand_user_events(user, self.definitions)
        axes = {step.axis for step in loaded.steps}
        self.assertEqual(axes, {"volume", "volume-prostate", "alpha"})
        self.assertFalse(any("alpha" in warning for warning in loaded.warnings))
        volume_step = next(step for step in loaded.steps if step.axis == "volume")
        self.assertEqual(volume_step.params["start_value"], 0.15)

    def test_normalization_pulse_frequency(self):
        self.assertAlmostEqual(
            normalize_value("pulse_frequency", 40.0, self.normalization),
            40.0 / 120.0)

    def test_normalization_passthrough_unit_interval(self):
        self.assertAlmostEqual(
            normalize_value("pulse_frequency", 0.5, self.normalization), 0.5)


class LinearAndModulationTests(unittest.TestCase):
    def test_linear_additive_and_overwrite_with_ramp(self):
        mid = apply_linear_change(
            0.5, 500, 0, 1000, 0.2, 0.2, 0, 0, "additive", "volume")
        self.assertAlmostEqual(mid, 0.7)
        over = apply_linear_change(
            0.5, 500, 0, 1000, 0.8, 0.8, 0, 0, "overwrite", "volume")
        self.assertAlmostEqual(over, 0.8)
        ramp_start = apply_linear_change(
            0.5, 0, 0, 1000, 0.2, 0.2, 500, 0, "additive", "volume")
        self.assertAlmostEqual(ramp_start, 0.5)
        ramp_mid = apply_linear_change(
            0.5, 250, 0, 1000, 0.2, 0.2, 500, 0, "additive", "volume")
        self.assertAlmostEqual(ramp_mid, 0.6)

    def test_modulation_sin_endpoints_and_mid(self):
        start = apply_modulation(
            0.5, 0, 0, 10000, "sin", 1.0, 0.1, 0.0, 0.0, 0, 0, "additive")
        self.assertAlmostEqual(start, 0.5)
        quarter = apply_modulation(
            0.5, 250, 0, 10000, "sin", 1.0, 0.1, 0.0, 0.0, 0, 0, "additive")
        self.assertAlmostEqual(quarter, 0.6)


class EventEngineTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        root = Path(self._tmpdir.name)
        self.defs_path = root / "defs.yml"
        self.defs_path.write_text(DEFINITIONS_YAML, encoding="utf-8")
        self.events_path = root / "clip.events.yml"
        self.events_path.write_text(
            "events:\n  - time: 1000\n    name: volume_boost\n",
            encoding="utf-8",
        )
        self.engine = EventEngine(self.defs_path)
        self.engine.load_events_file(self.events_path)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_inactive_when_position_ms_none(self):
        values = {"volume": 0.4, "volume-prostate": 0.4}
        self.assertEqual(self.engine.apply(None, values), values)

    def test_applies_inside_window(self):
        result = self.engine.apply(1500, {"volume": 0.4, "volume-prostate": 0.4})
        self.assertAlmostEqual(result["volume"], 0.6)

    def test_ramp_then_event_volume(self):
        # Media ramp gain at 25% progress with floor 0.4 ceiling 1.0 => 0.55
        gain = media_volume_gain(0.25, 0.4, 1.0, "Linear")
        self.assertAlmostEqual(gain, 0.55)
        post_ramp = min(1.0, max(0.0, 0.8 * gain))
        result = self.engine.apply(1500, {"volume": post_ramp})
        self.assertAlmostEqual(result["volume"], post_ramp + 0.2)

    def test_pulse_overwrite_uses_normalization(self):
        path = Path(self._tmpdir.name) / "pulse.events.yml"
        path.write_text(
            "events:\n  - time: 0\n    name: pulse_overwrite\n",
            encoding="utf-8",
        )
        self.engine.load_events_file(path)
        result = self.engine.apply(100, {"pulse_frequency": 0.2})
        self.assertAlmostEqual(result["pulse_frequency"], 60.0 / 120.0)

    def test_bundled_definitions_load(self):
        engine = EventEngine()
        self.assertGreater(len(engine.definitions), 10)
        self.assertIn("pulse_frequency", engine.normalization)

    def test_send_time_timeline_position_selects_later_media_ms(self):
        # Queue sample is calculated ~lookahead before send. Event at 2000 ms
        # media is inactive at calculate-time position and active at due_at.
        lookahead = 2.0
        calculated_at = 5.0
        due_at = calculated_at + lookahead
        timeline = MediaTimeline(clock=lambda: 10.0)
        # Media at 1.0 s when sample is calculated; 2.5 s when it is sent.
        timeline.receive(TCodeCommand("T0", 0.0001), calculated_at)  # 1 s
        timeline.receive(TCodeCommand("T1", 0.5000), calculated_at)
        timeline.receive(TCodeCommand("T0", 0.00025), due_at)  # 2.5 s
        calc_ms = timeline.snapshot(calculated_at).position_ms
        send_ms = timeline.snapshot(due_at).position_ms
        self.assertEqual(calc_ms, 1000)
        self.assertEqual(send_ms, 2500)

        path = Path(self._tmpdir.name) / "send_clock.events.yml"
        path.write_text(
            "events:\n  - time: 2000\n    name: volume_boost\n",
            encoding="utf-8",
        )
        self.engine.load_events_file(path)
        base = {"volume": 0.4, "volume-prostate": 0.4}
        at_calc = self.engine.apply(calc_ms, base)
        at_send = self.engine.apply(send_ms, base)
        self.assertAlmostEqual(at_calc["volume"], 0.4)
        self.assertAlmostEqual(at_send["volume"], 0.6)

    def test_alpha_additive_inside_window(self):
        path = Path(self._tmpdir.name) / "alpha.events.yml"
        path.write_text(
            "events:\n  - time: 1000\n    name: alpha_boost\n",
            encoding="utf-8",
        )
        self.engine.load_events_file(path)
        result = self.engine.apply(1500, {"alpha": 0.4})
        self.assertAlmostEqual(result["alpha"], 0.6)

    def test_e1_modulation_overwrites_base(self):
        path = Path(self._tmpdir.name) / "e1.events.yml"
        path.write_text(
            "events:\n  - time: 0\n    name: e1_square\n",
            encoding="utf-8",
        )
        self.engine.load_events_file(path)
        base = 0.5
        inside = self.engine.apply(250, {"e1": base})
        outside = self.engine.apply(2000, {"e1": base})
        self.assertNotAlmostEqual(inside["e1"], base)
        self.assertAlmostEqual(outside["e1"], base)

    def test_sensor_suppression_accepted_and_overwrites(self):
        path = Path(self._tmpdir.name) / "suppress.events.yml"
        path.write_text(
            "events:\n"
            "  - time: 0\n    name: suppress_edge\n"
            "  - time: 2000\n    name: suppress_cum\n",
            encoding="utf-8",
        )
        self.engine.load_events_file(path)
        self.assertFalse(self.engine.warnings)
        baseline = {"sensor_suppression": 0.0}
        self.assertAlmostEqual(
            self.engine.apply(500, baseline)["sensor_suppression"], 0.5)
        self.assertAlmostEqual(
            self.engine.apply(2500, baseline)["sensor_suppression"], 1.0)
        self.assertAlmostEqual(
            self.engine.apply(5000, baseline)["sensor_suppression"], 0.0)

    def test_sensor_suppression_seeds_from_authored_baseline(self):
        path = Path(self._tmpdir.name) / "suppress_authored.events.yml"
        path.write_text(
            "events:\n  - time: 0\n    name: suppress_edge\n",
            encoding="utf-8",
        )
        self.engine.load_events_file(path)
        # Outside the event window the authored seed is preserved.
        outside = self.engine.apply(5000, {"sensor_suppression": 0.8})
        self.assertAlmostEqual(outside["sensor_suppression"], 0.8)
        inside = self.engine.apply(500, {"sensor_suppression": 0.8})
        self.assertAlmostEqual(inside["sensor_suppression"], 0.5)

    def test_bundled_edge_and_cum_include_sensor_suppression(self):
        engine = EventEngine()
        for name in ("edge", "cum", "CH_edge", "CH_cum_tease",
                     "mcb_edge", "mcb_edge_ce", "clutch_edge", "ruin"):
            definition = engine.definitions.get(name)
            self.assertIsNotNone(definition, name)
            axes = set()
            for step in definition.get("steps") or []:
                axis = str(step.get("axis", ""))
                axes.update(part.strip() for part in axis.split(","))
            self.assertIn("sensor_suppression", axes, name)
        # Spot-check policy values after expansion.
        edge = expand_user_events(
            {"events": [{"time": 0, "name": "edge"}]}, engine.definitions)
        cum = expand_user_events(
            {"events": [{"time": 0, "name": "cum"}]}, engine.definitions)
        ruin = expand_user_events(
            {"events": [{"time": 0, "name": "ruin"}]}, engine.definitions)
        stay = expand_user_events(
            {"events": [{"time": 0, "name": "stay"}]}, engine.definitions)
        edge_s = next(s for s in edge.steps if s.axis == "sensor_suppression")
        cum_s = next(s for s in cum.steps if s.axis == "sensor_suppression")
        ruin_s = next(s for s in ruin.steps if s.axis == "sensor_suppression")
        self.assertEqual(edge_s.params["start_value"], 50)
        self.assertEqual(cum_s.params["start_value"], 100)
        self.assertEqual(ruin_s.params["start_value"], 100)
        self.assertFalse(any(s.axis == "sensor_suppression" for s in stay.steps))

    def test_spatial_axes_clip_to_unit_interval(self):
        clipped = apply_linear_change(
            0.9, 500, 0, 1000, 0.5, 0.5, 0, 0, "additive", "e2")
        self.assertLessEqual(clipped, 1.0)
        self.assertGreaterEqual(clipped, 0.0)

    def test_schedule_trigger_before_activate_has_no_effect(self):
        self.engine.reload_definitions(self.defs_path)
        activate_at = 100.0
        self.assertTrue(self.engine.schedule_trigger(
            "volume_boost", {"duration_ms": 1000, "amount": 0.2}, activate_at))
        before = self.engine.apply_triggers(99.0, {"volume": 0.5})
        self.assertAlmostEqual(before["volume"], 0.5)
        during = self.engine.apply_triggers(100.5, {"volume": 0.5})
        self.assertAlmostEqual(during["volume"], 0.7)

    def test_schedule_trigger_without_file_or_media_position(self):
        self.engine.reload_definitions(self.defs_path)
        self.engine.clear()
        activate_at = 10.0
        self.engine.schedule_trigger(
            "volume_boost", {"duration_ms": 500, "amount": 0.25}, activate_at)
        # No file steps; apply with None position is a no-op for files.
        base = {"volume": 0.4}
        self.assertAlmostEqual(self.engine.apply(None, base)["volume"], 0.4)
        active = self.engine.apply_triggers(10.2, base)
        self.assertAlmostEqual(active["volume"], 0.65)

    def test_overlapping_triggers_stack_in_receive_order(self):
        self.engine.reload_definitions(self.defs_path)
        self.engine.schedule_trigger(
            "volume_boost", {"duration_ms": 1000, "amount": 0.1}, 50.0)
        self.engine.schedule_trigger(
            "volume_boost", {"duration_ms": 1000, "amount": 0.2}, 50.0)
        result = self.engine.apply_triggers(50.1, {"volume": 0.5})
        self.assertAlmostEqual(result["volume"], 0.8)

    def test_unknown_trigger_name_does_not_raise(self):
        self.engine.reload_definitions(self.defs_path)
        self.assertFalse(self.engine.schedule_trigger("no_such_event", {}, 1.0))
        self.assertEqual(self.engine.pending_trigger_count, 0)
        self.assertTrue(any("no_such_event" in w for w in self.engine.warnings))

    def test_scheduled_edge_mutes_sensor_suppression(self):
        engine = EventEngine()
        activate_at = 5.0
        self.assertTrue(engine.schedule_trigger(
            "edge", {"duration_ms": 2000, "ramp_up_ms": 0}, activate_at))
        seed = {"sensor_suppression": 0.0, "volume": 0.5}
        before = engine.apply_triggers(4.0, seed)
        self.assertAlmostEqual(before["sensor_suppression"], 0.0)
        during = engine.apply_triggers(5.1, seed)
        self.assertAlmostEqual(during["sensor_suppression"], 0.5)

    def test_status_line_shows_triggers_without_file(self):
        self.engine.reload_definitions(self.defs_path)
        self.engine.clear()
        self.engine.schedule_trigger(
            "volume_boost", {"duration_ms": 1000}, 20.0)
        line = self.engine.status_line(enabled=True, due_at=20.1)
        self.assertIn("triggers=", line)
        self.assertIn("volume_boost", line)


class TestOrgasmCountdownEvents(unittest.TestCase):
    def test_bundled_new_mcb_names_exist(self):
        engine = EventEngine()
        for name in ("mcb_goodboy", "mcb_orgasm_countdown",
                     "mcb_orgasm_countdown_stroke_override"):
            self.assertIn(name, engine.definitions)

    def test_default_countdown_expand_axes_and_durations(self):
        engine = EventEngine()
        steps, warnings = expand_named_event(
            "mcb_orgasm_countdown", None, engine.definitions)
        self.assertEqual(warnings, [])
        axes = {step.axis for step in steps}
        self.assertEqual(
            axes, {"pulse_frequency", "pulse_width", "volume", "volume-prostate"})
        self.assertFalse(any(step.axis in ("alpha", "beta") for step in steps))
        climax = [
            s for s in steps
            if s.axis == "volume" and s.start_time_ms == 21000
            and s.params.get("start_value") == 0.09
        ]
        self.assertEqual(len(climax), 1)
        self.assertEqual(climax[0].duration_ms, 25867)
        goodboy_pulse = [
            s for s in steps
            if s.axis == "pulse_frequency" and s.start_time_ms == 16567
        ]
        self.assertEqual(len(goodboy_pulse), 1)
        self.assertEqual(goodboy_pulse[0].duration_ms, 5000)

    def test_stroke_override_includes_alpha_beta_e(self):
        engine = EventEngine()
        steps, warnings = expand_named_event(
            "mcb_orgasm_countdown_stroke_override", None, engine.definitions)
        self.assertEqual(warnings, [])
        axes = {step.axis for step in steps}
        self.assertIn("alpha", axes)
        self.assertIn("beta", axes)
        self.assertTrue({"e1", "e2", "e3", "e4"}.issubset(axes))
        self.assertFalse(any(
            "prostate" in str(step.axis) and step.axis != "volume-prostate"
            for step in steps))

    def test_stretch_default_duration_unchanged(self):
        params = {
            "duration_ms": 46867,
            "orgasm_offset_ms": 21000,
            "ramp_ms": 1500,
            "seg_orgasm_ms": 999,
            "goodboy_duration_ms": 999,
        }
        _derive_orgasm_countdown_params(params, "mcb_orgasm_countdown")
        self.assertEqual(params["seg_orgasm_ms"], 25867)
        self.assertEqual(params["goodboy_duration_ms"], 5000)

    def test_stretch_double_climax_doubles_goodboy(self):
        params = {
            "duration_ms": 46867 + 25867,
            "orgasm_offset_ms": 21000,
            "ramp_ms": 1500,
        }
        _derive_orgasm_countdown_params(
            params, "mcb_orgasm_countdown_stroke_override")
        self.assertEqual(params["seg_orgasm_ms"], 51734)
        self.assertEqual(params["goodboy_duration_ms"], 10000)

    def test_stretch_short_climax_keeps_goodboy_min(self):
        params = {
            "duration_ms": 36000,
            "orgasm_offset_ms": 21000,
            "ramp_ms": 1500,
        }
        _derive_orgasm_countdown_params(params, "mcb_orgasm_countdown")
        self.assertEqual(params["seg_orgasm_ms"], 15000)
        self.assertEqual(params["goodboy_duration_ms"], 5000)

    def test_stretch_too_short_raises(self):
        params = {
            "duration_ms": 21000,
            "orgasm_offset_ms": 21000,
            "ramp_ms": 1500,
        }
        with self.assertRaises(EventError):
            _derive_orgasm_countdown_params(params, "mcb_orgasm_countdown")

    def test_stretch_other_events_untouched(self):
        params = {
            "duration_ms": 99999,
            "seg_orgasm_ms": 1,
            "goodboy_duration_ms": 2,
        }
        _derive_orgasm_countdown_params(params, "mcb_goodboy")
        self.assertEqual(params["seg_orgasm_ms"], 1)
        self.assertEqual(params["goodboy_duration_ms"], 2)

    def test_stretch_applies_through_expand(self):
        engine = EventEngine()
        steps, _ = expand_named_event(
            "mcb_orgasm_countdown",
            {"duration_ms": 46867 + 25867},
            engine.definitions,
        )
        climax = [
            s for s in steps
            if s.axis == "volume" and s.start_time_ms == 21000
            and s.params.get("start_value") == 0.09
        ]
        self.assertEqual(climax[0].duration_ms, 51734)
        goodboy = [
            s for s in steps
            if s.axis == "pulse_frequency" and s.start_time_ms == 16567
        ]
        self.assertEqual(goodboy[0].duration_ms, 10000)


if __name__ == "__main__":
    unittest.main()
