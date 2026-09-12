"""Control timer delivery explicitly to exercise cancellation without sleeping."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rb3dashboard.listener import UnifiedRB3EListener


def packet(event_type, payload):
    return b"RB3E" + bytes((0, event_type, len(payload), 0)) + payload


class ControlledTimer:
    def __init__(self, interval, function, args=(), kwargs=None):
        self.interval = interval
        self.function = function
        self.args = args
        self.kwargs = kwargs or {}
        self.started = False
        self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def deliver(self):
        # Deliver even after cancellation to simulate a callback which has
        # already left Timer.wait() when the song ends.
        self.function(*self.args, **self.kwargs)


class VideoLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.timers = []
        self.plays = []
        self.stops = []
        self.listener = UnifiedRB3EListener()
        self.listener.running = True
        self.listener.set_video_components(
            SimpleNamespace(search_video=lambda artist, song: song + '-video',
                            get_cached_title=lambda video_id: video_id),
            SimpleNamespace(play_video=lambda *args: self.plays.append(args),
                            stop_current_video=lambda: self.stops.append('stop')),
            SimpleNamespace(get_stream_url=lambda video_id: 'https://video.invalid/' + video_id),
        )
        self.settings = {'video_start_delay': 5, 'auto_quit_on_menu': True}
        self.listener.update_video_settings(self.settings, enabled=True)

        def timer(*args, **kwargs):
            result = ControlledTimer(*args, **kwargs)
            self.timers.append(result)
            return result

        self.timer_patch = patch('rb3dashboard.listener.threading.Timer', side_effect=timer)
        self.timer_patch.start()
        self.addCleanup(self.timer_patch.stop)

    def select_song(self, song='Song'):
        self.listener.process_packet(packet(2, song.encode()))
        self.listener.process_packet(packet(3, b'Artist'))
        self.listener.process_packet(packet(4, song.lower().encode()))

    def start_song(self, song='Song'):
        self.select_song(song)
        self.listener.process_packet(packet(1, b'\x01'))
        self.assertTrue(self.timers[-1].started)
        return self.timers[-1]

    def end_song(self):
        self.listener.process_packet(packet(1, b'\x00'))

    def test_current_song_plays_after_requested_delay(self):
        timer = self.start_song()
        self.assertEqual(timer.interval, 5)
        self.assertEqual(self.plays, [])
        timer.deliver()
        self.assertEqual(len(self.plays), 1)
        self.assertEqual(self.plays[0][1:4], ('Song-video', 'Artist', 'Song'))
        self.assertIsNone(self.listener.pending_video)

    def test_menu_cancels_timer_and_already_queued_callback(self):
        timer = self.start_song()
        self.end_song()
        timer.deliver()
        self.assertEqual(self.plays, [])
        self.assertTrue(timer.cancelled)
        self.assertIsNone(self.listener.pending_video)

    def test_menu_cancels_pending_start_when_auto_quit_is_disabled(self):
        self.settings['auto_quit_on_menu'] = False
        self.listener.update_video_settings(self.settings, enabled=True)
        timer = self.start_song()
        self.end_song()
        timer.deliver()
        self.assertEqual(self.plays, [])
        self.assertTrue(timer.cancelled)
        self.assertEqual(self.stops, [])

    def test_menu_preserves_already_started_video_when_auto_quit_is_disabled(self):
        self.settings['auto_quit_on_menu'] = False
        self.listener.update_video_settings(self.settings, enabled=True)
        self.start_song().deliver()
        self.end_song()
        self.assertEqual(len(self.plays), 1)
        self.assertEqual(self.stops, [])

    def test_stopped_listener_cancels_timer_even_if_later_restarted(self):
        timer = self.start_song()
        self.listener.stop()
        self.listener.running = True
        timer.deliver()
        self.assertEqual(self.plays, [])
        self.assertTrue(timer.cancelled)
        self.assertIsNone(self.listener.pending_video)

    def test_disabling_and_reenabling_video_does_not_revive_old_timer(self):
        timer = self.start_song()
        self.listener.update_video_settings(self.settings, enabled=False)
        self.listener.update_video_settings(self.settings, enabled=True)
        timer.deliver()
        self.assertEqual(self.plays, [])
        self.assertTrue(timer.cancelled)

    def test_old_timer_cannot_play_during_a_replay_of_the_same_song(self):
        old_timer = self.start_song()
        self.end_song()
        new_timer = self.start_song()
        old_timer.deliver()
        self.assertEqual(self.plays, [])
        new_timer.deliver()
        self.assertEqual(len(self.plays), 1)
        self.assertFalse(new_timer.cancelled)

    def test_metadata_change_invalidates_previous_song_timer(self):
        old_timer = self.start_song()
        self.select_song('Next')
        new_timer = self.timers[-1]
        old_timer.deliver()
        self.assertEqual(self.plays, [])
        self.assertTrue(old_timer.cancelled)
        new_timer.deliver()
        self.assertEqual(self.plays[0][3], 'Next')

    def test_repeated_start_pending_video_does_not_schedule_twice(self):
        timer = self.start_song()
        self.listener.start_pending_video()
        self.listener.start_pending_video()
        self.assertEqual(self.timers, [timer])
        timer.deliver()
        self.assertEqual(len(self.plays), 1)

    def test_repeated_metadata_does_not_restart_the_countdown(self):
        timer = self.start_song()
        self.select_song()
        self.assertEqual(self.timers, [timer])
        self.assertFalse(timer.cancelled)
        timer.deliver()
        self.assertEqual(len(self.plays), 1)
        self.assertIsNone(self.listener.pending_video)

    def test_end_callback_cannot_deliver_a_stale_timer_before_cleanup(self):
        timer = self.start_song()
        self.listener.song_ended_callback = lambda *args: timer.deliver()
        self.end_song()
        self.assertEqual(self.plays, [])
        self.assertTrue(timer.cancelled)

    def test_failed_end_callback_still_invalidates_video(self):
        timer = self.start_song()

        def fail(*args):
            raise RuntimeError('integration failed')

        self.listener.song_ended_callback = fail
        self.end_song()
        timer.deliver()
        self.assertEqual(self.plays, [])
        self.assertTrue(timer.cancelled)

    def test_lookup_finishing_after_song_ends_is_discarded(self):
        self.start_song().deliver()

        def extract(video_id):
            self.end_song()
            return 'https://video.invalid/stale'

        self.listener.stream_extractor.get_stream_url = extract
        self.listener.prepare_video()
        self.assertIsNone(self.listener.pending_video)
        self.assertEqual(len(self.plays), 1)

    def test_lookup_finishing_after_stop_is_discarded(self):
        self.select_song()

        def extract(video_id):
            self.listener.stop()
            return 'https://video.invalid/stale'

        self.listener.stream_extractor.get_stream_url = extract
        self.listener.prepare_video()
        self.assertIsNone(self.listener.pending_video)

    def test_old_lookup_cannot_overwrite_new_song_result(self):
        self.select_song()
        initial_extractor = self.listener.stream_extractor.get_stream_url

        def extract(video_id):
            self.listener.stream_extractor.get_stream_url = initial_extractor
            self.select_song('Next')
            return 'https://video.invalid/stale'

        self.listener.stream_extractor.get_stream_url = extract
        self.listener.prepare_video()
        self.listener.process_packet(packet(1, b'\x01'))
        self.timers[-1].deliver()
        self.assertEqual(self.plays[0][3], 'Next')

    def test_finishing_playback_start_does_not_clear_new_pending_video(self):
        timer = self.start_song()

        def play(*args):
            self.plays.append(args)
            self.end_song()
            self.select_song('Next')

        self.listener.vlc_player.play_video = play
        timer.deliver()
        self.assertIsNotNone(self.listener.pending_video)
        self.assertEqual(self.listener.pending_video[3], 'Next')


if __name__ == '__main__':
    unittest.main()
