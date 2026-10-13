from pathlib import Path
from vector1a.media_clock import (
    events_auto_load_decision,
    missing_file_warn_due,
    find_matching_events_file,
    find_matching_funscript,
    find_matching_ramp_file,
    _file_uri_to_path,
    _parse_clock_value,
)


def test_script_resolver_prefers_media_sibling(tmp_path):
    media = tmp_path/'Movie.mp4'; media.write_bytes(b'')
    script = tmp_path/'Movie.funscript'; script.write_text('{}')
    lib = tmp_path/'library'; lib.mkdir(); (lib/'Movie.funscript').write_text('{}')
    assert find_matching_funscript(str(media), [str(lib)]) == str(script.resolve())


def test_script_resolver_searches_library_recursively(tmp_path):
    media_dir = tmp_path/'media'; media_dir.mkdir(); media = media_dir/'Movie.mp4'; media.write_bytes(b'')
    lib = tmp_path/'library'; nested = lib/'nested'; nested.mkdir(parents=True)
    script = nested/'Movie.funscript'; script.write_text('{}')
    assert find_matching_funscript(str(media), [str(lib)]) == str(script.resolve())


def test_events_resolver_prefers_media_sibling_yml(tmp_path):
    media = tmp_path / 'Chapter.mp4'
    media.write_bytes(b'')
    events = tmp_path / 'Chapter.events.yml'
    events.write_text('events: []\n')
    lib = tmp_path / 'library'
    lib.mkdir()
    (lib / 'Chapter.events.yml').write_text('events: []\n')
    assert find_matching_events_file(str(media), [str(lib)]) == str(events.resolve())


def test_events_resolver_accepts_yaml_suffix(tmp_path):
    media = tmp_path / 'Chapter.mp4'
    media.write_bytes(b'')
    events = tmp_path / 'Chapter.events.yaml'
    events.write_text('events: []\n')
    assert find_matching_events_file(str(media)) == str(events.resolve())


def test_events_resolver_searches_library_recursively(tmp_path):
    media_dir = tmp_path / 'media'
    media_dir.mkdir()
    media = media_dir / 'Movie.mp4'
    media.write_bytes(b'')
    lib = tmp_path / 'library'
    nested = lib / 'nested'
    nested.mkdir(parents=True)
    events = nested / 'Movie.events.yml'
    events.write_text('events: []\n')
    assert find_matching_events_file(str(media), [str(lib)]) == str(events.resolve())


def test_events_resolver_miss_returns_none(tmp_path):
    media = tmp_path / 'Movie.mp4'
    media.write_bytes(b'')
    assert find_matching_events_file(str(media)) is None


def test_events_auto_load_decision_load_clear_and_warn_once():
    media_a = r'C:\clips\A.mp4'
    media_b = r'C:\clips\B.mp4'
    events_a = r'C:\clips\A.events.yml'

    changed, path, warn = events_auto_load_decision(media_a, None, '', events_a)
    assert (changed, path, warn) == (True, events_a, False)

    changed, path, warn = events_auto_load_decision(media_a, media_a, events_a, events_a)
    assert (changed, path, warn) == (False, None, False)

    changed, path, warn = events_auto_load_decision(media_b, media_a, events_a, None)
    assert (changed, path, warn) == (True, '', True)

    changed, path, warn = events_auto_load_decision(None, media_b, '', None)
    assert (changed, path, warn) == (False, None, False)

    changed, path, warn = events_auto_load_decision('', media_a, events_a, None)
    assert (changed, path, warn) == (True, '', True)


def test_missing_file_warn_cooldown():
    assert missing_file_warn_due(None, 100.0) is True
    assert missing_file_warn_due(100.0, 129.9) is False
    assert missing_file_warn_due(100.0, 130.0) is True


def test_ramp_resolver_prefers_sidecar_not_l0_funscript(tmp_path):
    media = tmp_path / 'Chapter.mp4'
    media.write_bytes(b'')
    (tmp_path / 'Chapter.funscript').write_text('{}')
    ramp = tmp_path / 'Chapter.ramp.funscript'
    ramp.write_text('{"waypoints": []}\n')
    assert find_matching_ramp_file(str(media)) == str(ramp.resolve())


def test_ramp_resolver_accepts_json(tmp_path):
    media = tmp_path / 'Chapter.mp4'
    media.write_bytes(b'')
    ramp = tmp_path / 'Chapter.ramp.json'
    ramp.write_text('{}\n')
    assert find_matching_ramp_file(str(media)) == str(ramp.resolve())


def test_ramp_resolver_miss_returns_none(tmp_path):
    media = tmp_path / 'Chapter.mp4'
    media.write_bytes(b'')
    (tmp_path / 'Chapter.funscript').write_text('{}')
    assert find_matching_ramp_file(str(media)) is None


def test_clock_string_parser():
    assert _parse_clock_value('01:02:03') == 3723.0
    assert _parse_clock_value('02:30') == 150.0
    assert _parse_clock_value('12.5') == 12.5


def test_mpc_numeric_variables_are_milliseconds(monkeypatch):
    from vector1a import media_clock as mc
    html = '''<html><body>
    <p id="filepath">C:\\Media\\Example.mp4</p>
    <p id="state">2</p><p id="statestring">Playing</p>
    <p id="position">8922383</p><p id="positionstring">02:28:42</p>
    <p id="duration">10000000</p><p id="durationstring">02:46:40</p>
    <p id="playbackrate">1</p></body></html>'''
    monkeypatch.setattr(mc, '_http_text', lambda *a, **k: html)
    snap = mc.poll_mpc()
    assert snap.connected is True
    assert snap.state == 'playing'
    assert abs(snap.position_seconds - 8922.383) < 1e-9
    assert abs(snap.duration_seconds - 10000.0) < 1e-9
    assert snap.raw_position == '8922383'
    assert snap.position_source == 'position_ms'


def test_potplayer_snapshot_converts_milliseconds_and_state():
    from vector1a.media_clock import _potplayer_snapshot
    snap = _potplayer_snapshot(12500, 0, 2, r"E:\Videos\clip.m4v")
    assert snap.connected is True
    assert snap.state == "playing"
    assert snap.position_seconds == 12.5
    assert snap.duration_seconds is None
    assert snap.media_path == r"E:\Videos\clip.m4v"
    paused = _potplayer_snapshot(5000, 90000, 1, None)
    assert paused.state == "paused"
    assert paused.position_seconds == 5.0
    assert paused.duration_seconds == 90.0
    empty = _potplayer_snapshot(0, 0, -1, None)
    assert empty.connected is False
    assert empty.error == "no media"


def test_poll_potplayer_uses_query_result(monkeypatch):
    from vector1a import media_clock as mc
    monkeypatch.setattr(mc, "_query_potplayer", lambda: (209175, 6845000, 2, r"E:\clip.m4v"))
    snap = mc.poll_potplayer()
    assert snap.player == "PotPlayer"
    assert abs(snap.position_seconds - 209.175) < 1e-9
    assert snap.media_path == r"E:\clip.m4v"


def test_mpc_state_uses_numeric_code_when_statestring_is_localized(monkeypatch):
    from vector1a import media_clock as mc
    html = '''<html><body>
    <p id="state">2</p><p id="statestring">Wiedergabe</p>
    <p id="position">1000</p><p id="duration">2000</p>
    </body></html>'''
    monkeypatch.setattr(mc, '_http_text', lambda *a, **k: html)
    snap = mc.poll_mpc()
    assert snap.state == 'playing'
