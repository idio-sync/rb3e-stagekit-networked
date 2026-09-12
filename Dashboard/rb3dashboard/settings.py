"""Validated, data-safe persistence for dashboard settings."""

import json
import logging
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, Optional, Union
import uuid


LOGGER = logging.getLogger(__name__)

DEFAULT_SETTINGS = {
    "youtube_api_key": "",
    "lastfm_api_key": "",
    "lastfm_api_secret": "",
    "lastfm_session_key": "",
    "scrobble_enabled": False,
    "discord_enabled": False,
    "discord_client_id": "",
    "history_enabled": True,
    "stats_enabled": True,
    "song_browser_expanded": False,
    "video_enabled": False,
    "fullscreen": True,
    "muted": True,
    "always_on_top": False,
    "sync_video_to_song": True,
    "auto_quit_on_menu": True,
    "video_start_delay": 0.0,
    "video_monitor": 0,
    "cookie_browser": "",
    "database_path": "",
    "ha_webhook_url": "",
    "blank_screen_on_song": False,
    # Following a headless server instead of the wire. Defaults keep the
    # desktop standalone, exactly as it behaved before these keys existed.
    "observe_only": False,
    "server_url": "",
    "server_token": "",
}

_STRING_FIELDS = {
    "youtube_api_key",
    "lastfm_api_key",
    "lastfm_api_secret",
    "lastfm_session_key",
    "discord_client_id",
    "cookie_browser",
    "database_path",
    "ha_webhook_url",
    "server_url",
    "server_token",
}
_BOOLEAN_FIELDS = {
    "scrobble_enabled",
    "discord_enabled",
    "history_enabled",
    "stats_enabled",
    "song_browser_expanded",
    "video_enabled",
    "fullscreen",
    "muted",
    "always_on_top",
    "sync_video_to_song",
    "auto_quit_on_menu",
    "blank_screen_on_song",
    "observe_only",
}


def _default_settings_path() -> Path:
    appdata_dir = os.environ.get("APPDATA")
    if appdata_dir:
        try:
            settings_dir = Path(appdata_dir) / "RB3Dashboard"
            settings_dir.mkdir(parents=True, exist_ok=True)
            return settings_dir / "settings.json"
        except OSError:
            pass

    try:
        settings_dir = Path(os.path.expanduser("~")) / ".rb3dashboard"
        settings_dir.mkdir(parents=True, exist_ok=True)
        return settings_dir / "settings.json"
    except OSError:
        return Path("rb3_dashboard_settings.json")


def _valid_known_value(key: str, value: Any) -> bool:
    if key in _STRING_FIELDS:
        return isinstance(value, str)
    if key in _BOOLEAN_FIELDS:
        return isinstance(value, bool)
    if key == "video_start_delay":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        try:
            finite_value = math.isfinite(value)
        except OverflowError:
            return False
        return finite_value and value >= 0
    if key == "video_monitor":
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    return True


class SettingsStore:
    """Load and atomically save the dashboard's JSON settings."""

    def __init__(self, path: Optional[Union[str, os.PathLike]] = None) -> None:
        self.path = Path(path) if path is not None else _default_settings_path()
        self._loaded = False
        self._save_allowed = True
        self._invalid_input = False
        self._original_bytes = None  # type: Optional[bytes]

    def load(self) -> Dict[str, Any]:
        """Return validated settings merged with defaults and unknown keys."""
        self._loaded = True
        self._save_allowed = True
        self._invalid_input = False
        self._original_bytes = None

        try:
            original_bytes = self.path.read_bytes()
        except FileNotFoundError:
            return DEFAULT_SETTINGS.copy()
        except OSError as exc:
            self._save_allowed = False
            LOGGER.error("Settings file %s is unreadable: %s", self.path, exc)
            return DEFAULT_SETTINGS.copy()

        self._original_bytes = original_bytes
        try:
            loaded = json.loads(original_bytes.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            self._invalid_input = True
            LOGGER.warning("Settings file %s is invalid: %s", self.path, exc)
            return DEFAULT_SETTINGS.copy()

        if not isinstance(loaded, dict):
            self._invalid_input = True
            LOGGER.warning("Settings file %s must contain a JSON object", self.path)
            return DEFAULT_SETTINGS.copy()

        result = loaded.copy()
        for key, default in DEFAULT_SETTINGS.items():
            if key not in loaded:
                result[key] = default
            elif not _valid_known_value(key, loaded[key]):
                result[key] = default
                self._invalid_input = True
                LOGGER.warning("Settings file %s has an invalid %s value", self.path, key)
        return result

    def save(self, settings: Dict[str, Any]) -> None:
        """Validate and atomically replace the settings file."""
        if not isinstance(settings, dict):
            raise TypeError("settings must be a dictionary")
        if not self._loaded:
            self.load()
        if not self._save_allowed:
            raise OSError("settings input is unreadable; refusing to overwrite it")

        normalized = settings.copy()
        for key, default in DEFAULT_SETTINGS.items():
            if key not in normalized or not _valid_known_value(key, normalized[key]):
                normalized[key] = default

        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self._invalid_input and self._original_bytes is not None:
            self._backup_invalid_input()

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix="." + self.path.name + ".",
                suffix=".tmp",
                dir=str(self.path.parent),
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
                json.dump(normalized, temp_file, indent=2, ensure_ascii=False)
                temp_file.write("\n")
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(str(temp_path), str(self.path))
            temp_path = None
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except FileNotFoundError:
                    pass

        self._loaded = True
        self._invalid_input = False
        self._original_bytes = json.dumps(
            normalized, indent=2, ensure_ascii=False
        ).encode("utf-8") + b"\n"

    def _backup_invalid_input(self) -> Path:
        """Preserve the exact invalid bytes in a unique sibling backup."""
        backup_path = self.path.with_name(
            self.path.name + ".invalid-" + uuid.uuid4().hex + ".bak"
        )
        file_descriptor = None
        try:
            file_descriptor = os.open(
                str(backup_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(file_descriptor, "wb") as backup_file:
                file_descriptor = None
                backup_file.write(self._original_bytes or b"")
                backup_file.flush()
                os.fsync(backup_file.fileno())
        except Exception:
            if file_descriptor is not None:
                os.close(file_descriptor)
            try:
                backup_path.unlink()
            except FileNotFoundError:
                pass
            raise
        return backup_path
