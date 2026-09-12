"""Settings persistence tests that never touch the user's configuration."""

import json
import math
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rb3dashboard.settings import SettingsStore


class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "settings.json"

    def test_missing_file_loads_complete_defaults(self):
        loaded = SettingsStore(self.path).load()

        self.assertEqual(loaded["youtube_api_key"], "")
        self.assertEqual(loaded["history_enabled"], True)
        self.assertEqual(loaded["video_start_delay"], 0.0)
        self.assertEqual(loaded["video_monitor"], 0)
        self.assertEqual(loaded["blank_screen_on_song"], False)

    def test_load_keeps_supported_legacy_and_unknown_values(self):
        original = {
            "youtube_api_key": "legacy-key",
            "fullscreen": False,
            "video_start_delay": 1.25,
            "video_monitor": 2,
            "blank_screen_on_song": True,
            "future_setting": {"nested": "value"},
        }
        self.path.write_text(json.dumps(original), encoding="utf-8")

        loaded = SettingsStore(self.path).load()

        for key, value in original.items():
            self.assertEqual(loaded[key], value)
        self.assertEqual(loaded["stats_enabled"], True)

    def test_invalid_known_values_use_defaults_and_are_backed_up_before_save(self):
        invalid_values = {
            "youtube_api_key": 4,
            "fullscreen": 1,
            "video_start_delay": True,
            "video_monitor": -1,
            "future_setting": "preserved",
        }
        original_bytes = json.dumps(invalid_values).encode("utf-8")
        self.path.write_bytes(original_bytes)
        store = SettingsStore(self.path)

        loaded = store.load()
        self.assertEqual(loaded["youtube_api_key"], "")
        self.assertIs(loaded["fullscreen"], True)
        self.assertEqual(loaded["video_start_delay"], 0.0)
        self.assertEqual(loaded["video_monitor"], 0)
        self.assertEqual(loaded["future_setting"], "preserved")

        store.save(loaded)

        backups = list(self.path.parent.glob("settings.json.invalid-*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original_bytes)
        self.assertEqual(SettingsStore(self.path).load()["future_setting"], "preserved")

    def test_invalid_json_and_root_are_preserved_before_replacement(self):
        for name, original_bytes in (
            ("json", b"{broken"),
            ("root", b"[1, 2, 3]"),
        ):
            with self.subTest(name=name):
                path = self.path.with_name(name + ".json")
                path.write_bytes(original_bytes)
                store = SettingsStore(path)

                loaded = store.load()
                store.save(loaded)

                backups = list(path.parent.glob(path.name + ".invalid-*.bak"))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_bytes(), original_bytes)
                self.assertEqual(SettingsStore(path).load()["history_enabled"], True)

    def test_valid_numeric_boundaries_exclude_bool_and_nonfinite_values(self):
        for value, expected in (
            (0, 0),
            (2.5, 2.5),
            (-0.1, 0.0),
            (math.inf, 0.0),
            (math.nan, 0.0),
            (True, 0.0),
        ):
            with self.subTest(value=value):
                self.path.write_text(
                    json.dumps({"video_start_delay": value}), encoding="utf-8"
                )
                self.assertEqual(
                    SettingsStore(self.path).load()["video_start_delay"], expected
                )

    def test_unrepresentably_large_delay_defaults_and_preserves_input(self):
        original_bytes = b'{"video_start_delay": 1' + (b"0" * 400) + b"}"
        self.path.write_bytes(original_bytes)
        store = SettingsStore(self.path)

        loaded = store.load()
        self.assertEqual(loaded["video_start_delay"], 0.0)
        store.save(loaded)

        backups = list(self.path.parent.glob("settings.json.invalid-*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original_bytes)

    def test_excessively_nested_json_defaults_and_preserves_input(self):
        original_bytes = (b"[" * 1100) + (b"]" * 1100)
        self.path.write_bytes(original_bytes)
        store = SettingsStore(self.path)

        loaded = store.load()
        self.assertEqual(loaded["history_enabled"], True)
        store.save(loaded)

        backups = list(self.path.parent.glob("settings.json.invalid-*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original_bytes)

    def test_integer_limit_json_defaults_and_preserves_input(self):
        original_bytes = b'{"video_start_delay": ' + (b"9" * 5000) + b"}"
        self.path.write_bytes(original_bytes)
        store = SettingsStore(self.path)

        loaded = store.load()
        self.assertEqual(loaded["video_start_delay"], 0.0)
        store.save(loaded)

        backups = list(self.path.parent.glob("settings.json.invalid-*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original_bytes)

    def test_save_is_atomic_and_reloads_utf8_data(self):
        store = SettingsStore(self.path)
        values = store.load()
        values.update({"youtube_api_key": "clé-音", "future_setting": "✓"})

        store.save(values)

        self.assertEqual(SettingsStore(self.path).load()["youtube_api_key"], "clé-音")
        self.assertEqual(SettingsStore(self.path).load()["future_setting"], "✓")
        self.assertEqual(list(self.path.parent.glob(".settings.json.*.tmp")), [])

    def test_replace_failure_keeps_original_and_cleans_temporary_file(self):
        original_bytes = b'{"history_enabled": false}'
        self.path.write_bytes(original_bytes)
        store = SettingsStore(self.path)
        loaded = store.load()
        loaded["history_enabled"] = True

        with patch("rb3dashboard.settings.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                store.save(loaded)

        self.assertEqual(self.path.read_bytes(), original_bytes)
        self.assertEqual(list(self.path.parent.glob(".settings.json.*.tmp")), [])

    def test_write_failure_keeps_original_and_cleans_temporary_file(self):
        original_bytes = b'{"history_enabled": false}'
        self.path.write_bytes(original_bytes)
        store = SettingsStore(self.path)
        loaded = store.load()

        with patch("rb3dashboard.settings.json.dump", side_effect=OSError("write failed")):
            with self.assertRaisesRegex(OSError, "write failed"):
                store.save(loaded)

        self.assertEqual(self.path.read_bytes(), original_bytes)
        self.assertEqual(list(self.path.parent.glob(".settings.json.*.tmp")), [])

    def test_backup_failure_keeps_invalid_original_and_refuses_replacement(self):
        original_bytes = b"{broken"
        self.path.write_bytes(original_bytes)
        store = SettingsStore(self.path)
        loaded = store.load()

        with patch.object(store, "_backup_invalid_input", side_effect=OSError("backup failed")):
            with self.assertRaisesRegex(OSError, "backup failed"):
                store.save(loaded)

        self.assertEqual(self.path.read_bytes(), original_bytes)
        self.assertEqual(list(self.path.parent.glob(".settings.json.*.tmp")), [])

    def test_unreadable_input_refuses_save(self):
        store = SettingsStore(self.path)
        with patch("pathlib.Path.read_bytes", side_effect=PermissionError("denied")):
            loaded = store.load()

        with self.assertRaisesRegex(OSError, "unreadable"):
            store.save(loaded)
        self.assertFalse(self.path.exists())

    def test_default_path_preserves_appdata_location(self):
        appdata = Path(self.temp_dir.name) / "appdata"
        with patch.dict(os.environ, {"APPDATA": str(appdata)}):
            store = SettingsStore()

        self.assertEqual(store.path, appdata / "RB3Dashboard" / "settings.json")

    def test_default_path_falls_back_to_home_when_appdata_cannot_be_created(self):
        appdata = Path(self.temp_dir.name) / "unusable-appdata"
        home = Path(self.temp_dir.name) / "home"
        real_mkdir = Path.mkdir

        def reject_appdata(path, *args, **kwargs):
            if path == appdata / "RB3Dashboard":
                raise PermissionError("denied")
            return real_mkdir(path, *args, **kwargs)

        with patch.dict(os.environ, {"APPDATA": str(appdata)}), \
             patch("os.path.expanduser", return_value=str(home)), \
             patch("pathlib.Path.mkdir", autospec=True, side_effect=reject_appdata):
            store = SettingsStore()

        self.assertEqual(store.path, home / ".rb3dashboard" / "settings.json")


class DashboardSettingsTests(unittest.TestCase):
    def test_collecting_ui_settings_preserves_unknown_loaded_keys(self):
        from dashboard import RB3Dashboard

        app = RB3Dashboard.__new__(RB3Dashboard)
        app.settings = {"future_setting": {"nested": "value"}, "database_path": "songs.dta"}
        values = {
            "api_key_var": "youtube",
            "lastfm_api_key_var": "last-key",
            "lastfm_secret_var": "last-secret",
            "lastfm_session_var": "last-session",
            "scrobble_enabled_var": True,
            "discord_enabled_var": True,
            "discord_client_id_var": "discord-id",
            "history_enabled_var": True,
            "stats_enabled_var": True,
            "song_browser_expanded_var": False,
            "video_enabled_var": True,
            "fullscreen_var": True,
            "muted_var": False,
            "always_on_top_var": False,
            "sync_var": True,
            "auto_quit_var": True,
            "delay_var": 0.5,
            "cookie_browser_var": "Firefox",
            "ha_webhook_url_var": "https://example.invalid/hook",
            "blank_screen_var": False,
        }
        for name, value in values.items():
            setattr(app, name, SimpleNamespace(get=lambda value=value: value))
        app.get_selected_monitor_index = lambda: 1

        collected = app.get_current_settings()

        self.assertEqual(collected["future_setting"], {"nested": "value"})
        self.assertEqual(collected["youtube_api_key"], "youtube")
        self.assertEqual(collected["cookie_browser"], "firefox")

    def test_shutdown_uses_store_invalid_input_preservation(self):
        from dashboard import RB3Dashboard

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            original_bytes = b"{broken"
            path.write_bytes(original_bytes)
            store = SettingsStore(path)
            loaded = store.load()

            app = RB3Dashboard.__new__(RB3Dashboard)
            app._closing = False
            app.stop_listener = lambda: None
            app.integration_worker = SimpleNamespace(close=lambda: None)
            app.settings_store = store
            app.album_art_manager = None
            app.ui_dispatcher = SimpleNamespace(close=lambda: None)
            app.is_running = False
            app.discord_presence = None
            app.root = SimpleNamespace(destroy=lambda: None)
            app.get_current_settings = lambda: loaded
            app.log_message = lambda message: None

            app.on_closing()

            backups = list(path.parent.glob("settings.json.invalid-*.bak"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), original_bytes)


if __name__ == "__main__":
    unittest.main()
