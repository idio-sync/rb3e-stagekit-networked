"""Song browser behavior with isolated cache files and HTTP fakes."""

from copy import deepcopy
import json
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

    def set_existing_state(self):
        self.browser.songs_data = [
            {'artist': 'Existing', 'title': 'Song', 'shortname': 'existing'},
        ]
        self.browser.artists_index = {'Existing': self.browser.songs_data.copy()}
        self.browser.rb3_ip = '192.0.2.1'

    def assert_existing_state_unchanged(self, expected):
        self.assertEqual(self.browser.songs_data, expected['songs_data'])
        self.assertEqual(self.browser.artists_index, expected['artists_index'])
        self.assertEqual(self.browser.rb3_ip, expected['rb3_ip'])

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

    def test_cache_with_valid_then_malformed_song_preserves_existing_state(self):
        self.set_existing_state()
        expected = deepcopy(self.browser.__dict__)
        self.cache_path.write_text(json.dumps({
            'songs': [
                {'artist': 'New', 'title': 'Valid', 'shortname': 'valid'},
                {'artist': 42, 'title': 'Malformed', 'shortname': 'malformed'},
            ],
            'source_ip': '192.0.2.99',
        }), encoding='utf-8')

        self.assertFalse(self.browser.load_from_cache())

        self.assert_existing_state_unchanged(expected)

    def test_cache_rejects_invalid_roots_and_gui_field_types(self):
        invalid_caches = [
            [],
            {'songs': {}},
            {'songs': [], 'source_ip': 1234},
            {'songs': [{'artist': []}]},
            {'songs': [{'title': False}]},
            {'songs': [{'album': 7}]},
            {'songs': [{'shortname': None}]},
        ]
        for cache_data in invalid_caches:
            with self.subTest(cache_data=cache_data):
                self.set_existing_state()
                expected = deepcopy(self.browser.__dict__)
                self.cache_path.write_text(json.dumps(cache_data), encoding='utf-8')

                self.assertFalse(self.browser.load_from_cache())

                self.assert_existing_state_unchanged(expected)

    def test_cache_accepts_empty_songs_and_preserves_optional_and_extra_fields(self):
        self.cache_path.write_text(json.dumps({
            'songs': [{'shortname': 'only', 'custom': {'rating': 5}}],
            'source_ip': None,
        }), encoding='utf-8')

        self.assertTrue(self.browser.load_from_cache())

        self.assertEqual(self.browser.songs_data, [
            {'shortname': 'only', 'custom': {'rating': 5}},
        ])
        self.assertEqual(self.browser.artists_index, {
            'Unknown Artist': self.browser.songs_data,
        })
        self.assertIsNone(self.browser.rb3_ip)

        self.cache_path.write_text(json.dumps({
            'songs': [],
            'source_ip': '192.0.2.50',
        }), encoding='utf-8')
        self.assertTrue(self.browser.load_from_cache())
        self.assertEqual(self.browser.songs_data, [])
        self.assertEqual(self.browser.artists_index, {})
        self.assertEqual(self.browser.rb3_ip, '192.0.2.50')

    def test_fetch_failures_return_false_and_clear_loading(self):
        self.assertFalse(self.browser.fetch_song_list(''))
        self.assertEqual(self.messages, ['No RB3Enhanced IP detected'])

        self.messages.clear()
        self.set_existing_state()
        expected = deepcopy(self.browser.__dict__)
        error = song_browser.requests.exceptions.Timeout('timed out')
        with patch.object(song_browser.requests, 'get', side_effect=error):
            self.assertFalse(self.browser.fetch_song_list('192.0.2.30'))
        self.assertFalse(self.browser.loading)
        self.assert_existing_state_unchanged(expected)
        self.assertTrue(any('timed out' in message for message in self.messages))

    def test_fetch_rejects_malformed_song_without_publishing_partial_state(self):
        self.set_existing_state()
        expected = deepcopy(self.browser.__dict__)
        response = Mock(text='ignored')
        with patch.object(song_browser.requests, 'get', return_value=response), \
                patch.object(self.browser, 'parse_ini_format', return_value=[
                    {'artist': 'New', 'title': 'Valid', 'shortname': 'valid'},
                    {'artist': 'New', 'title': object(), 'shortname': 'malformed'},
                ]):
            self.assertFalse(self.browser.fetch_song_list('192.0.2.30'))

        self.assertFalse(self.browser.loading)
        self.assert_existing_state_unchanged(expected)

    def test_play_dispatch_uses_console_jump_endpoint(self):
        self.assertFalse(self.browser.play_song('song'))
        self.browser.rb3_ip = '192.0.2.40'
        self.assertFalse(self.browser.play_song(''))

        response = song_browser.requests.Response()
        response.status_code = 204
        with patch.object(song_browser.requests, 'get', return_value=response) as get:
            self.assertTrue(self.browser.play_song('song & version'))

        get.assert_called_once_with(
            'http://192.0.2.40:21070/jump',
            params={'shortname': 'song & version'},
            timeout=10,
        )
        self.assertIn('Sent play command for: song & version', self.messages)

    def test_play_returns_false_for_http_error(self):
        self.browser.rb3_ip = '192.0.2.40'
        response = song_browser.requests.Response()
        response.status_code = 503
        response.url = 'http://192.0.2.40:21070/jump?shortname=song'

        with patch.object(song_browser.requests, 'get', return_value=response):
            self.assertFalse(self.browser.play_song('song'))

        self.assertTrue(any('503 Server Error' in message for message in self.messages))


if __name__ == '__main__':
    unittest.main()
