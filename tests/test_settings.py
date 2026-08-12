import os
import tempfile
import unittest

from vector1a.settings import load_settings, save_settings, settings_path


class SettingsTests(unittest.TestCase):
    def test_round_trip_uses_local_app_data(self):
        previous = os.environ.get("LOCALAPPDATA")
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.environ["LOCALAPPDATA"] = directory
                values = {"lookahead": 2.0, "mode": "Top-Left -> Bottom-Right 0-90"}
                save_settings(values)
                self.assertEqual(load_settings(), values)
                self.assertTrue(settings_path().is_file())
        finally:
            if previous is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = previous

    def test_missing_or_invalid_settings_fall_back_cleanly(self):
        previous = os.environ.get("LOCALAPPDATA")
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.environ["LOCALAPPDATA"] = directory
                self.assertEqual(load_settings(), {})
                path = settings_path()
                path.parent.mkdir(parents=True)
                path.write_text("not json", encoding="utf-8")
                self.assertEqual(load_settings(), {})
        finally:
            if previous is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = previous
