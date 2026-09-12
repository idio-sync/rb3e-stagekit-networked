"""Real listener/player ownership at blocked external process boundaries."""

from contextlib import ExitStack
import subprocess
import threading
import unittest
from unittest.mock import patch

from rb3dashboard.listener import UnifiedRB3EListener
from rb3dashboard import video
from test_desktop_lifecycle import make_app


class Process:
    def __init__(self, exited=False):
        self.exited = exited
        self.terminated = self.killed = 0

    def poll(self):
        return 1 if self.exited else None

    def terminate(self):
        self.terminated += 1
        self.exited = True

    def wait(self, timeout):
        return 0

    def kill(self):
        self.killed += 1
        self.exited = True


class VLCPlayerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(video.VLCPlayer, 'find_vlc', return_value='fake-vlc'))
        self.stack.enter_context(patch.object(video.time, 'sleep'))
        self.player = video.VLCPlayer()
        self.listener = UnifiedRB3EListener()
        self.listener.vlc_player = self.player
        self.listener.running = self.listener.video_enabled = True
        self.listener.game_state = 1
        self.listener.video_settings = {'video_monitor': 1, 'auto_quit_on_menu': True}
        self.errors = []

    def start_blocked(self, action):
        entered, release = threading.Event(), threading.Event()
        def barrier(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError('External-work barrier was not released')
            return action(*args, **kwargs)
        return entered, release, barrier

    def run_worker(self, callback):
        def invoke():
            try:
                callback()
            except Exception as error:
                self.errors.append(error)
        worker = threading.Thread(target=invoke)
        worker.start()
        return worker

    def play(self, video_id='old'):
        self.player.play_video('https://video.invalid/stream', video_id, 'Artist', 'Song', {})

    def play_from_listener(self):
        self.listener._play_video_now(self.listener._video_generation,
            ('https://video.invalid/stream', 'old', 'Artist', 'Song', 'short', 'Title'))

    def finish(self, worker, release):
        release.set()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.errors, [])

    def test_menu_during_monitor_lookup_prevents_launch_without_blocking_listener_lock(self):
        entered, release, monitors = self.start_blocked(lambda: [])
        with patch.object(video, 'SCREENINFO_AVAILABLE', True), \
                patch.object(video, 'get_monitors', side_effect=monitors), \
                patch.object(video.subprocess, 'Popen', return_value=Process()) as launch:
            worker = self.run_worker(self.play_from_listener)
            try:
                self.assertTrue(entered.wait(2))
                self.assertTrue(self.listener._state_lock.acquire(timeout=1))
                self.listener._state_lock.release()
                self.listener.handle_state_change('0')
                self.assertEqual(self.listener.game_state, 0)
            finally:
                self.finish(worker, release)
        self.assertEqual(launch.call_count, 0)
        self.assertIsNone(self.player.current_process)
        self.assertEqual(list(self.player.played_videos), [])

    def test_desktop_stop_and_close_during_monitor_lookup_prevent_launch(self):
        for close in (False, True):
            with self.subTest(close=close):
                self.listener.running = True
                app = make_app()
                app.is_running = True
                app.listener, app.vlc_player = self.listener, self.player
                entered, release, monitors = self.start_blocked(lambda: [])
                with patch.object(video, 'SCREENINFO_AVAILABLE', True), \
                        patch.object(video, 'get_monitors', side_effect=monitors), \
                        patch.object(video.subprocess, 'Popen', return_value=Process()) as launch:
                    worker = self.run_worker(self.play_from_listener)
                    try:
                        self.assertTrue(entered.wait(2))
                        app.on_closing() if close else app.stop_listener()
                    finally:
                        self.finish(worker, release)
                        app.ui_dispatcher.close()
                self.assertEqual(launch.call_count, 0)
                self.assertIsNone(self.player.current_process)

    def test_menu_during_published_startup_respects_auto_quit_setting(self):
        for auto_quit in (False, True):
            with self.subTest(auto_quit=auto_quit):
                self.listener.game_state = 1
                self.listener.video_settings = {'auto_quit_on_menu': auto_quit}
                process = Process()
                entered, release, startup = self.start_blocked(lambda seconds: None)
                with patch.object(video.time, 'sleep', side_effect=startup), \
                        patch.object(video.subprocess, 'Popen', return_value=process) as launch:
                    worker = self.run_worker(self.play_from_listener)
                    try:
                        self.assertTrue(entered.wait(2))
                        self.assertIs(self.player.current_process, process)
                        self.listener.handle_state_change('0')
                    finally:
                        self.finish(worker, release)
                self.assertEqual(launch.call_count, 1)
                self.assertEqual(process.terminated, 1 if auto_quit else 0)
                if auto_quit:
                    self.assertIsNone(self.player.current_process)
                else:
                    self.assertIs(self.player.current_process, process)
                    self.assertIsNone(process.poll())

    def test_desktop_stop_and_close_stop_published_startup_with_auto_quit_disabled(self):
        for close in (False, True):
            with self.subTest(close=close):
                self.listener.running = True
                self.listener.video_settings = {'auto_quit_on_menu': False}
                app = make_app()
                app.is_running = True
                app.listener, app.vlc_player = self.listener, self.player
                process = Process()
                entered, release, startup = self.start_blocked(lambda seconds: None)
                with patch.object(video.time, 'sleep', side_effect=startup), \
                        patch.object(video.subprocess, 'Popen', return_value=process):
                    worker = self.run_worker(self.play_from_listener)
                    try:
                        self.assertTrue(entered.wait(2))
                        self.assertIs(self.player.current_process, process)
                        app.on_closing() if close else app.stop_listener()
                    finally:
                        self.finish(worker, release)
                        app.ui_dispatcher.close()
                self.assertEqual(process.terminated, 1)
                self.assertIsNone(self.player.current_process)

    def test_menu_with_auto_quit_disabled_still_disposes_unpublished_launch(self):
        self.listener.video_settings = {'auto_quit_on_menu': False}
        process = Process()
        entered, release, launch = self.start_blocked(lambda *args, **kwargs: process)
        with patch.object(video.subprocess, 'Popen', side_effect=launch):
            worker = self.run_worker(self.play_from_listener)
            try:
                self.assertTrue(entered.wait(2))
                self.assertIsNone(self.player.current_process)
                self.listener.handle_state_change('0')
            finally:
                self.finish(worker, release)
        self.assertEqual(process.terminated, 1)
        self.assertIsNone(self.player.current_process)

    def test_listener_stop_during_process_creation_disposes_late_process(self):
        process = Process()
        entered, release, launch = self.start_blocked(lambda *args, **kwargs: process)
        with patch.object(video.subprocess, 'Popen', side_effect=launch):
            worker = self.run_worker(self.play_from_listener)
            try:
                self.assertTrue(entered.wait(2))
                self.listener.stop()
            finally:
                self.finish(worker, release)
        self.assertEqual(process.terminated, 1)
        self.assertIsNone(self.player.current_process)
        self.assertEqual(list(self.player.played_videos), [])

    def test_stop_during_exit_poll_cancels_fallback(self):
        process = Process(exited=True)
        entered, release, poll = self.start_blocked(lambda: 1)
        # Only the playback worker blocks; stop may poll its detached process.
        process.poll = lambda: poll() if threading.current_thread() is not threading.main_thread() else 1
        with patch.object(video.subprocess, 'Popen', return_value=process) as launch:
            worker = self.run_worker(self.play)
            try:
                self.assertTrue(entered.wait(2))
                self.player.stop_current_video()
            finally:
                self.finish(worker, release)
        self.assertEqual(launch.call_count, 1)
        self.assertIsNone(self.player.current_process)
        self.assertEqual(list(self.player.played_videos), [])

    def test_stop_during_fallback_creation_disposes_retry(self):
        first, retry = Process(exited=True), Process()
        entered, release, fallback = self.start_blocked(lambda: retry)
        attempts = []
        def launch(*args, **kwargs):
            attempts.append(args[0])
            return first if len(attempts) == 1 else fallback()
        with patch.object(video.subprocess, 'Popen', side_effect=launch):
            worker = self.run_worker(self.play)
            try:
                self.assertTrue(entered.wait(2))
                self.player.stop_current_video()
            finally:
                self.finish(worker, release)
        self.assertEqual(retry.terminated, 1)
        self.assertIsNone(self.player.current_process)
        self.assertEqual(list(self.player.played_videos), [])

    def test_late_launch_cannot_overwrite_or_stop_replacement(self):
        old, replacement = Process(), Process()
        entered, release, old_launch = self.start_blocked(lambda: old)
        attempts = []
        def launch(*args, **kwargs):
            attempts.append(args[0])
            return old_launch() if len(attempts) == 1 else replacement
        with patch.object(video.subprocess, 'Popen', side_effect=launch):
            worker = self.run_worker(self.play)
            try:
                self.assertTrue(entered.wait(2))
                self.play('replacement')
            finally:
                self.finish(worker, release)
        self.assertEqual(old.terminated, 1)
        self.assertEqual(replacement.terminated, 0)
        self.assertIs(self.player.current_process, replacement)
        self.assertEqual(list(self.player.played_videos), ['replacement'])

    def test_old_stop_timeout_kills_only_detached_process(self):
        old, replacement = Process(), Process()
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired('fake-vlc', 3)
        entered, release, wait = self.start_blocked(timeout)
        old.wait = wait
        self.player.current_process = old
        with patch.object(video.subprocess, 'Popen', return_value=replacement):
            worker = self.run_worker(self.player.stop_current_video)
            try:
                self.assertTrue(entered.wait(2))
                self.play('replacement')
            finally:
                self.finish(worker, release)
        self.assertEqual(old.killed, 1)
        self.assertEqual(replacement.killed, 0)
        self.assertEqual(replacement.terminated, 0)
        self.assertIs(self.player.current_process, replacement)


if __name__ == '__main__':
    unittest.main()
