"""Headless desktop lifecycle checks with owner-aware Tk and I/O barriers."""

from contextlib import ExitStack
import json
from pathlib import Path
import threading
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from rb3dashboard.desktop.app import RB3Dashboard
from rb3dashboard.dispatch import UiDispatcher
from rb3dashboard.song_browser import SongBrowser
from test_dispatch import OwnerRoot


class Root(OwnerRoot):
    def after(self, delay, callback, *args):
        return super().after(delay, lambda: callback(*args))

    def destroy(self):
        self.check_owner()


class Widget:
    def __init__(self, root, value=''):
        self.root, self.value = root, value
        self.options = {}

    def config(self, **kwargs):
        self.root.check_owner()
        self.options.update(kwargs)

    def get(self):
        self.root.check_owner()
        return self.value

    def set(self, value):
        self.root.check_owner()
        self.value = value

    def pack(self, **kwargs):
        self.root.check_owner()

    def pack_forget(self):
        self.root.check_owner()

    def winfo_ismapped(self):
        self.root.check_owner()
        return False


class Listener:
    def __init__(self, **callbacks):
        self.callbacks = callbacks
        self.release = threading.Event()

    def prepare_start(self):
        return object()

    def start_listening(self, token=None):
        self.release.wait(3)

    def stop(self):
        self.release.set()

    def set_webhook_url(self, url):
        pass

    def update_video_settings(self, *args):
        pass


def make_app():
    app = RB3Dashboard.__new__(RB3Dashboard)
    app.root = Root()
    app.ui_dispatcher = UiDispatcher(app.root)
    app._generation = app._browser_generation = app._artwork_generation = 0
    app._integration_generation = app._scrobble_generation = 0
    app._closing = False
    app.is_running = False
    app.detected_ip = None
    app.listener = app.listener_thread = app.stagekit_transport = None
    app.vlc_player = app.youtube_searcher = app.stream_extractor = None
    app.device_cleanup_timer_id = app.scrobble_timer_id = None
    app.search_timer_id = app.startup_timer_id = None
    app.blank_screen_window = None
    app.devices = {}
    app.settings = {'scrobble_enabled': True, 'discord_enabled': True}
    app.settings_store = Mock()
    app.song_database = None
    app.song_browser = SongBrowser()
    app.album_art_manager = None
    app.scrobbler = Mock(enabled=True)
    app.discord_presence = Mock(enabled=True)
    app.integration_worker = Mock()
    app.song_history = Mock(enabled=True)
    app.play_stats = Mock()
    app.refresh_history_display = Mock()
    app._update_log = Mock()
    app.get_video_settings = lambda: {}
    app.get_current_settings = lambda: app.settings.copy()
    app.populate_song_tree = Mock()
    for name in ('status_label', 'ip_status_label', 'web_ui_button',
                 'load_songs_button', 'activity_content_frame', 'activity_song_label',
                 'activity_separator', 'activity_waiting_label', 'vlc_status_label',
                 'song_var', 'artist_var', 'discord_status_label',
                 'song_count_label', 'browser_status_label'):
        setattr(app, name, Widget(app.root))
    app.video_enabled_var = Widget(app.root, False)
    app.api_key_var = Widget(app.root, '')
    app.log_stagekit_var = Widget(app.root, True)
    return app


class DesktopLifecycleTests(unittest.TestCase):
    def test_accepted_song_list_remains_visible_when_cache_save_fails(self):
        app = self.app
        app.is_running = True
        client = SongBrowser()
        client.songs_data = [{'shortname': 'new'}]
        client.artists_index = {'Unknown Artist': client.songs_data}
        with patch.object(client, 'save_to_cache', return_value=False):
            app._accept_song_list(app._generation, app._browser_generation, client, True)
        self.assertIs(app.song_browser, client)
        app.populate_song_tree.assert_called_once_with()
        self.assertEqual(app.song_count_label.options['text'], '1 songs, 1 artists')
        self.assertIn('cache failed', app.browser_status_label.options['text'].lower())
        self.assertNotIn('and cached', app.browser_status_label.options['text'])

    def setUp(self):
        self.app = make_app()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('rb3dashboard.desktop.app.StageKitTransport'))
        self.stack.enter_context(patch('rb3dashboard.desktop.app.UnifiedRB3EListener', Listener))
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.app.stop_listener()
        self.app.ui_dispatcher.close()

    def dispatch(self):
        self.app.root.pending.pop(self.app.ui_dispatcher._after_id, None)
        self.app.ui_dispatcher._drain()
        self.assertEqual(self.app.root.errors, [])

    def emit(self, listener, name, *args):
        errors = []
        def invoke():
            try:
                listener.callbacks[name](*args)
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=invoke)
        worker.start()
        worker.join(2)
        self.assertEqual(errors, [])

    def test_worker_callbacks_are_complete_owner_handlers_and_stale_session_is_ignored(self):
        app = self.app
        app.start_listener()
        old = app.listener
        self.emit(old, 'ip_detected_callback', '192.0.2.1')
        self.assertIsNone(app.detected_ip)
        self.emit(old, 'stagekit_callback', 1, 2)
        self.emit(old, 'song_update_callback', 'Song', 'Artist')
        self.emit(old, 'song_started_callback', 'Artist', 'Song', 'short')
        self.dispatch()
        self.assertEqual(app.song_var.get(), 'Song')
        self.assertEqual(app.artist_var.get(), 'Artist')
        self.assertIsNotNone(app.scrobble_timer_id)
        app.integration_worker.song_started.assert_called_once_with('Artist', 'Song')
        app.scrobbler.update_now_playing.assert_not_called()
        app.discord_presence.update_presence.assert_not_called()
        app.song_history.add_song.assert_called_once_with('Artist', 'Song', '', 'short')

        self.emit(old, 'song_started_callback', 'Artist', 'Stale', 'stale')
        app.stop_listener()
        app.start_listener()
        self.emit(old, 'song_update_callback', 'Obsolete', 'Artist')
        self.emit(old, 'ip_detected_callback', '192.0.2.99')
        self.dispatch()
        self.assertIsNone(app.detected_ip)
        self.assertEqual(app.song_history.add_song.call_count, 1)
        self.assertEqual(app.activity_song_label.options['text'], '')

    def test_stop_cancels_timers_and_already_dispatched_scrobble_cannot_run(self):
        app = self.app
        app.start_listener()
        app.on_song_started('Artist', 'Song', 'short')
        timer = app.scrobble_timer_id
        callback = app.root.pending[timer]
        cleanup = app.device_cleanup_timer_id
        app.stop_listener()
        self.assertNotIn(timer, app.root.pending)
        self.assertNotIn(cleanup, app.root.pending)
        callback()
        app.integration_worker.scrobble.assert_not_called()
        app.integration_worker.stop.assert_called()

    def test_menu_and_new_song_invalidate_old_scrobble_even_before_session_stops(self):
        app = self.app
        app.start_listener()
        app.on_song_started('Artist', 'Old', 'old')
        old_callback = app.root.pending[app.scrobble_timer_id]
        app.on_song_update('', '')
        app.on_song_started('Artist', 'New', 'new')
        old_callback()
        app.integration_worker.scrobble.assert_not_called()
        app.root.pending[app.scrobble_timer_id]()
        app.integration_worker.scrobble.assert_called_once_with('Artist', 'New')

    def test_stop_invalidates_already_queued_auto_start(self):
        app = self.app
        app.root.mainloop = lambda: None
        app.run()
        callback = app.root.pending[app.startup_timer_id]
        app.stop_listener()
        callback()
        self.assertFalse(app.is_running)

    def test_search_timer_remains_owned_by_app_when_browser_client_is_replaced(self):
        app = self.app
        app.search_var = Widget(app.root, 'test')
        app.on_search_changed()
        timer = app.search_timer_id
        app.song_browser = SongBrowser()
        app.stop_listener()
        self.assertNotIn(timer, app.root.pending)
        self.assertIsNone(app.search_timer_id)

    def test_partial_start_failure_stops_transport_and_restores_ui(self):
        app = self.app
        app.video_enabled_var.value = True
        with patch('rb3dashboard.desktop.app.VLCPlayer', side_effect=RuntimeError('setup')):
            with patch('rb3dashboard.desktop.app.messagebox.showerror'):
                app.start_listener()
        self.assertFalse(app.is_running)
        self.assertIsNone(app.stagekit_transport)
        self.assertIsNone(app.device_cleanup_timer_id)
        self.assertEqual(app.status_label.options['text'], 'Stopped')

    def test_close_cancels_timers_even_when_not_listening(self):
        app = self.app
        app.scrobble_timer_id = app.root.after(1000, lambda: None)
        app.search_timer_id = app.root.after(300, lambda: None)
        app.on_closing()
        self.assertEqual(app.root.pending, {})
        app.integration_worker.close.assert_called_once_with()

    def test_load_finishing_after_stop_close_or_console_change_cannot_publish(self):
        for transition in ('stop', 'close', 'console'):
            with self.subTest(transition=transition), TemporaryDirectory() as folder, ExitStack() as stack:
                app = make_app()
                original = app.song_browser
                app.is_running = True
                app.detected_ip = '192.0.2.1'
                entered, release = threading.Event(), threading.Event()
                threads = []
                real_thread = threading.Thread

                def create_thread(*args, **kwargs):
                    thread = real_thread(*args, **kwargs)
                    threads.append(thread)
                    return thread

                def get(*args, **kwargs):
                    entered.set()
                    if not release.wait(3):
                        raise AssertionError('Browser barrier not released')
                    return SimpleNamespace(text='[song]\ntitle=Obsolete\nartist=Artist',
                                           raise_for_status=lambda: None)

                cache = Path(folder) / 'songs.json'
                stack.enter_context(patch.object(SongBrowser, 'get_cache_path', return_value=str(cache)))
                stack.enter_context(patch('rb3dashboard.song_browser.requests.get', side_effect=get))
                stack.enter_context(patch('rb3dashboard.desktop.browser.threading.Thread', side_effect=create_thread))
                try:
                    app.load_song_list()
                    self.assertTrue(entered.wait(2))
                    if transition == 'close':
                        app.on_closing()
                    elif transition == 'stop':
                        app.stop_listener()
                    else:
                        app.on_ip_detected('192.0.2.2')
                    release.set()
                    threads[0].join(2)
                    app.root.pending.pop(app.ui_dispatcher._after_id, None)
                    app.ui_dispatcher._drain()
                    self.assertIs(app.song_browser, original)
                    self.assertFalse(cache.exists())
                    app.populate_song_tree.assert_not_called()
                finally:
                    release.set()
                    for thread in threads:
                        thread.join(2)
                    app.stop_listener()
                    app.ui_dispatcher.close()

    def test_artwork_callback_cannot_update_reused_row_after_rebuild_or_stop(self):
        app = self.app
        app.is_running = True
        app.settings.update(lastfm_api_key='fake', song_browser_expanded=True)
        app.song_browser.artists_index = {'Artist': [{'title': 'Song', 'album': 'Album'}]}
        callbacks, updated = [], []
        app._tree_image_refs = {}
        app.song_tree = SimpleNamespace(
            get_children=lambda: ['row'], delete=lambda item: None,
            insert=lambda *args, **kwargs: 'row', exists=lambda row: True,
            item=lambda *args, **kwargs: updated.append((args, kwargs)))
        app.album_art_manager = SimpleNamespace(
            api_key='fake', get_album_art=lambda *args, **kwargs: callbacks.append(kwargs['callback']))
        app.populate_song_tree = RB3Dashboard.populate_song_tree.__get__(app)
        app.populate_song_tree()
        first = callbacks[0]
        app.populate_song_tree()
        first('key', object())
        self.assertEqual(updated, [])
        current = callbacks[-1]
        app.ui_dispatcher.post(current, 'key', object())
        app.stop_listener()
        self.dispatch()
        self.assertEqual(updated, [])

    def test_receiver_startup_failure_stops_already_started_services_on_dispatch(self):
        from rb3dashboard.listener import UnifiedRB3EListener
        app = self.app
        sock = Mock()
        sock.bind.side_effect = OSError('Bind failed')
        with patch('rb3dashboard.desktop.app.UnifiedRB3EListener', UnifiedRB3EListener):
            with patch('rb3dashboard.listener.socket.socket', return_value=sock):
                app.start_listener()
                transport = app.stagekit_transport
                app.listener_thread.join(2)
                self.dispatch()
        self.assertFalse(app.is_running)
        transport.stop.assert_called_once_with()
        self.assertTrue(sock.close.called)
        self.assertIsNone(app.device_cleanup_timer_id)

    def test_saved_game_handler_does_not_schedule_an_unguarded_second_ui_hop(self):
        app = self.app
        app.start_listener()
        received = []
        app._update_game_info = lambda *args: received.append(args)
        self.emit(app.listener, 'game_info_callback', 'venue', 'test_venue')
        self.dispatch()
        self.assertEqual(received, [('venue', 'test_venue')])
        app.stop_listener()
        # Only the dispatcher poll remains; no zero-delay game/blank/activity hops.
        self.assertEqual(set(app.root.pending), {app.ui_dispatcher._after_id})

    def test_reverse_browser_loads_publish_and_cache_only_latest_accepted_result(self):
        app = self.app
        app.is_running = True
        app.detected_ip = '192.0.2.1'
        entered = [threading.Event(), threading.Event()]
        release = [threading.Event(), threading.Event()]
        threads = []
        real_thread = threading.Thread

        def create_thread(*args, **kwargs):
            thread = real_thread(*args, **kwargs)
            threads.append(thread)
            return thread

        def get(url, **kwargs):
            index = 0 if '192.0.2.1:' in url else 1
            entered[index].set()
            if not release[index].wait(3):
                raise AssertionError('Browser barrier not released')
            return SimpleNamespace(text='[song]\ntitle=Song %s\nartist=Artist' % index,
                                   raise_for_status=lambda: None)

        with TemporaryDirectory() as folder, ExitStack() as stack:
            cache = Path(folder) / 'songs.json'
            stack.enter_context(patch.object(SongBrowser, 'get_cache_path', return_value=str(cache)))
            stack.enter_context(patch('rb3dashboard.song_browser.requests.get', side_effect=get))
            stack.enter_context(patch('rb3dashboard.desktop.browser.threading.Thread', side_effect=create_thread))
            try:
                app.load_song_list()
                self.assertTrue(entered[0].wait(2))
                app.detected_ip = '192.0.2.2'
                app.load_song_list()
                self.assertTrue(entered[1].wait(2))
                release[1].set()
                threads[1].join(2)
                self.assertFalse(cache.exists(), 'Worker must not publish cache')
                self.dispatch()
                release[0].set()
                threads[0].join(2)
                self.dispatch()
                self.assertEqual(app.song_browser.rb3_ip, '192.0.2.2')
                self.assertEqual(app.song_browser.songs_data[0]['title'], 'Song 1')
                self.assertEqual(json.loads(cache.read_text())['source_ip'], '192.0.2.2')
            finally:
                for event in release:
                    event.set()
                for thread in threads:
                    thread.join(2)
