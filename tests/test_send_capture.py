import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from vector1a.send_capture import (
    SendCaptureLog,
    default_log_dir,
    format_sample,
    parse_sample,
    session_log_path,
)


class FormatTests(unittest.TestCase):
    def test_sample_round_trip(self):
        line = format_sample(
            754321, True, ["CH_edge"],
            {"V0": 0.8123, "P0": 0.41, "P1": 0.552, "S1": 0.5, "Vp": 0.8})
        fields = parse_sample(line)
        self.assertEqual(fields["media_ms"], "754321")
        self.assertEqual(fields["events_on"], "1")
        self.assertEqual(fields["active"], "CH_edge")
        self.assertEqual(fields["V0"], "0.8123")
        self.assertEqual(fields["P0"], "0.4100")
        self.assertEqual(fields["P1"], "0.5520")
        self.assertEqual(fields["S1"], "0.5000")
        self.assertEqual(fields["Vp"], "0.8000")

    def test_missing_values_use_dash(self):
        fields = parse_sample(format_sample(None, False, [], {"V0": 0.25}))
        self.assertEqual(fields["media_ms"], "-")
        self.assertEqual(fields["events_on"], "0")
        self.assertEqual(fields["active"], "-")
        self.assertEqual(fields["V0"], "0.2500")
        self.assertEqual(fields["S1"], "-")
        self.assertEqual(fields["Vp"], "-")

    def test_multiple_active_names_are_comma_joined(self):
        fields = parse_sample(format_sample(0, True, ["CH_edge", "EVT_cum"], {}))
        self.assertEqual(fields["active"], "CH_edge,EVT_cum")

    def test_session_path_and_dir(self):
        with TemporaryDirectory() as tmp:
            path = session_log_path(Path(tmp))
            self.assertEqual(path.parent, Path(tmp))
            self.assertTrue(path.name.startswith("session-"))
            self.assertTrue(path.name.endswith(".log"))
        self.assertEqual(default_log_dir().name, "send-capture")


class SendCaptureLogTests(unittest.TestCase):
    def test_writes_meta_and_samples(self):
        with TemporaryDirectory() as tmp:
            path = session_log_path(Path(tmp))
            log = SendCaptureLog(path)
            log.write_meta("events_on=1 events_file=chapter.events.yml")
            log.write_sample(
                media_ms=754321, events_on=True, active=["CH_edge"],
                V0=0.8123, P0=0.41, P1=0.552, S1=0.5, Vp=0.8)
            log.close()
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertIn("session_start", lines[0])
            self.assertIn("events_file=chapter.events.yml", lines[1])
            sample = parse_sample(lines[2])
            self.assertEqual(sample["media_ms"], "754321")
            self.assertEqual(sample["V0"], "0.8123")
            self.assertIn("session_end", lines[3])

    def test_writes_after_close_are_dropped(self):
        with TemporaryDirectory() as tmp:
            path = session_log_path(Path(tmp))
            log = SendCaptureLog(path)
            log.close()
            before = path.read_text(encoding="utf-8")
            log.write_sample(media_ms=1, V0=0.5)
            log.close()
            self.assertEqual(path.read_text(encoding="utf-8"), before)


class AppCaptureHookTests(unittest.TestCase):
    """The send-path hook writes the finals Restim gets, or nothing when off."""

    def setUp(self):
        from vector1a.app import VectorApp
        from vector1a.events import EventEngine

        class FakeEngine:
            lookahead_seconds = 2.0

            def clock(self):
                return 100.0

        self.app = VectorApp.__new__(VectorApp)
        self.app.engine = FakeEngine()
        self.app.event_engine = EventEngine()
        self.app._events_enabled_cached = True
        self.app._send_capture = None
        self.app._capture_media_ms = lambda _at: 754321

    def test_hook_logs_override_finals(self):
        with TemporaryDirectory() as tmp:
            path = session_log_path(Path(tmp))
            log = SendCaptureLog(path)
            self.app._capture_send_sample(
                log, 100.0, {"V0": 0.8123, "P1": 0.552, "S1": 0.5},
                primary_volume=0.1, pulse_frequency=0.41, pulse_width=0.2,
                prostate_volume=0.8)
            log.close()
            sample = parse_sample(path.read_text(encoding="utf-8").splitlines()[1])
            self.assertEqual(sample["media_ms"], "754321")
            self.assertEqual(sample["events_on"], "1")
            # Overrides win; axes without an override fall back to the final arg.
            self.assertEqual(sample["V0"], "0.8123")
            self.assertEqual(sample["P0"], "0.4100")
            self.assertEqual(sample["P1"], "0.5520")
            self.assertEqual(sample["S1"], "0.5000")
            self.assertEqual(sample["Vp"], "0.8000")

    def test_absent_s1_logs_dash(self):
        with TemporaryDirectory() as tmp:
            path = session_log_path(Path(tmp))
            log = SendCaptureLog(path)
            self.app._events_enabled_cached = False
            self.app._capture_send_sample(
                log, 100.0, None, primary_volume=0.6, pulse_frequency=0.4,
                pulse_width=0.3, prostate_volume=0.5)
            log.close()
            sample = parse_sample(path.read_text(encoding="utf-8").splitlines()[1])
            self.assertEqual(sample["events_on"], "0")
            self.assertEqual(sample["S1"], "-")
            self.assertEqual(sample["V0"], "0.6000")


if __name__ == "__main__":
    unittest.main()
