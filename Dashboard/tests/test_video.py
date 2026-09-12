"""Video service behavior with fake API responses and VLC processes."""

import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from rb3dashboard import video


class YouTubeSearcherTests(unittest.TestCase):
    def test_search_selects_matching_official_video_and_reuses_cache(self):
        client = Mock()
        client.search.return_value.list.return_value.execute.return_value = {'items': [
            {'id': {'videoId': 'cover'},
             'snippet': {'title': 'Artist - Song (cover)', 'channelTitle': 'Artist Fan'}},
            {'id': {'videoId': 'official'},
             'snippet': {'title': 'Artist - Song (Official Video)', 'channelTitle': 'ArtistVEVO'}},
        ]}
        client.videos.return_value.list.return_value.execute.return_value = {'items': [
            {'id': video_id, 'contentDetails': {'duration': 'PT3M'}}
            for video_id in ('cover', 'official')
        ]}
        database = SimpleNamespace(is_loaded=lambda: True,
                                   get_song_duration=lambda *args: 180)
        with patch.object(video, 'build', return_value=client) as build:
            searcher = video.YouTubeSearcher('test-key', song_database=database)
        build.assert_called_once_with('youtube', 'v3', developerKey='test-key')

        self.assertEqual(searcher.search_video('Artist', 'Song (Remastered)'), 'official')
        self.assertEqual(searcher.search_video('Artist', 'Song'), 'official')
        self.assertEqual(searcher.get_cached_title('official'), 'Artist - Song (Official Video)')
        self.assertEqual(client.search.call_count, 1)

    def test_failed_search_returns_no_result_and_reports_error(self):
        client = Mock()
        client.search.side_effect = RuntimeError('Service unavailable')
        logs = []
        with patch.object(video, 'build', return_value=client):
            searcher = video.YouTubeSearcher('test-key', gui_callback=logs.append)
        self.assertIsNone(searcher.search_video('Artist', 'Song'))
        self.assertIn('Search error: Service unavailable', logs)


class StreamExtractorTests(unittest.TestCase):
    def test_stream_selection_and_browser_cookies(self):
        cases = [
            ({'url': 'https://video.invalid/direct'}, 'https://video.invalid/direct'),
            ({'formats': [
                {'url': 'https://video.invalid/low', 'vcodec': 'h264'},
                {'url': 'https://video.invalid/high', 'vcodec': 'h264'},
                {'url': 'https://video.invalid/audio', 'vcodec': 'none'},
            ]}, 'https://video.invalid/high'),
            ({'formats': [{'url': 'https://video.invalid/audio', 'vcodec': 'none'}]}, None),
        ]
        for response, expected in cases:
            with self.subTest(response=response):
                with patch.object(video.yt_dlp, 'YoutubeDL') as factory:
                    downloader = factory.return_value.__enter__.return_value
                    downloader.extract_info.return_value = response
                    extractor = video.StreamExtractor(cookie_browser='Firefox')
                    self.assertEqual(extractor.get_stream_url('video-id'), expected)
                    self.assertEqual(factory.call_args.args[0]['cookiesfrombrowser'], ('firefox',))
                    downloader.extract_info.assert_called_once_with(
                        'https://www.youtube.com/watch?v=video-id', download=False,
                    )
                    extractor.set_cookie_browser('')
                    self.assertNotIn('cookiesfrombrowser', extractor.ydl_opts)

    def test_failed_extraction_returns_no_stream_and_reports_error(self):
        logs = []
        with patch.object(video.yt_dlp, 'YoutubeDL') as factory:
            factory.return_value.__enter__.return_value.extract_info.side_effect = RuntimeError('No stream')
            extractor = video.StreamExtractor(gui_callback=logs.append)
            self.assertIsNone(extractor.get_stream_url('video-id'))
        self.assertEqual(logs, ['Error extracting stream: No stream'])


class VLCPlayerTests(unittest.TestCase):
    def setUp(self):
        with patch.object(video.VLCPlayer, 'find_vlc', return_value='vlc'):
            self.player = video.VLCPlayer()

    def test_playback_applies_settings_and_selected_monitor(self):
        process = Mock()
        process.poll.return_value = None
        monitors = [SimpleNamespace(x=0, y=0), SimpleNamespace(x=1920, y=-200)]
        settings = {'video_monitor': 2, 'fullscreen': True, 'muted': True,
                    'always_on_top': True, 'force_best_quality': True}
        with patch.object(video, 'SCREENINFO_AVAILABLE', True), \
                patch.object(video, 'get_monitors', return_value=monitors), \
                patch.object(video.subprocess, 'Popen', return_value=process) as launch, \
                patch.object(video.time, 'sleep'):
            self.player.play_video('https://video.invalid/stream', 'video-id', 'Artist', 'Song',
                                   settings, 'song_shortname', 'Video title')
        launch.assert_called_once_with([
            'vlc', 'https://video.invalid/stream', '--intf', 'dummy',
            '--no-video-title-show', '--meta-title=Artist - Song',
            '--fullscreen', '--qt-fullscreen-screennumber=1', '--volume=0', '--video-on-top',
            '--video-x=2020', '--video-y=-100', '--avcodec-hw=any', '--network-caching=2000',
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertIs(self.player.current_process, process)
        self.assertEqual(list(self.player.played_videos), ['video-id'])

    def test_failed_initial_launch_retries_with_basic_command(self):
        exited, retry = Mock(), Mock()
        exited.poll.return_value = 1
        with patch.object(video.subprocess, 'Popen', side_effect=[exited, retry]) as launch, \
                patch.object(video.time, 'sleep'):
            self.player.play_video('https://video.invalid/stream', 'video-id', 'Artist', 'Song', {})
        self.assertEqual(launch.call_count, 2)
        self.assertEqual(launch.call_args.args[0], ['vlc', 'https://video.invalid/stream'])
        self.assertIs(self.player.current_process, retry)

    def test_stop_kills_unresponsive_process_and_clears_it(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired('vlc', 3)
        self.player.current_process = process
        self.player.stop_current_video()
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertIsNone(self.player.current_process)


if __name__ == '__main__':
    unittest.main()
