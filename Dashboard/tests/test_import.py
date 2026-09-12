"""Reusable dashboard modules must not start the desktop application."""

from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


class ListenerImportTests(unittest.TestCase):
    def test_import_does_not_need_desktop_dependencies_or_start_services(self):
        script = textwrap.dedent("""\
            import importlib.abc
            import sys
            import threading
            from unittest.mock import patch

            class BlockDesktopImports(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.split('.')[0] in {
                        'dashboard', 'tkinter', 'PIL', 'googleapiclient',
                        'yt_dlp', 'pypresence', 'screeninfo',
                    }:
                        raise AssertionError('Desktop dependency: ' + fullname)

            def block_side_effects(event, args):
                if event in {'socket.__new__', 'subprocess.Popen', 'os.system'}:
                    raise AssertionError('Startup side effect: ' + event)

            sys.meta_path.insert(0, BlockDesktopImports())
            sys.addaudithook(block_side_effects)
            with patch.object(threading.Thread, 'start',
                              side_effect=AssertionError('Started a thread')):
                from rb3dashboard.listener import UnifiedRB3EListener
                UnifiedRB3EListener()
            """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class VideoImportTests(unittest.TestCase):
    def test_video_import_needs_no_ui_or_startup_services(self):
        script = textwrap.dedent("""\
            import importlib.abc
            import sys
            import threading
            from unittest.mock import patch

            without_screeninfo = sys.argv[1] == 'True'

            class BlockUIImports(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.split('.')[0] in {'dashboard', 'tkinter', 'PIL', 'pypresence'}:
                        raise AssertionError('UI dependency: ' + fullname)
                    if without_screeninfo and fullname.split('.')[0] == 'screeninfo':
                        raise ImportError('Simulated missing optional screeninfo')

            def block_side_effects(event, args):
                if event in {'socket.__new__', 'subprocess.Popen', 'os.system'}:
                    raise AssertionError('Startup side effect: ' + event)

            sys.meta_path.insert(0, BlockUIImports())
            sys.addaudithook(block_side_effects)
            with patch.object(threading.Thread, 'start',
                              side_effect=AssertionError('Started a thread')):
                from rb3dashboard.video import (
                    YouTubeSearcher, StreamExtractor, VLCPlayer, SCREENINFO_AVAILABLE,
                )
                assert YouTubeSearcher('').search_video('Artist', 'Song') is None
                StreamExtractor()
                if without_screeninfo:
                    assert not SCREENINFO_AVAILABLE
            """)
        for without_screeninfo in (False, True):
            with self.subTest(without_screeninfo=without_screeninfo):
                result = subprocess.run(
                    [sys.executable, '-c', script, str(without_screeninfo)],
                    cwd=Path(__file__).resolve().parents[1],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class SongDataImportTests(unittest.TestCase):
    def test_song_data_import_uses_only_stdlib_and_starts_no_services(self):
        script = textwrap.dedent("""\
            import importlib.abc
            import sys
            import threading
            from unittest.mock import patch

            class BlockUIImports(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.split('.')[0] in {'dashboard', 'tkinter'}:
                        raise AssertionError('UI dependency: ' + fullname)

            def block_side_effects(event, args):
                if event in {'socket.__new__', 'subprocess.Popen', 'os.system', 'os.mkdir'}:
                    raise AssertionError('Startup side effect: ' + event)

            sys.dont_write_bytecode = True
            sys.meta_path.insert(0, BlockUIImports())
            sys.addaudithook(block_side_effects)
            with patch.object(threading.Thread, 'start',
                              side_effect=AssertionError('Started a thread')):
                from rb3dashboard.song_data import SongDatabase, SongHistory, PlayStatistics
                assert not SongDatabase().is_loaded()
                assert SongHistory().get_count() == 0
            """)
        result = subprocess.run(
            [sys.executable, '-S', '-c', script],  # No installed third-party packages.
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class IntegrationsImportTests(unittest.TestCase):
    def test_integrations_import_needs_no_ui_or_startup_services(self):
        script = textwrap.dedent("""\
            import importlib.abc
            import sys
            import threading
            from unittest.mock import patch

            without_discord = sys.argv[1] == 'True'

            class BlockDesktopImports(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.split('.')[0] in {
                        'dashboard', 'tkinter', 'PIL', 'googleapiclient',
                        'yt_dlp', 'screeninfo',
                    }:
                        raise AssertionError('Desktop dependency: ' + fullname)
                    if without_discord and fullname.split('.')[0] == 'pypresence':
                        raise ImportError('Simulated missing optional pypresence')

            def block_side_effects(event, args):
                if event in {'socket.__new__', 'subprocess.Popen', 'os.system'}:
                    raise AssertionError('Startup side effect: ' + event)

            sys.meta_path.insert(0, BlockDesktopImports())
            sys.addaudithook(block_side_effects)
            with patch.object(threading.Thread, 'start',
                              side_effect=AssertionError('Started a thread')):
                from rb3dashboard.integrations import (
                    LastFmScrobbler, DiscordPresence, DISCORD_AVAILABLE,
                )
                scrobbler = LastFmScrobbler()
                scrobbler.update_now_playing('Artist', 'Song')
                scrobbler.scrobble('Artist', 'Song')
                assert scrobbler.get_auth_token() is None
                presence = DiscordPresence()
                presence.update_presence('Artist', 'Song')
                presence.set_idle()
                if without_discord:
                    assert not DISCORD_AVAILABLE
                    assert presence.connect() is False
            """)
        for without_discord in (False, True):
            with self.subTest(without_discord=without_discord):
                result = subprocess.run(
                    [sys.executable, '-c', script, str(without_discord)],
                    cwd=Path(__file__).resolve().parents[1],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class SongBrowserImportTests(unittest.TestCase):
    def test_song_browser_import_needs_no_desktop_or_startup_services(self):
        script = textwrap.dedent("""\
            import importlib.abc
            import sys
            import threading
            from unittest.mock import patch

            class BlockDesktopImports(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if (fullname == 'dashboard'
                            or fullname == 'rb3dashboard.video'
                            or fullname.split('.')[0] in {'tkinter', 'PIL'}):
                        raise AssertionError('Desktop dependency: ' + fullname)

            def block_side_effects(event, args):
                if event in {'socket.__new__', 'subprocess.Popen', 'os.system'}:
                    raise AssertionError('Startup side effect: ' + event)

            sys.meta_path.insert(0, BlockDesktopImports())
            sys.addaudithook(block_side_effects)
            with patch.object(threading.Thread, 'start',
                              side_effect=AssertionError('Started a thread')):
                from rb3dashboard.song_browser import SongBrowser
                browser = SongBrowser()
                assert browser.songs_data == []
                assert browser.artists_index == {}
            """)
        result = subprocess.run(
            [sys.executable, '-c', script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
