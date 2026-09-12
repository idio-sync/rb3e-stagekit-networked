"""Song browser behavior with isolated cache files and HTTP fakes."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from rb3dashboard import song_browser


class SongBrowserTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.cache_path = self.directory / 'song_list_cache.json'
        cache_path = patch.object(
            song_browser.SongBrowser, 'get_cache_path',
            return_value=str(self.cache_path),
        )
        cache_path.start()
        self.addCleanup(cache_path.stop)
        self.messages = []
        self.browser = song_browser.SongBrowser(gui_callback=self.messages.append)

    def test_ini_parser_preserves_equals_signs_in_values(self):
        songs = self.browser.parse_ini_format(
            '[first]\nartist = Zed\ntitle = One = One\nshortname=first\n\n'
            '[second]\nartist=Alpha\ntitle=Second\nshortname=second\n'
        )

        self.assertEqual(songs, [
            {'artist': 'Zed', 'title': 'One = One', 'shortname': 'first'},
            {'artist': 'Alpha', 'title': 'Second', 'shortname': 'second'},
        ])

    def test_fetch_indexes_songs_by_artist_and_sorts_titles(self):
        response = Mock()
        response.text = (
            '[zulu]\nartist=Band\ntitle=Zulu\nshortname=zulu\n'
            '[alpha]\nartist=Band\ntitle=Alpha\nshortname=alpha\n'
            '[unknown]\ntitle=Middle\nshortname=unknown\n'
        )
        with patch.object(song_browser.requests, 'get', return_value=response) as get:
            self.assertTrue(self.browser.fetch_song_list('192.0.2.10'))

        get.assert_called_once_with('http://192.0.2.10:21070/list_songs', timeout=30)
        response.raise_for_status.assert_called_once_with()
        self.assertEqual(
            [song['title'] for song in self.browser.artists_index['Band']],
            ['Alpha', 'Zulu'],
        )
        self.assertEqual(self.browser.artists_index['Unknown Artist'][0]['shortname'],
                         'unknown')
        self.assertFalse(self.browser.loading)

    def test_cache_round_trip_preserves_songs_source_and_sorted_index(self):
        self.browser.songs_data = [
            {'artist': 'Band', 'title': 'Zulu', 'shortname': 'zulu'},
            {'artist': 'Band', 'title': 'Alpha', 'shortname': 'alpha'},
        ]
        self.browser.rb3_ip = '192.0.2.20'
        with patch.object(song_browser.time, 'time', return_value=1234.5):
            self.assertTrue(self.browser.save_to_cache())

        self.assertTrue(self.cache_path.is_file())
        restored = song_browser.SongBrowser()
        self.assertTrue(restored.load_from_cache())
        self.assertEqual(restored.songs_data, self.browser.songs_data)
        self.assertEqual(restored.rb3_ip, '192.0.2.20')
        self.assertEqual(
            [song['shortname'] for song in restored.artists_index['Band']],
            ['alpha', 'zulu'],
        )

    def test_fetch_failures_return_false_and_clear_loading(self):
        self.assertFalse(self.browser.fetch_song_list(''))
        self.assertEqual(self.messages, ['No RB3Enhanced IP detected'])

        self.messages.clear()
        error = song_browser.requests.exceptions.Timeout('timed out')
        with patch.object(song_browser.requests, 'get', side_effect=error):
            self.assertFalse(self.browser.fetch_song_list('192.0.2.30'))
        self.assertFalse(self.browser.loading)
        self.assertEqual(self.browser.rb3_ip, '192.0.2.30')
        self.assertTrue(any('timed out' in message for message in self.messages))

    def test_play_dispatch_uses_console_jump_endpoint(self):
        self.assertFalse(self.browser.play_song('song'))
        self.browser.rb3_ip = '192.0.2.40'
        self.assertFalse(self.browser.play_song(''))

        with patch.object(song_browser.requests, 'get') as get:
            self.assertTrue(self.browser.play_song('song=version'))

        get.assert_called_once_with(
            'http://192.0.2.40:21070/jump?shortname=song=version', timeout=10,
        )
        self.assertIn('Sent play command for: song=version', self.messages)


if __name__ == '__main__':
    unittest.main()
