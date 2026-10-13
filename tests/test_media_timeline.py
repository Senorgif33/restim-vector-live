import unittest

from vector1a.media_timeline import (
    EXTRA_RAMP_LEVEL_KEYS,
    VECTOR_RAMP_FUNSCRIPT_META_KEY,
    RAMP_CURVE_NAMES,
    RampWaypoint,
    bake_ramp_funscript_actions,
    export_ramp_funscript,
    export_ramp_waypoints_payload,
    format_media_time,
    format_ofs_time,
    format_ramp_bookmark_name,
    import_ramp_funscript,
    import_ramp_waypoints_payload,
    media_volume_gain,
    media_volume_gain_waypoints,
    normalize_level_key,
    normalize_waypoints,
    parse_media_time,
    parse_ofs_time,
    parse_ramp_bookmark_name,
    ramp_curve,
    waypoint_levels_used,
)


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


class MediaTimeParseTests(unittest.TestCase):
    def test_parse_plain_seconds_and_clock(self):
        self.assertAlmostEqual(parse_media_time("90"), 90.0)
        self.assertAlmostEqual(parse_media_time("1:30"), 90.0)
        self.assertAlmostEqual(parse_media_time("1:02:03"), 3723.0)

    def test_format_media_time(self):
        self.assertEqual(format_media_time(90), "1:30")
        self.assertEqual(format_media_time(3723), "1:02:03")

    def test_parse_rejects_empty(self):
        with self.assertRaises(ValueError):
            parse_media_time("")


class MediaVolumeWaypointTests(unittest.TestCase):
    def test_hold_after_last_waypoint(self):
        points = [
            RampWaypoint(0.0, "floor1"),
            RampWaypoint(100.0, "ceiling1"),
        ]
        # Past last waypoint: hold Ceiling 1
        self.assertAlmostEqual(
            media_volume_gain_waypoints(
                150.0, points, 0.4, 1.0, 0.35, 0.8, 0.3, 0.7, "Linear"),
            1.0)

    def test_multi_ceiling_dip_between_highs(self):
        points = [
            RampWaypoint(0.0, "floor1"),
            RampWaypoint(100.0, "ceiling1"),
            RampWaypoint(120.0, "floor1"),
            RampWaypoint(200.0, "ceiling2"),
        ]
        mid_gap = media_volume_gain_waypoints(
            120.0, points, 0.4, 1.0, 0.35, 0.85, 0.3, 0.7, "Linear")
        self.assertAlmostEqual(mid_gap, 0.4)
        second_peak = media_volume_gain_waypoints(
            200.0, points, 0.4, 1.0, 0.35, 0.85, 0.3, 0.7, "Linear")
        self.assertAlmostEqual(second_peak, 0.85)
        between_rise = media_volume_gain_waypoints(
            160.0, points, 0.4, 1.0, 0.35, 0.85, 0.3, 0.7, "Linear")
        self.assertGreater(between_rise, 0.4)
        self.assertLess(between_rise, 0.85)

    def test_mid_list_floor_between_ceilings(self):
        """Floor between ceilings stays chronological (sort by time only)."""
        points = normalize_waypoints([
            RampWaypoint(0.0, "floor1"),
            RampWaypoint(200.0, "ceiling2"),
            RampWaypoint(100.0, "ceiling1"),
            RampWaypoint(150.0, "floor2"),
        ])
        self.assertEqual(
            [point.level for point in points],
            ["floor1", "ceiling1", "floor2", "ceiling2"])
        gap = media_volume_gain_waypoints(
            150.0, points, 0.4, 1.0, 0.25, 0.9, 0.2, 0.8, "Linear")
        self.assertAlmostEqual(gap, 0.25)

    def test_missing_position_uses_floor(self):
        points = [RampWaypoint(0.0, "floor1"), RampWaypoint(10.0, "ceiling1")]
        self.assertAlmostEqual(
            media_volume_gain_waypoints(None, points, 0.4, 1.0), 0.4)

    def test_empty_waypoints_use_floor(self):
        self.assertAlmostEqual(
            media_volume_gain_waypoints(50.0, [], 0.4, 1.0), 0.4)

    def test_normalize_drops_malformed_and_sorts_by_time_only(self):
        points = normalize_waypoints([
            {"time_s": 30, "level": "ceiling2"},
            {"time_s": -1, "level": "floor1"},
            {"bad": True},
            {"time_s": 10, "level": "floor"},
            {"time_s": 30, "level": "floor2"},
        ])
        self.assertEqual([point.time_s for point in points], [10.0, 30.0, 30.0])
        self.assertEqual(points[0].level, "floor1")  # legacy floor ΓåÆ floor1
        # Same-time order is stable (ceiling2 before floor2 as inputted).
        self.assertEqual(points[1].level, "ceiling2")
        self.assertEqual(points[2].level, "floor2")

    def test_legacy_floor_normalizes_to_floor1(self):
        self.assertEqual(normalize_level_key("floor"), "floor1")
        self.assertEqual(normalize_level_key("Floor 2"), "floor2")

    def test_waypoint_levels_used_for_progressive_ui(self):
        used = waypoint_levels_used([
            RampWaypoint(0.0, "floor1"),
            RampWaypoint(10.0, "ceiling1"),
            RampWaypoint(20.0, "floor2"),
            RampWaypoint(30.0, "ceiling3"),
        ])
        self.assertEqual(used, frozenset({"floor1", "ceiling1", "floor2", "ceiling3"}))
        self.assertTrue(set(EXTRA_RAMP_LEVEL_KEYS) - used)

    def test_export_import_round_trip(self):
        points = [
            RampWaypoint(0.0, "floor1", "Linear"),
            RampWaypoint(90.0, "ceiling1", "Power2"),
            RampWaypoint(120.0, "floor2", "Smoothstep"),
            RampWaypoint(180.0, "ceiling2", "Late Kick"),
        ]
        payload = export_ramp_waypoints_payload(
            points, 0.4, 0.3, 0.2, 1.0, 0.85, 0.7, "Power2")
        restored, settings = import_ramp_waypoints_payload(payload)
        self.assertEqual(
            [(p.time_s, p.level, p.curve) for p in restored],
            [(p.time_s, p.level, p.curve) for p in points])
        self.assertAlmostEqual(settings["floor1"], 0.4)
        self.assertAlmostEqual(settings["floor2"], 0.3)
        self.assertAlmostEqual(settings["ceiling2"], 0.85)
        self.assertEqual(settings["curve"], "Power2")

    def test_import_legacy_floor_key(self):
        restored, settings = import_ramp_waypoints_payload({
            "waypoints": [{"time_s": 0, "level": "floor"}],
            "floor": 0.55,
            "ceiling1": 0.9,
            "curve": "Linear",
        })
        self.assertEqual(restored[0].level, "floor1")
        self.assertEqual(restored[0].curve, "Linear")
        self.assertAlmostEqual(settings["floor1"], 0.55)

    def test_segment_uses_curve_shape(self):
        points = [RampWaypoint(0.0, "floor1"), RampWaypoint(100.0, "ceiling1")]
        linear_mid = media_volume_gain_waypoints(
            50.0, points, 0.0, 1.0, curve="Linear")
        power_mid = media_volume_gain_waypoints(
            50.0, points, 0.0, 1.0, curve="Power2")
        self.assertAlmostEqual(linear_mid, 0.5)
        self.assertAlmostEqual(power_mid, 0.25)

    def test_per_waypoint_segment_curves(self):
        """Destination waypoint curve shapes AΓåÆB; global is only a fallback."""
        points = [
            RampWaypoint(0.0, "floor1", "Linear"),
            RampWaypoint(100.0, "ceiling1", "Power2"),
            RampWaypoint(200.0, "floor1", "Linear"),
        ]
        # Mid first segment: uses ceiling1's Power2, not global Linear.
        first_mid = media_volume_gain_waypoints(
            50.0, points, 0.0, 1.0, curve="Linear")
        self.assertAlmostEqual(first_mid, 0.25)
        # Mid second segment: uses floor1's Linear at 200s.
        second_mid = media_volume_gain_waypoints(
            150.0, points, 0.0, 1.0, curve="Power2")
        self.assertAlmostEqual(second_mid, 0.5)

    def test_legacy_waypoint_without_curve_uses_default(self):
        points = normalize_waypoints(
            [{"time_s": 0, "level": "floor1"},
             {"time_s": 100, "level": "ceiling1"}],
            default_curve="Power2")
        self.assertEqual(points[1].curve, "Power2")
        mid = media_volume_gain_waypoints(50.0, points, 0.0, 1.0, curve="Linear")
        self.assertAlmostEqual(mid, 0.25)

    def test_ofs_time_round_trip(self):
        self.assertEqual(format_ofs_time(0), "00:00:00.000")
        self.assertEqual(format_ofs_time(3723.456), "01:02:03.456")
        self.assertAlmostEqual(parse_ofs_time("01:02:03.456"), 3723.456, places=3)
        self.assertAlmostEqual(parse_ofs_time("1:30.5"), 90.5)

    def test_bookmark_name_parse(self):
        self.assertEqual(
            parse_ramp_bookmark_name("Ceiling 2 | Power2"),
            ("ceiling2", "Power2"))
        self.assertEqual(parse_ramp_bookmark_name("Floor 1"), ("floor1", None))
        self.assertIsNone(parse_ramp_bookmark_name("Intro"))
        self.assertEqual(
            format_ramp_bookmark_name("ceiling1", "Late Kick"),
            "Ceiling 1 | Late Kick")

    def test_funscript_export_import_round_trip(self):
        points = [
            RampWaypoint(0.0, "floor1", "Linear"),
            RampWaypoint(10.0, "ceiling1", "Power2"),
            RampWaypoint(20.0, "floor2", "Smoothstep"),
        ]
        script = export_ramp_funscript(
            points, 0.4, 0.25, 0.2, 1.0, 0.85, 0.7, "Linear", end_s=25.0, step_s=0.5)
        self.assertIn("actions", script)
        self.assertGreater(len(script["actions"]), 5)
        self.assertEqual(script["actions"][0]["pos"], 40)
        meta = script["metadata"][VECTOR_RAMP_FUNSCRIPT_META_KEY]
        self.assertEqual(len(meta["waypoints"]), 3)
        self.assertEqual(len(script["metadata"]["bookmarks"]), 3)
        restored, settings = import_ramp_funscript(script)
        self.assertEqual(
            [(p.time_s, p.level, p.curve) for p in restored],
            [(p.time_s, p.level, p.curve) for p in points])
        self.assertAlmostEqual(settings["floor2"], 0.25)
        # Baked mid-segment: Power2 at u=0.5 ΓåÆ 0.4 + 0.6*0.25 = 0.55.
        mid_action = next(a for a in script["actions"] if a["at"] == 5000)
        self.assertEqual(mid_action["pos"], 55)

    def test_funscript_import_from_bookmarks_only(self):
        script = {
            "actions": [{"at": 0, "pos": 40}, {"at": 10000, "pos": 100}],
            "metadata": {
                "bookmarks": [
                    {"name": "Floor 1 | Linear", "time": "00:00:00.000"},
                    {"name": "Ceiling 1 | Power2", "time": "00:00:10.000"},
                ],
            },
        }
        restored, settings = import_ramp_funscript(script)
        self.assertEqual(
            [(p.time_s, p.level, p.curve) for p in restored],
            [(0.0, "floor1", "Linear"), (10.0, "ceiling1", "Power2")])
        self.assertTrue(settings.get("gains_from_bookmarks_only"))

    def test_funscript_actions_only_rejected(self):
        with self.assertRaises(ValueError):
            import_ramp_funscript({"actions": [{"at": 0, "pos": 50}]})

    def test_bake_includes_hold_after_last(self):
        points = [RampWaypoint(0.0, "floor1"), RampWaypoint(5.0, "ceiling1")]
        actions = bake_ramp_funscript_actions(
            points, 0.0, 1.0, end_s=8.0, step_s=1.0, curve="Linear")
        last = actions[-1]
        self.assertEqual(last["at"], 8000)
        self.assertEqual(last["pos"], 100)


if __name__ == "__main__":
    unittest.main()
