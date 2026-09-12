"""Song data and file-format compatibility using isolated temporary files."""

import csv
from datetime import datetime
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from rb3dashboard import song_data


class TemporaryFilesTestCase(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)


class SongDatabaseTests(TemporaryFilesTestCase):
    def test_loads_bom_encodings_and_looks_up_song_metadata(self):
        data = {'setlist': [
            {'shortname': 'cafe', 'name': 'Caf\u00e9', 'artist': 'Artist', 'album': 'Album',
             'duration': '2:17', 'year_released': 2000, 'genre': 'Rock'},
            {'name': 'No shortname'},
        ]}
        for encoding in ('utf-8-sig', 'utf-16'):
            with self.subTest(encoding=encoding):
                path = self.directory / (encoding + '.json')
                path.write_text(json.dumps(data, ensure_ascii=False), encoding=encoding)
                database = song_data.SongDatabase()
                self.assertTrue(database.load_database(str(path)))
                self.assertEqual(database.get_stats(), {
                    'loaded_count': 1, 'database_path': str(path), 'has_data': True,
                })
                self.assertEqual(database.lookup_song('cafe'), {
                    'shortname': 'cafe', 'name': 'Caf\u00e9', 'artist': 'Artist', 'album': 'Album',
                    'duration_str': '2:17', 'duration_seconds': 137,
                    'year_released': 2000, 'genre': 'Rock',
                })
                self.assertEqual(database.get_song_duration(None, 'ARTIST', 'CAF\u00c9'), 137)
                self.assertIsNone(database.lookup_song('missing'))

    def test_unreadable_database_reports_failure_and_keeps_loaded_songs(self):
        path = self.directory / 'songs.json'
        path.write_text(json.dumps({'setlist': [
            {'shortname': 'song', 'name': 'Song', 'artist': 'Artist', 'duration': '120'},
        ]}), encoding='utf-8')
        logs = []
        database = song_data.SongDatabase(gui_callback=logs.append)
        self.assertTrue(database.load_database(str(path)))
        path.write_text('{invalid json', encoding='utf-8')
        self.assertFalse(database.load_database(str(path)))
        self.assertEqual(database.get_song_duration('song'), 120)
        self.assertTrue(any(message.startswith('Failed to load song database:') for message in logs))


class SongHistoryTests(TemporaryFilesTestCase):
    def test_session_tracks_duration_order_and_disabled_recording(self):
        history = song_data.SongHistory()
        history.add_song('Artist', 'First')
        history.update_last_song_duration(180)
        history.add_song('Artist', 'Second')
        history.update_last_song_duration(60)
        history.enabled = False
        history.add_song('Artist', 'Ignored')
        self.assertEqual([entry['song'] for entry in history.get_history()], ['Second', 'First'])
        self.assertEqual([entry['duration'] for entry in history.get_history()], [60, 180])
        self.assertEqual(history.get_count(), 2)
        self.assertEqual(history.get_total_time(), 240)
        self.assertEqual(history.get_total_time_formatted(), '4m')
        history.clear()
        self.assertEqual(history.get_history(), [])
        self.assertEqual(history.get_total_time(), 0)

    def test_csv_and_json_exports_preserve_existing_formats(self):
        history = song_data.SongHistory()
        now = datetime(2026, 1, 1, 12, 30)
        with patch.object(song_data, 'datetime') as clock:
            clock.now.return_value = now
            history.add_song('Artist, Duo', 'Caf\u00e9 "Live"', 'Album\nPart 2', 'cafe')
            history.update_last_song_duration(137)
            csv_path, json_path = self.directory / 'history.csv', self.directory / 'history.json'
            history.export_to_csv(str(csv_path))
            history.export_to_json(str(json_path))
        with csv_path.open(encoding='utf-8', newline='') as exported:
            self.assertEqual(list(csv.reader(exported)), [
                ['Timestamp', 'Artist', 'Song', 'Album'],
                [now.isoformat(), 'Artist, Duo', 'Caf\u00e9 "Live"', 'Album\nPart 2'],
            ])
        self.assertEqual(json.loads(json_path.read_text(encoding='utf-8')), {
            'exported': now.isoformat(),
            'session_history': [{
                'timestamp': now.isoformat(), 'artist': 'Artist, Duo', 'song': 'Caf\u00e9 "Live"',
                'album': 'Album\nPart 2', 'shortname': 'cafe', 'duration': 137,
            }],
        })


class PlayStatisticsTests(TemporaryFilesTestCase):
    def test_statistics_survive_reload_and_combine_song_name_case(self):
        path = self.directory / 'stats.json'
        now = datetime(2026, 1, 1, 12, 30)
        with patch.object(song_data, 'datetime') as clock:
            clock.now.return_value = now
            stats = song_data.PlayStatistics(str(path))
            stats.record_play('Artist', 'Song')
            stats.add_play_time('Artist', 'Song', 180)
            stats.record_play('ARTIST', 'SONG')
            stats.add_play_time('artist', 'song', 60)
            stats.record_play('Other', 'Another')
        reloaded = song_data.PlayStatistics(str(path))
        self.assertEqual(reloaded.get_total_plays(), 3)
        self.assertEqual(reloaded.get_unique_songs(), 2)
        self.assertEqual(reloaded.get_total_time(), 240)
        self.assertEqual(reloaded.get_total_time_formatted(), '4m')
        self.assertEqual(reloaded.get_top_songs(1), [{
            'artist': 'Artist', 'song': 'Song', 'play_count': 2, 'total_time_seconds': 240,
            'first_played': now.isoformat(), 'last_played': now.isoformat(),
        }])
        saved = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(set(saved), {'total_plays', 'total_time_seconds', 'songs', 'first_tracked'})
        self.assertEqual(saved['first_tracked'], now.isoformat())
        self.assertEqual(set(saved['songs']), {'artist|song', 'other|another'})

    def test_missing_or_malformed_file_starts_with_empty_statistics(self):
        path = self.directory / 'stats.json'
        for content in (None, '{invalid json'):
            with self.subTest(content=content):
                if content is not None:
                    path.write_text(content, encoding='utf-8')
                stats = song_data.PlayStatistics(str(path))
                self.assertEqual(stats.get_total_plays(), 0)
                self.assertEqual(stats.get_total_time(), 0)
                self.assertEqual(stats.get_top_songs(), [])

    def test_default_statistics_path_uses_appdata(self):
        with patch.dict(os.environ, {'APPDATA': str(self.directory)}):
            stats = song_data.PlayStatistics()
        self.assertEqual(Path(stats.stats_path), self.directory / 'RB3Dashboard' / 'play_stats.json')
        stats.record_play('Artist', 'Song')
        self.assertTrue(Path(stats.stats_path).is_file())

    def test_default_statistics_path_uses_home_when_appdata_is_unavailable(self):
        with patch.dict(os.environ, {'APPDATA': ''}), \
                patch.object(song_data.os.path, 'expanduser', return_value=str(self.directory)):
            stats = song_data.PlayStatistics()
        self.assertEqual(Path(stats.stats_path), self.directory / '.rb3dashboard' / 'play_stats.json')
        stats.record_play('Artist', 'Song')
        self.assertTrue(Path(stats.stats_path).is_file())

    def test_last_resort_path_stays_beside_desktop_entry_point(self):
        entry_directory = self.directory / 'app'
        entry_directory.mkdir()
        module_path = entry_directory / 'rb3dashboard' / 'song_data.py'
        with patch.dict(os.environ, {'APPDATA': str(self.directory / 'appdata')}), \
                patch.object(song_data.os.path, 'expanduser', return_value=str(self.directory / 'home')), \
                patch.object(song_data.os, 'makedirs', side_effect=OSError('Directory unavailable')), \
                patch.object(song_data, '__file__', str(module_path)):
            stats = song_data.PlayStatistics()
        self.assertEqual(Path(stats.stats_path), entry_directory / 'play_stats.json')
        stats.record_play('Artist', 'Song')
        self.assertTrue(Path(stats.stats_path).is_file())


if __name__ == '__main__':
    unittest.main()
