"""The desktop integration owner stays responsive and cancels queued work."""

import threading
import unittest
from unittest.mock import Mock

from rb3dashboard.desktop.integrations import DesktopIntegrationWorker


class DesktopIntegrationWorkerTests(unittest.TestCase):
    def test_settings_save_preserves_queued_song_and_configuration_order(self):
        scrobbler, discord = Mock(), Mock()
        discord.connected = True
        discord.client_id = 'same'
        entered, release, configured, completed = (threading.Event() for _ in range(4))
        order = []

        def now_playing(artist, song):
            order.append(('song', song, scrobbler.api_key))
            if song == 'First':
                entered.set()
                release.wait(3)

        scrobbler.update_now_playing.side_effect = now_playing
        worker = DesktopIntegrationWorker(scrobbler, discord)
        settings = {'discord_enabled': True, 'discord_client_id': 'same',
                    'scrobble_enabled': True, 'lastfm_api_key': 'before'}
        try:
            worker.configure(settings, lambda *args: configured.set())
            self.assertTrue(configured.wait(2))
            worker.song_started('Artist', 'First')
            self.assertTrue(entered.wait(2))
            worker.song_started('Artist', 'Queued')
            settings['lastfm_api_key'] = 'after'
            worker.configure(settings, lambda *args: completed.set())
            release.set()
            self.assertTrue(completed.wait(2))
            self.assertEqual(order, [('song', 'First', 'before'), ('song', 'Queued', 'before')])
            self.assertEqual(scrobbler.api_key, 'after')
        finally:
            release.set()
            worker.close()
            worker._thread.join(2)

    def test_close_during_connect_skips_idle_and_closes_on_connect_owner(self):
        entered, release = threading.Event(), threading.Event()
        scrobbler, discord = Mock(), Mock()
        discord.connected = False
        discord.client_id = 'same'
        discord.current_song = discord.current_artist = None
        owners = []

        def connect():
            owners.append(threading.get_ident())
            entered.set()
            release.wait(3)
            return True

        discord.connect.side_effect = connect
        discord.disconnect.side_effect = lambda: owners.append(threading.get_ident())
        status = Mock()
        worker = DesktopIntegrationWorker(scrobbler, discord)
        try:
            worker.configure({'discord_enabled': True, 'discord_client_id': 'same'}, status)
            self.assertTrue(entered.wait(2))
            worker.close()
        finally:
            release.set()
            worker.close()
            worker._thread.join(2)
        self.assertEqual(len(owners), 2)
        self.assertEqual(owners[0], owners[1])
        self.assertNotEqual(owners[0], threading.get_ident())
        discord.set_idle.assert_not_called()
        status.assert_not_called()

    def test_client_id_change_restores_song_and_stop_restart_returns_to_idle(self):
        from rb3dashboard.integrations import DiscordPresence, LastFmScrobbler
        from unittest.mock import patch
        discord = DiscordPresence('one')
        discord.enabled = True
        discord.current_artist, discord.current_song = 'Artist', 'Song'
        rpc = Mock()
        worker = DesktopIntegrationWorker(LastFmScrobbler(), discord)
        with patch('rb3dashboard.integrations.Presence', return_value=rpc, create=True):
            with patch('rb3dashboard.integrations.DISCORD_AVAILABLE', True):
                try:
                    first, second = threading.Event(), threading.Event()
                    worker.configure({'discord_enabled': True, 'discord_client_id': 'two'},
                                     lambda *args: first.set())
                    self.assertTrue(first.wait(2))
                    self.assertEqual(rpc.update.call_args.kwargs['details'], 'Song')
                    worker.stop()
                    worker.configure({'discord_enabled': True, 'discord_client_id': 'two'},
                                     lambda *args: second.set())
                    self.assertTrue(second.wait(2))
                    self.assertEqual(rpc.update.call_args.kwargs['details'], 'Browsing Songs')
                finally:
                    worker.close()
                    worker._thread.join(2)

    def test_disable_then_enable_does_not_restore_previously_played_song(self):
        from rb3dashboard.integrations import DiscordPresence, LastFmScrobbler
        from unittest.mock import patch
        discord = DiscordPresence('same')
        discord.current_artist, discord.current_song = 'Artist', 'Old Song'
        rpc = Mock()
        worker = DesktopIntegrationWorker(LastFmScrobbler(), discord)
        with patch('rb3dashboard.integrations.Presence', return_value=rpc, create=True):
            with patch('rb3dashboard.integrations.DISCORD_AVAILABLE', True):
                try:
                    disabled, enabled = threading.Event(), threading.Event()
                    worker.configure({'discord_enabled': False, 'discord_client_id': 'same'},
                                     lambda *args: disabled.set())
                    self.assertTrue(disabled.wait(2))
                    worker.configure({'discord_enabled': True, 'discord_client_id': 'same'},
                                     lambda *args: enabled.set())
                    self.assertTrue(enabled.wait(2))
                    self.assertEqual(rpc.update.call_args.kwargs['details'], 'Browsing Songs')
                finally:
                    worker.close()
                    worker._thread.join(2)

    def test_unrelated_settings_save_does_not_replace_playing_presence_with_idle(self):
        scrobbler, discord = Mock(), Mock()
        discord.client_id = 'same'
        discord.enabled = True
        discord.connected = True
        done = threading.Event()
        worker = DesktopIntegrationWorker(scrobbler, discord)
        try:
            worker.configure({'discord_enabled': True, 'discord_client_id': 'same'},
                             lambda *args: done.set())
            self.assertTrue(done.wait(2))
            discord.set_idle.assert_not_called()
            discord.connect.assert_not_called()
        finally:
            worker.close()
            worker._thread.join(2)

    def test_blocked_service_does_not_block_submit_stop_or_owner_cleanup(self):
        entered, release, configured = (threading.Event() for _ in range(3))
        calls = []
        owner = threading.get_ident()
        scrobbler, discord = Mock(), Mock()
        discord.connected = False
        discord.current_song = discord.current_artist = None
        discord.enabled = False
        discord.client_id = 'old'

        def record(name):
            calls.append((name, threading.get_ident()))

        def blocked(*args):
            record('now playing')
            entered.set()
            if not release.wait(3):
                raise AssertionError('Service barrier not released')

        scrobbler.update_now_playing.side_effect = blocked
        discord.connect.side_effect = lambda: record('connect') or True
        discord.set_idle.side_effect = lambda: record('idle')
        discord.update_presence.side_effect = lambda *args: record('song')
        discord.disconnect.side_effect = lambda: record('disconnect')
        worker = DesktopIntegrationWorker(scrobbler, discord)
        try:
            worker.configure({'scrobble_enabled': True, 'discord_enabled': True,
                              'discord_client_id': 'new'}, lambda *args: configured.set())
            self.assertTrue(configured.wait(2))
            worker.song_started('Artist', 'First')
            self.assertTrue(entered.wait(2))
            worker.song_started('Artist', 'Obsolete')
            worker.scrobble('Artist', 'Obsolete')
            worker.stop()
            worker.close()
            self.assertTrue(worker._thread.is_alive())
        finally:
            release.set()
            worker.close()
            worker._thread.join(3)
        self.assertFalse(worker._thread.is_alive())
        self.assertEqual([name for name, _ in calls],
                         ['disconnect', 'connect', 'idle', 'now playing', 'disconnect'])
        service_owners = {ident for _, ident in calls}
        self.assertEqual(len(service_owners), 1)
        self.assertNotIn(owner, service_owners)
        scrobbler.scrobble.assert_not_called()
        self.assertEqual(scrobbler.update_now_playing.call_count, 1)

    def test_error_does_not_strand_later_jobs_and_settings_change_reconnects(self):
        scrobbler, discord = Mock(), Mock()
        discord.client_id = 'one'
        discord.enabled = True
        discord.connected = True
        discord.disconnect.side_effect = lambda: setattr(discord, 'connected', False)
        discord.current_song = discord.current_artist = None
        errors, statuses = [], []
        done = threading.Event()
        scrobbler.update_now_playing.side_effect = RuntimeError('offline')
        discord.update_presence.side_effect = lambda *args: done.set()
        def failing_logger(message):
            errors.append(message)
            raise RuntimeError('Log sink failed too')
        worker = DesktopIntegrationWorker(scrobbler, discord, failing_logger)
        try:
            configured = threading.Event()
            worker.configure({'discord_enabled': True, 'discord_client_id': 'two',
                              'scrobble_enabled': True},
                             lambda *args: (statuses.append(args), configured.set()))
            self.assertTrue(configured.wait(2))
            worker.song_started('Artist', 'Song')
            self.assertTrue(done.wait(2))
        finally:
            worker.close()
            worker._thread.join(2)
        self.assertTrue(any('offline' in error for error in errors))
        self.assertEqual(discord.client_id, 'two')
        discord.connect.assert_called_once_with()
        self.assertEqual(discord.disconnect.call_count, 2)
