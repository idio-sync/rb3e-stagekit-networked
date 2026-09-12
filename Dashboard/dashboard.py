# RB3Enhanced Unified Dashboard
# Combines Stage Kit Fleet Manager with Music Video Player

# Standard-library startup checks must not install or contact anything.
import importlib.util

_REQUIRED_DEPENDENCIES = {
    "googleapiclient": "google-api-python-client",
    "yt_dlp": "yt-dlp",
    "PIL": "Pillow",
    "requests": "requests",
}


def _missing_dependencies():
    return [
        package_name
        for import_name, package_name in _REQUIRED_DEPENDENCIES.items()
        if importlib.util.find_spec(import_name) is None
    ]


if __name__ == "__main__":
    missing_dependencies = _missing_dependencies()
    if missing_dependencies:
        raise SystemExit(
            "Missing dashboard dependencies: "
            + ", ".join(missing_dependencies)
            + ". From the repository root, run: "
            + "python -m pip install -r Dashboard/requirements.txt"
        )

from rb3dashboard.album_art import AlbumArtManager, LRUCache
from rb3dashboard.desktop.app import RB3Dashboard
from rb3dashboard.dispatch import UiDispatcher
from rb3dashboard.integrations import DISCORD_AVAILABLE, DiscordPresence, LastFmScrobbler
from rb3dashboard.listener import UnifiedRB3EListener
from rb3dashboard.settings import SettingsStore
from rb3dashboard.song_browser import SongBrowser
from rb3dashboard.song_data import PlayStatistics, SongDatabase, SongHistory
from rb3dashboard.stagekit import StageKitTransport
from rb3dashboard.video import (
    SCREENINFO_AVAILABLE,
    StreamExtractor,
    VLCPlayer,
    YouTubeSearcher,
    get_monitors,
)

if not DISCORD_AVAILABLE:
    print("pypresence not available - Discord Rich Presence will be disabled")

if not SCREENINFO_AVAILABLE:
    print("screeninfo not available - multi-monitor selection will be disabled")


def main():
    app = RB3Dashboard()
    app.run()


if __name__ == "__main__":
    main()
