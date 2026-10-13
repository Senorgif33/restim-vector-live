import json
from pathlib import Path

from vector1a.timeline import FunscriptTimeline


class FakeClock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now
    def advance(self, s): self.now += s


def make_script(path: Path):
    actions=[]
    # deliberately distinctive 20 s path
    points=[(0,10),(1000,90),(2500,20),(4000,80),(6000,30),(9000,95),(12000,15),(16000,70),(20000,40)]
    for at,pos in points: actions.append({'at':at,'pos':pos})
    path.write_text(json.dumps({'version':'1.0','inverted':False,'range':100,'actions':actions}))


def test_manual_preview_exposes_future(tmp_path):
    c=FakeClock(); path=tmp_path/'x.funscript'; make_script(path)
    t=FunscriptTimeline(clock=c)
    meta=t.load(str(path)); assert meta['actions']==9
    t.set_clock_mode(FunscriptTimeline.CLOCK_MANUAL)
    t.manual_seek(4.0)
    snap=t.snapshot()
    assert snap['synced'] is True
    assert snap['position_seconds']==4.0
    assert 'energy_band' in snap['next_10_seconds']
    assert 'energy_trend' in snap['next_30_seconds']
    assert snap['sync_confidence']==1.0


def test_manual_play_advances(tmp_path):
    c=FakeClock(); path=tmp_path/'x.funscript'; make_script(path)
    t=FunscriptTimeline(clock=c); t.load(str(path)); t.set_clock_mode(FunscriptTimeline.CLOCK_MANUAL)
    t.manual_seek(3); t.manual_play(); c.advance(2.5)
    assert abs(t.position_seconds()-5.5)<1e-6
    t.manual_pause(); c.advance(5)
    assert abs(t.position_seconds()-5.5)<1e-6


def test_mfp_pattern_sync_can_lock(tmp_path):
    c=FakeClock(); path=tmp_path/'x.funscript'; make_script(path)
    t=FunscriptTimeline(clock=c); t.load(str(path))
    # Feed interpolated samples corresponding to around script t=8..11 s.
    # Access interpolation only for deterministic test generation.
    with t._lock:
        vals=[t._interp_locked(8.0+i*0.1) for i in range(31)]
    for v in vals:
        t.observe_live(v, c())
        c.advance(0.1)
    snap=t.snapshot()
    assert snap['synced'] is True
    assert 9.0 <= snap['position_seconds'] <= 12.5
    assert snap['sync_confidence'] is not None


def test_bad_live_pattern_does_not_claim_confident_sync(tmp_path):
    c=FakeClock(); path=tmp_path/'x.funscript'; make_script(path)
    t=FunscriptTimeline(clock=c); t.load(str(path))
    # Extreme alternating pattern is intentionally unlike most of script.
    for i in range(40):
        t.observe_live(0.0 if i % 2 else 1.0, c()); c.advance(0.1)
    snap=t.snapshot()
    # It may find a loose local coincidence, but must not claim a high-confidence lock.
    assert (not snap['synced']) or (snap.get('sync_confidence') or 0) < 0.8


def test_direct_media_clock_is_authoritative_and_auto_loads(tmp_path, monkeypatch):
    from vector1a import timeline as timeline_module
    from vector1a.media_clock import MediaSnapshot

    media = tmp_path / 'Example Movie.mp4'
    media.write_bytes(b'')
    script = tmp_path / 'Example Movie.funscript'
    make_script(script)
    c = FakeClock()
    t = FunscriptTimeline(clock=c)
    t.configure_media(auto_load_script=True)
    t.set_clock_mode(FunscriptTimeline.CLOCK_VLC)

    monkeypatch.setattr(timeline_module, 'poll_vlc', lambda *a, **k: MediaSnapshot(
        'VLC', True, 'playing', 7.25, 20.0, 1.0, str(media), media.name))
    t._media_poll_worker(FunscriptTimeline.CLOCK_VLC, '127.0.0.1', 8080, '', 13579)
    snap = t.snapshot()
    assert snap['loaded'] is True
    assert snap['file'] == 'Example Movie.funscript'
    assert snap['position_seconds'] == 7.25
    assert snap['sync_confidence'] == 1.0
    assert snap['clock_authoritative'] is True
    assert snap['media_player'] == 'VLC'


def test_direct_media_seek_updates_without_pattern_reacquire(tmp_path, monkeypatch):
    from vector1a import timeline as timeline_module
    from vector1a.media_clock import MediaSnapshot
    media = tmp_path / 'Example Movie.mp4'; media.write_bytes(b'')
    script = tmp_path / 'Example Movie.funscript'; make_script(script)
    positions = iter([2.0, 16.0])
    monkeypatch.setattr(timeline_module, 'poll_vlc', lambda *a, **k: MediaSnapshot(
        'VLC', True, 'playing', next(positions), 20.0, 1.0, str(media), media.name))
    t = FunscriptTimeline(clock=FakeClock())
    t.configure_media(auto_load_script=True)
    t.set_clock_mode(FunscriptTimeline.CLOCK_VLC)
    t._media_poll_worker(FunscriptTimeline.CLOCK_VLC, '127.0.0.1', 8080, '', 13579)
    assert t.position_seconds() == 2.0
    t._media_poll_worker(FunscriptTimeline.CLOCK_VLC, '127.0.0.1', 8080, '', 13579)
    assert t.position_seconds() == 16.0


def test_direct_media_clock_extrapolates_between_polls(tmp_path, monkeypatch):
    from vector1a import timeline as timeline_module
    from vector1a.media_clock import MediaSnapshot
    media = tmp_path / 'Example Movie.mp4'; media.write_bytes(b'')
    script = tmp_path / 'Example Movie.funscript'; make_script(script)
    c = FakeClock()
    monkeypatch.setattr(timeline_module, 'poll_vlc', lambda *a, **k: MediaSnapshot(
        'VLC', True, 'playing', 5.0, 20.0, 1.0, str(media), media.name))
    t = FunscriptTimeline(clock=c)
    t.configure_media(auto_load_script=True)
    t.set_clock_mode(FunscriptTimeline.CLOCK_VLC)
    t._media_poll_worker(FunscriptTimeline.CLOCK_VLC, '127.0.0.1', 8080, '', 13579)
    assert t.position_seconds() == 5.0
    c.advance(2.5)
    assert abs(t.position_seconds() - 7.5) < 1e-6


def test_paused_direct_media_clock_does_not_extrapolate(tmp_path, monkeypatch):
    from vector1a import timeline as timeline_module
    from vector1a.media_clock import MediaSnapshot
    media = tmp_path / 'Example Movie.mp4'; media.write_bytes(b'')
    script = tmp_path / 'Example Movie.funscript'; make_script(script)
    c = FakeClock()
    monkeypatch.setattr(timeline_module, 'poll_vlc', lambda *a, **k: MediaSnapshot(
        'VLC', True, 'paused', 5.0, 20.0, 1.0, str(media), media.name))
    t = FunscriptTimeline(clock=c)
    t.configure_media(auto_load_script=True)
    t.set_clock_mode(FunscriptTimeline.CLOCK_VLC)
    t._media_poll_worker(FunscriptTimeline.CLOCK_VLC, '127.0.0.1', 8080, '', 13579)
    c.advance(4.0)
    assert t.position_seconds() == 5.0


def test_direct_clock_marks_repeated_playing_position_stale(tmp_path, monkeypatch):
    from vector1a import timeline as timeline_module
    from vector1a.media_clock import MediaSnapshot
    media = tmp_path / 'Example Movie.mp4'; media.write_bytes(b'')
    script = tmp_path / 'Example Movie.funscript'; make_script(script)
    c = FakeClock()
    monkeypatch.setattr(timeline_module, 'poll_mpc', lambda *a, **k: MediaSnapshot(
        'MPC', True, 'playing', 5.0, 20.0, 1.0, str(media), media.name,
        raw_position='5000', raw_duration='20000', position_source='position_ms'))
    t = FunscriptTimeline(clock=c)
    t.configure_media(auto_load_script=True)
    t.set_clock_mode(FunscriptTimeline.CLOCK_MPC)
    t._media_poll_worker(FunscriptTimeline.CLOCK_MPC, '127.0.0.1', 8080, '', 13579)
    c.advance(1.0)
    t._media_poll_worker(FunscriptTimeline.CLOCK_MPC, '127.0.0.1', 8080, '', 13579)
    assert t.metadata()['media_clock_health'] in {'checking', 'live'}
    c.advance(1.2)
    t._media_poll_worker(FunscriptTimeline.CLOCK_MPC, '127.0.0.1', 8080, '', 13579)
    snap = t.snapshot()
    assert snap['media_clock_health'] == 'stale'
    assert snap['clock_authoritative'] is False


def test_media_position_ignores_missing_funscript_and_pattern_match():
    from vector1a.media_clock import MediaSnapshot
    c = FakeClock()
    t = FunscriptTimeline(clock=c)
    t.set_clock_mode(FunscriptTimeline.CLOCK_VLC)
    t._media_snapshot = MediaSnapshot(
        'VLC', True, 'playing', 12.5, 100.0, 1.0, r'E:\clip.mp4', 'clip.mp4')
    t._media_snapshot_received_at = c()
    t._media_clock_health = 'live'
    t._match_position = 99.0
    assert t.position_seconds() is None
    assert t.media_position_seconds() == 12.5
    assert t.events_media_path() == r'E:\clip.mp4'
    c.advance(1.0)
    assert abs(t.media_position_seconds() - 13.5) < 1e-6
    t._media_snapshot = MediaSnapshot(
        'VLC', True, 'paused', 12.5, 100.0, 1.0, r'E:\clip.mp4', 'clip.mp4')
    t._media_snapshot_received_at = c()
    c.advance(4.0)
    assert t.media_position_seconds() == 12.5


def test_stale_media_position_holds_player_sample():
    from vector1a.media_clock import MediaSnapshot
    c = FakeClock()
    t = FunscriptTimeline(clock=c)
    t.set_clock_mode(FunscriptTimeline.CLOCK_AUTO)
    t._media_snapshot = MediaSnapshot(
        'VLC', True, 'playing', 8.0, 100.0, 2.0, r'E:\clip.mp4', 'clip.mp4')
    t._media_snapshot_received_at = c()
    t._media_clock_health = 'stale'
    t._match_position = 40.0
    c.advance(3.0)
    assert t.media_position_seconds() == 8.0
    t.set_clock_mode(FunscriptTimeline.CLOCK_MFP)
    assert t.media_position_seconds() is None
    assert t.events_media_path() is None


def test_auto_prefers_playing_potplayer_over_paused_mpc():
    from vector1a.media_clock import MediaSnapshot
    from vector1a.timeline import choose_auto_player
    vlc = MediaSnapshot("VLC", False, error="down")
    mpc = MediaSnapshot("MPC", True, "paused", 1.0, 10.0, 1.0, r"E:\old.mp4", "old.mp4")
    pot = MediaSnapshot("PotPlayer", True, "playing", 12.5, 100.0, 1.0, r"E:\clip.m4v", "clip.m4v")
    assert choose_auto_player([vlc, mpc, pot]).player == "PotPlayer"
    mpc_play = MediaSnapshot("MPC", True, "playing", 4.0, 10.0, 1.0, r"E:\old.mp4", "old.mp4")
    assert choose_auto_player([vlc, mpc_play, pot]).player == "MPC"
    assert choose_auto_player([vlc, mpc, MediaSnapshot("PotPlayer", False)]).player == "MPC"


def test_media_clock_returns_position_and_duration_without_funscript():
    from vector1a.media_clock import MediaSnapshot
    c = FakeClock()
    t = FunscriptTimeline(clock=c)
    t.set_clock_mode(FunscriptTimeline.CLOCK_POT)
    t._media_snapshot = MediaSnapshot(
        "PotPlayer", True, "playing", 25.0, 100.0, 1.0, r"E:\clip.m4v", "clip.m4v")
    t._media_snapshot_received_at = c()
    t._media_clock_health = "live"
    assert t.media_clock() == (25.0, 100.0)
    t.set_clock_mode(FunscriptTimeline.CLOCK_MFP)
    assert t.media_clock() == (None, None)


def test_ramp_gain_uses_player_clock():
    from vector1a.app import VectorApp
    from vector1a.media_timeline import RampWaypoint
    app = object.__new__(VectorApp)
    app._send_cfg = {
        "media_volume_ramp_enabled": True,
        "media_volume_ramp_waypoints_enabled": True,
        "media_volume_ramp_curve": "Linear",
        "media_volume_ramp_floor": 0.0,
        "media_volume_ramp_ceiling": 1.0,
        "media_volume_ramp_floor2": 0.4,
        "media_volume_ramp_ceiling2": 1.0,
        "media_volume_ramp_floor3": 0.4,
        "media_volume_ramp_ceiling3": 1.0,
    }
    app._media_ramp_waypoints = [
        RampWaypoint(0.0, "floor1", "Linear"),
        RampWaypoint(100.0, "ceiling1", "Linear"),
    ]
    app.timeline = type("TL", (), {"media_clock": lambda self: (10.0, 100.0)})()
    gain = VectorApp._media_volume_gain_at(app, 0.0)
    assert abs(gain - 0.1) < 1e-9
    app._send_cfg["media_volume_ramp_waypoints_enabled"] = False
    app.timeline = type("TL", (), {"media_clock": lambda self: (25.0, 100.0)})()
    simple = VectorApp._media_volume_gain_at(app, 0.0)
    assert abs(simple - 0.25) < 1e-9


def _ramp_app(clock):
    from vector1a.app import VectorApp

    class Var:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    app = object.__new__(VectorApp)
    app.media_ramp_auto_load = Var(True)
    app.media_volume_ramp_curve = Var("Linear")
    app.media_volume_ramp_waypoints_enabled = Var(False)
    app.timeline_script_libraries = Var("")
    app._media_ramp_waypoints = []
    app._media_ramp_source_path = ""
    app._last_ramp_auto_loaded_for_media = None
    app._ramp_seed_pending = None
    app._ramp_seeded_for = None
    app._media_ramp_auto_load_note = None
    app._mfp_media_path = None
    app.timeline = type("TL", (), {"media_clock": lambda self: clock()})()
    app._events_auto_load_media_path = lambda: r"E:\clip.m4v"
    app._refresh_send_config_cache = lambda: None
    app._refresh_media_ramp_waypoint_tree = lambda: None
    app._refresh_extra_level_controls = lambda: None
    app._redraw_media_ramp_curve_preview = lambda: None
    app._save_settings = lambda: None
    app._clear_media_ramp_waypoints = VectorApp._clear_media_ramp_waypoints.__get__(app, VectorApp)
    return app


def test_missing_ramp_seeds_floor_and_end_ceiling(monkeypatch):
    from vector1a.app import VectorApp
    from vector1a import app as app_module
    monkeypatch.setattr(app_module, "find_matching_ramp_file", lambda *_a, **_k: None)
    app = _ramp_app(lambda: (0.0, 100.0))
    VectorApp._maybe_auto_load_ramp_file(app)
    assert [(p.time_s, p.level) for p in app._media_ramp_waypoints] == [
        (0.0, "floor1"), (100.0, "ceiling1")]
    assert app._media_ramp_source_path == ""
    assert app.media_volume_ramp_waypoints_enabled.get() is True
    assert app._ramp_seeded_for == r"E:\clip.m4v"


def test_missing_duration_defers_default_ramp_until_known(monkeypatch):
    from vector1a.app import VectorApp
    from vector1a import app as app_module
    monkeypatch.setattr(app_module, "find_matching_ramp_file", lambda *_a, **_k: None)
    clock = {"value": (1.0, None)}
    app = _ramp_app(lambda: clock["value"])
    VectorApp._maybe_auto_load_ramp_file(app)
    assert app._media_ramp_waypoints == []
    assert app._ramp_seed_pending == r"E:\clip.m4v"
    clock["value"] = (1.0, 100.0)
    VectorApp._maybe_seed_default_ramp(app)
    assert app._media_ramp_waypoints[-1].time_s == 100.0
    app._media_ramp_waypoints = []
    VectorApp._maybe_seed_default_ramp(app)
    assert app._media_ramp_waypoints == []


def test_saved_ramp_file_is_not_replaced_by_default_pair(monkeypatch):
    from vector1a.app import VectorApp
    from vector1a import app as app_module
    from vector1a.media_timeline import RampWaypoint
    monkeypatch.setattr(app_module, "find_matching_ramp_file", lambda *_a, **_k: r"E:\clip.ramp.json")
    app = _ramp_app(lambda: (0.0, 100.0))

    def load(path):
        app._media_ramp_source_path = path
        app._media_ramp_waypoints = [RampWaypoint(12.0, "ceiling1", "Linear")]

    app._load_media_ramp_file = load
    VectorApp._maybe_auto_load_ramp_file(app)
    assert app._media_ramp_source_path == r"E:\clip.ramp.json"
    assert [(p.time_s, p.level) for p in app._media_ramp_waypoints] == [(12.0, "ceiling1")]


def test_potplayer_poll_drives_events_clock(monkeypatch):
    from vector1a import timeline as timeline_module
    from vector1a.media_clock import MediaSnapshot
    c = FakeClock()
    monkeypatch.setattr(timeline_module, "poll_potplayer", lambda: MediaSnapshot(
        "PotPlayer", True, "playing", 12.5, 100.0, 1.0, r"E:\clip.m4v", "clip.m4v"))
    t = FunscriptTimeline(clock=c)
    t.set_clock_mode(FunscriptTimeline.CLOCK_POT)
    t._media_poll_worker(FunscriptTimeline.CLOCK_POT, "127.0.0.1", 8080, "", 13579)
    assert t.media_position_seconds() == 12.5
    assert t.events_media_path() == r"E:\clip.m4v"
    c.advance(1.0)
    assert abs(t.media_position_seconds() - 13.5) < 1e-6


def test_sample_position_reads_authored_future_without_moving_clock(tmp_path):
    import json
    from vector1a.timeline import FunscriptTimeline
    path = tmp_path / "tempo.funscript"
    path.write_text(json.dumps({"actions": [
        {"at": 0, "pos": 0}, {"at": 1000, "pos": 100}, {"at": 2000, "pos": 0}
    ]}), encoding="utf-8")
    tl = FunscriptTimeline(clock=lambda: 10.0)
    tl.load(str(path))
    assert abs(tl.sample_position(0.5) - 0.5) < 1e-9
    assert abs(tl.sample_position(1.5) - 0.5) < 1e-9
    assert tl.sample_position(99.0) == 0.0
