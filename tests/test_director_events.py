from collections import deque

from vector1a.app import VectorApp
from vector1a.events import load_director_event_policy


class _Flag:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class _Events:
    def __init__(self, accepted=True):
        self.accepted = accepted
        self.calls = []

    def schedule_trigger(self, event, params, activate_at):
        self.calls.append((event, params, activate_at))
        return self.accepted


def _app(enabled=True):
    app = object.__new__(VectorApp)
    app.director_events_enabled = _Flag(enabled)
    app.event_engine = _Events()
    app._director_event_history = deque(maxlen=12)
    app._events_last_s1 = None
    catalog, overrides, min_seconds, max_seconds = load_director_event_policy()
    app.DIRECTOR_EVENT_CATALOG = catalog
    app.DIRECTOR_EVENT_PARAM_OVERRIDES = overrides
    app.DIRECTOR_EVENT_MIN_SECONDS = min_seconds
    app.DIRECTOR_EVENT_MAX_SECONDS = max_seconds
    app._events_auto_load_note = None
    return app


def test_director_event_requires_operator_opt_in():
    status, response = _app(False)._trigger_director_event({"event": "mcb_tease"})
    assert status == 409
    assert response["ok"] is False


def test_director_event_rejects_excluded_and_unknown_and_unbounded_duration():
    app = _app()
    assert "tranquil" not in app.DIRECTOR_EVENT_CATALOG
    assert "test_slaps" not in app.DIRECTOR_EVENT_CATALOG
    assert "mcb_extract" in app.DIRECTOR_EVENT_CATALOG
    assert "clutch_tranquil" in app.DIRECTOR_EVENT_CATALOG
    assert app._trigger_director_event({"event": "tranquil"})[0] == 400
    assert app._trigger_director_event({"event": "test_slaps"})[0] == 400
    assert app._trigger_director_event({"event": "no_such_event"})[0] == 400
    assert app._trigger_director_event(
        {"event": "mcb_tease", "duration_seconds": app.DIRECTOR_EVENT_MAX_SECONDS + 1})[0] == 400
    assert app._trigger_director_event({"event": "mcb_extract"})[0] == 202


def test_director_event_uses_curated_params_and_records_acceptance():
    app = _app()
    status, response = app._trigger_director_event(
        {"event": "mcb_tease", "duration_seconds": 8})
    assert status == 202
    assert response["accepted"] is True
    event, params, _activate_at = app.event_engine.calls[0]
    assert event == "mcb_tease"
    assert params == {"tease_amplitude": 0.08, "duration_ms": 8000}
    assert app._director_event_history[-1]["event"] == "mcb_tease"


def test_merge_event_axis_overrides_always_emits_s1_without_mfp_route():
    """Director-only path must force S1 even when authored_overrides is empty."""
    app = _app()
    merged = app._merge_event_axis_overrides(None, {
        "volume": 0.4,
        "frequency": 0.5,
        "pulse_frequency": 0.5,
        "pulse_width": 0.5,
        "alpha": 0.5,
        "beta": 0.5,
        "e1": 1.0,
        "e2": 0.0,
        "e3": 0.0,
        "e4": 0.0,
        "sensor_suppression": 0.5,
    })
    assert merged == {"S1": 0.5}
    assert app._events_last_s1 == 0.5


def test_merge_event_axis_overrides_updates_existing_and_forces_s1():
    app = _app()
    merged = app._merge_event_axis_overrides({"V0": 0.2, "L0": 0.1}, {
        "volume": 0.7,
        "frequency": 0.5,
        "pulse_frequency": 0.5,
        "pulse_width": 0.5,
        "alpha": 0.9,
        "beta": 0.5,
        "e1": 1.0,
        "e2": 0.0,
        "e3": 0.0,
        "e4": 0.0,
        "sensor_suppression": 1.0,
    })
    assert merged["V0"] == 0.7
    assert merged["L0"] == 0.9
    assert merged["S1"] == 1.0
    assert app._events_last_s1 == 1.0


class _StatusEngine:
    def __init__(self):
        self.loaded = type("L", (), {"steps": [], "source_path": None,
                                     "warnings": [], "event_count": 0})()
        self.step_count = 0
        self.last_position_ms = object()

    def status_line(self, position_ms=None, enabled=True, due_at=None):
        self.last_position_ms = position_ms
        return f"Events: stub @ {position_ms}"

    def active_trigger_names(self, due_at=None):
        return []


def test_events_status_defaults_to_send_path_position():
    """When send has applied, status uses that position (not a refresh guess)."""
    app = _app()
    app.events_enabled = _Flag(True)
    app.event_engine = _StatusEngine()
    app._events_last_position_ms = 2_591_805
    app._events_last_due_at = 12.0
    app._events_last_s1 = 0.0
    app._set_events_status_text = lambda _line: None
    app._update_events_status()
    assert app.event_engine.last_position_ms == 2_591_805


def test_events_apply_uses_player_milliseconds():
    app = _app()
    app._events_enabled_cached = True
    app._events_last_position_ms = None
    app._events_last_due_at = None
    app._events_last_s1 = None
    app.timeline = type("TL", (), {"media_position_seconds": lambda self: 12.5})()
    seen = {}

    class Engine:
        step_count = 1

        def apply(self, position_ms, values):
            seen["ms"] = position_ms
            return values

        def apply_triggers(self, _at_time, values):
            return values

    app.event_engine = Engine()
    VectorApp._apply_events_to_sample(
        app, 1.0, 0.2, 0.0, 0.0, 0.0, 0.0, 0.5, 0.5, (0.0, 0.0, 0.0, 0.0), {})
    assert seen["ms"] == 12500
    assert app._events_last_position_ms == 12500


def test_events_auto_load_path_prefers_player_over_path():
    app = _app()
    app._mfp_media_path = r"E:\from-path.mp4"

    class Player:
        def uses_player_clock(self):
            return True

        def events_media_path(self):
            return r"E:\from-player.mp4"

    app.timeline = Player()
    assert VectorApp._events_auto_load_media_path(app) == r"E:\from-player.mp4"

    class Pattern:
        def uses_player_clock(self):
            return False

    app.timeline = Pattern()
    assert VectorApp._events_auto_load_media_path(app) == r"E:\from-path.mp4"


def test_events_status_explicit_position_overrides_until_send():
    app = _app()
    app.events_enabled = _Flag(True)
    app.event_engine = _StatusEngine()
    app._events_last_position_ms = None
    app._events_last_due_at = None
    app._events_last_s1 = None
    app._set_events_status_text = lambda _line: None
    app._update_events_status(1_234_000)
    assert app.event_engine.last_position_ms == 1_234_000


def test_prostate_event_mirror_shifts_its_own_base_and_skips_electrodes():
    app = object.__new__(VectorApp)
    app._events_last_position_ms = 0

    class Engine:
        step_count = 1

        def apply(self, _position_ms, values):
            out = dict(values)
            if "alpha" in out:
                out["alpha"] = out["alpha"] + 0.1
            if "e1" in out:
                out["e1"] = 1.0
            if "pulse_frequency" in out:
                out["pulse_frequency"] = out["pulse_frequency"] + 0.05
            return out

        def apply_triggers(self, _at_time, values):
            return values

    app.event_engine = Engine()
    low = VectorApp._mirror_prostate_motion_events(
        app, 1.0, {"alpha": 0.2, "beta": 0.4, "pulse_frequency": 0.3, "pulse_width": 0.5},
        file_steps=True)
    high = VectorApp._mirror_prostate_motion_events(
        app, 1.0, {"alpha": 0.6, "beta": 0.4, "pulse_frequency": 0.3, "pulse_width": 0.5},
        file_steps=True)
    assert abs(low["alpha"] - 0.3) < 1e-9
    assert abs(high["alpha"] - 0.7) < 1e-9
    assert abs((high["alpha"] - low["alpha"]) - 0.4) < 1e-9
    assert abs(low["pulse_frequency"] - 0.35) < 1e-9
    assert "e1" not in low


def test_prostate_reversal_boost_changes_prostate_volume_only():
    app = object.__new__(VectorApp)
    app._prostate_stroke_axis_cached = "L3"
    sample = type("Sample", (), {})()
    sample.variation_depth_prostate = 1.0
    sample.prostate_stroke_direction = 1
    sample.reversal_distance_prostate = 0.0
    cfg = {
        "prostate_stroke_phase_texture": False,
        "prostate_reversal_emphasis": True,
        "prostate_reversal_window": 0.35,
        "prostate_reversal_strength": 0.5,
    }
    boosted = VectorApp._texture_prostate_volume(app, sample, 0.4, cfg)
    assert boosted > 0.4
    rising = dict(cfg, prostate_reversal_emphasis=False, prostate_stroke_phase_texture=True,
                  prostate_rising_volume_multiplier=0.8, prostate_falling_volume_multiplier=1.0)
    assert VectorApp._texture_prostate_volume(app, sample, 0.4, rising) < 0.4
    sample.prostate_stroke_direction = -1
    assert VectorApp._texture_prostate_volume(app, sample, 0.4, rising) == 0.4
    app._prostate_stroke_axis_cached = ""
    assert VectorApp._texture_prostate_volume(app, sample, 0.4, cfg) == 0.4
