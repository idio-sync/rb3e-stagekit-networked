"""Characterize service boundaries without contacting Last.fm or Discord."""

from contextlib import ExitStack
import hashlib
import unittest
from unittest.mock import Mock, patch

from rb3dashboard import integrations


class LastFmScrobblerTests(unittest.TestCase):
    def setUp(self):
        patches = ExitStack()
        self.addCleanup(patches.close)
        self.messages = []
        self.scrobbler = integrations.LastFmScrobbler(
            'key', 'secret', 'session', self.messages.append)
        self.scrobbler.enabled = True
        self.post = patches.enter_context(patch.object(integrations.requests, 'post'))
        self.get = patches.enter_context(patch.object(integrations.requests, 'get'))
        self.post.return_value.json.return_value = {}

    def test_disabled_or_unconfigured_scrobbler_sends_nothing(self):
        for missing in ('enabled', 'api_key', 'api_secret', 'session_key'):
            with self.subTest(missing=missing), patch.object(self.scrobbler, missing, ''):
                self.scrobbler.update_now_playing('Artist', 'Song')
                self.scrobbler.scrobble('Artist', 'Song')
                self.assertIsNone(self.scrobbler.current_song)
        self.post.assert_not_called()
        self.get.assert_not_called()

    def test_now_playing_sends_signed_metadata_and_records_start(self):
        with patch.object(integrations.time, 'time', return_value=1000.25):
            self.scrobbler.update_now_playing('Artist', 'Song', 'Album')
        # Literal preimage: sorted fields, secret last, format excluded.
        signature = hashlib.md5(
            b'albumAlbumapi_keykeyartistArtistmethodtrack.updateNowPlayingsksessiontrackSongsecret'
        ).hexdigest()
        self.post.assert_called_once_with(
            'https://ws.audioscrobbler.com/2.0/', timeout=10,
            data={'artist': 'Artist', 'track': 'Song', 'album': 'Album',
                  'method': 'track.updateNowPlaying', 'api_key': 'key',
                  'sk': 'session', 'api_sig': signature, 'format': 'json'})
        self.assertEqual(self.scrobbler.current_song,
                         {'artist': 'Artist', 'track': 'Song', 'album': 'Album'})
        self.assertEqual(self.scrobbler.current_song_start, 1000.25)

    def test_scrobble_sends_optional_metadata_only_when_supplied(self):
        self.scrobbler.scrobble('Artist', 'Song', 'Album', 180)
        payload = self.post.call_args.kwargs['data']
        self.assertEqual(payload['method'], 'track.scrobble')
        self.assertEqual(payload['artist'], 'Artist')
        self.assertEqual(payload['track'], 'Song')
        self.assertEqual(payload['album'], 'Album')
        self.assertEqual(payload['duration'], '180')
        self.assertGreater(int(payload['timestamp']), 0)
        self.scrobbler.scrobble('Artist', 'Song')
        payload = self.post.call_args.kwargs['data']
        self.assertNotIn('album', payload)
        self.assertNotIn('duration', payload)

    def test_authentication_returns_token_and_signed_session_key(self):
        self.get.return_value.json.return_value = {'token': 'auth-token'}
        self.assertEqual(self.scrobbler.get_auth_token(), 'auth-token')
        self.get.assert_called_once_with(
            'https://ws.audioscrobbler.com/2.0/', timeout=10,
            params={'method': 'auth.gettoken', 'api_key': 'key', 'format': 'json'})
        self.assertEqual(self.scrobbler.get_auth_url('auth-token'),
                         'https://www.last.fm/api/auth/?api_key=key&token=auth-token')
        self.get.return_value.json.return_value = {
            'session': {'name': 'test-user', 'key': 'new-session', 'subscriber': 0}}
        self.assertEqual(self.scrobbler.get_session_key('auth-token'), 'new-session')
        signature = hashlib.md5(
            b'api_keykeymethodauth.getSessiontokenauth-tokensecret').hexdigest()
        self.get.assert_called_with(
            'https://ws.audioscrobbler.com/2.0/', timeout=10,
            params={'method': 'auth.getSession', 'api_key': 'key',
                    'token': 'auth-token', 'api_sig': signature, 'format': 'json'})

    def test_service_errors_are_reported_without_raising(self):
        for operation in (self.scrobbler.update_now_playing, self.scrobbler.scrobble):
            with self.subTest(operation=operation.__name__):
                self.messages.clear()
                self.post.return_value.json.return_value = {
                    'error': 9, 'message': 'Invalid session key'}
                operation('Artist', 'Song')
                self.assertTrue(any('Invalid session key' in message for message in self.messages))
        self.post.side_effect = integrations.requests.exceptions.Timeout('timed out')
        self.assertEqual(self.scrobbler._api_call('track.scrobble', {}), {})
        self.assertTrue(any('timed out' in message for message in self.messages))
        self.get.side_effect = integrations.requests.exceptions.Timeout('timed out')
        self.assertIsNone(self.scrobbler.get_auth_token())
        self.assertIsNone(self.scrobbler.get_session_key('auth-token'))

    def test_scrobble_threshold_uses_half_duration_or_four_minutes(self):
        for duration, elapsed, expected in (
                (180, 89, False), (180, 90, True),
                (600, 239, False), (600, 240, True),
                (0, 239, False), (0, 240, True)):
            with self.subTest(duration=duration, elapsed=elapsed):
                self.assertEqual(self.scrobbler.should_scrobble(duration, elapsed), expected)


class DiscordPresenceTests(unittest.TestCase):
    def setUp(self):
        patches = ExitStack()
        self.addCleanup(patches.close)
        self.messages = []
        self.presence = integrations.DiscordPresence(gui_callback=self.messages.append)
        self.presence.enabled = True
        patches.enter_context(patch.object(integrations, 'DISCORD_AVAILABLE', True))
        self.factory = patches.enter_context(patch.object(integrations, 'Presence', create=True))
        self.rpc = self.factory.return_value
        self.clock = patches.enter_context(patch.object(integrations.time, 'time', return_value=1000.25))

    def test_connect_uses_default_or_custom_id_and_disconnect_closes_rpc(self):
        for client_id in (None, 'custom-app'):
            with self.subTest(client_id=client_id):
                presence = integrations.DiscordPresence(client_id)
                self.assertTrue(presence.connect())
                self.factory.assert_called_with(client_id or presence.DEFAULT_CLIENT_ID)
                self.assertTrue(presence.connected)
                presence.disconnect()
                self.assertFalse(presence.connected)
                self.assertIsNone(presence.rpc)
        self.assertEqual(self.rpc.clear.call_count, 2)
        self.assertEqual(self.rpc.close.call_count, 2)

    def test_failed_connection_leaves_disconnected_state_and_reports_error(self):
        self.rpc.connect.side_effect = OSError('Discord unavailable')
        self.assertFalse(self.presence.connect())
        self.assertFalse(self.presence.connected)
        self.assertIsNone(self.presence.rpc)
        self.assertTrue(any('Discord unavailable' in message for message in self.messages))

    def test_disabled_presence_does_not_connect_or_update(self):
        self.presence.enabled = False
        self.presence.update_presence('Artist', 'Song')
        self.presence.set_idle()
        self.factory.assert_not_called()
        self.assertIsNone(self.presence.current_song)

    def test_song_payload_truncates_text_and_clear_forgets_song(self):
        self.presence.connect()
        self.presence.update_presence('A' * 150, 'S' * 150)
        self.rpc.update.assert_called_once_with(
            details='S' * 128, state='by ' + 'A' * 125,
            large_image='guitar', large_text='Rock Band 3 Deluxe', start=1000)
        self.presence.clear_presence()
        self.rpc.clear.assert_called_once_with()
        self.assertIsNone(self.presence.current_artist)
        self.assertIsNone(self.presence.current_song)
        self.assertIsNone(self.presence.start_time)

    def test_empty_song_metadata_uses_display_fallbacks(self):
        self.presence.connect()
        self.presence.update_presence('', '', state='Practicing')
        self.rpc.update.assert_called_once_with(
            details='Unknown Song', state='Practicing', large_image='guitar',
            large_text='Rock Band 3 Deluxe', start=1000)

    def test_reconnect_restores_song_and_original_start_time(self):
        self.presence.connect()
        self.presence.update_presence('Artist', 'Song')
        self.presence.connected = False
        replacement = Mock()
        self.factory.return_value = replacement
        self.clock.return_value = 1030
        self.assertTrue(self.presence._try_reconnect())
        self.rpc.close.assert_called_once_with()
        replacement.connect.assert_called_once_with()
        replacement.update.assert_called_once_with(
            details='Song', state='by Artist', large_image='guitar',
            large_text='Rock Band 3 Deluxe', start=1000)
        self.assertTrue(self.presence.connected)
        self.assertEqual(self.presence.reconnect_attempts, 0)

    def test_failed_reconnects_observe_backoff_and_reset_after_cooldown(self):
        self.factory.side_effect = OSError('Discord unavailable')
        for attempt, now in enumerate((1000, 1010, 1040, 1100, 1220), 1):
            self.clock.return_value = now - 1
            if attempt > 1:
                self.assertFalse(self.presence._try_reconnect())
                self.assertEqual(self.factory.call_count, attempt - 1)
            self.clock.return_value = now
            self.assertFalse(self.presence._try_reconnect())
            self.assertEqual(self.factory.call_count, attempt)
            self.assertFalse(self.presence.reconnecting)
        self.clock.return_value = 1520
        self.assertFalse(self.presence._try_reconnect())
        self.assertEqual(self.factory.call_count, 5)
        self.clock.return_value = 1521
        self.assertFalse(self.presence._try_reconnect())
        self.assertEqual(self.factory.call_count, 6)
        self.assertEqual(self.presence.reconnect_attempts, 1)

    def test_idle_payload_displays_menus_without_song_timer(self):
        self.presence.connect()
        self.presence.update_presence('Artist', 'Song')
        self.presence.set_idle()
        self.rpc.update.assert_called_with(
            details='Browsing Songs', state='In Menus', large_image='guitar',
            large_text='Rock Band 3 Deluxe')

    def test_failed_menu_update_reconnects_with_idle_instead_of_previous_song(self):
        self.presence.connect()
        self.presence.update_presence('Artist', 'Song')
        self.rpc.update.side_effect = OSError('Discord connection lost')
        replacement = Mock()
        self.factory.return_value = replacement

        self.presence.set_idle()

        replacement.update.assert_called_once_with(
            details='Browsing Songs', state='In Menus', large_image='guitar',
            large_text='Rock Band 3 Deluxe')
        self.assertIsNone(self.presence.current_artist)
        self.assertIsNone(self.presence.current_song)
        self.assertIsNone(self.presence.start_time)

    def test_menu_during_retry_delay_is_restored_after_connection_returns(self):
        self.presence.connect()
        self.presence.update_presence('Artist', 'Song')
        self.presence.connected = False
        self.presence.last_reconnect_time = 1000
        self.clock.return_value = 1001
        replacement = Mock()
        self.factory.return_value = replacement

        self.presence.set_idle()
        replacement.connect.assert_not_called()
        self.clock.return_value = 1005
        self.assertTrue(self.presence._try_reconnect())

        replacement.update.assert_called_once_with(
            details='Browsing Songs', state='In Menus', large_image='guitar',
            large_text='Rock Band 3 Deluxe')
        self.assertIsNone(self.presence.current_artist)
        self.assertIsNone(self.presence.current_song)
        self.assertIsNone(self.presence.start_time)

    def test_menu_while_disconnected_never_briefly_restores_previous_song(self):
        self.presence.connect()
        self.presence.update_presence('Artist', 'Song')
        self.presence.connected = False
        replacement = Mock()
        self.factory.return_value = replacement

        self.presence.set_idle()

        self.assertTrue(replacement.update.called)
        for update in replacement.update.call_args_list:
            self.assertEqual(update.kwargs, {
                'details': 'Browsing Songs', 'state': 'In Menus',
                'large_image': 'guitar', 'large_text': 'Rock Band 3 Deluxe'})

    def test_reconnect_after_successful_menu_update_restores_idle(self):
        self.presence.connect()
        self.presence.update_presence('Artist', 'Song')
        self.presence.set_idle()
        self.presence.connected = False
        replacement = Mock()
        self.factory.return_value = replacement

        self.assertTrue(self.presence._try_reconnect())

        replacement.update.assert_called_once_with(
            details='Browsing Songs', state='In Menus', large_image='guitar',
            large_text='Rock Band 3 Deluxe')

    def test_new_song_replaces_idle_state_on_reconnect(self):
        self.presence.connect()
        self.presence.update_presence('Artist', 'Old Song')
        self.presence.set_idle()
        self.clock.return_value = 1100.5
        self.presence.update_presence('New Artist', 'New Song')
        self.presence.connected = False
        replacement = Mock()
        self.factory.return_value = replacement
        self.clock.return_value = 1130

        self.assertTrue(self.presence._try_reconnect())

        replacement.update.assert_called_once_with(
            details='New Song', state='by New Artist', large_image='guitar',
            large_text='Rock Band 3 Deluxe', start=1100)

    def test_failed_idle_restore_retains_menu_state_for_next_reconnect(self):
        self.presence.connect()
        self.presence.update_presence('Artist', 'Song')
        self.presence.set_idle()
        self.presence.connected = False
        replacement = Mock()
        replacement.update.side_effect = OSError('Discord connection lost again')
        self.factory.return_value = replacement

        self.assertFalse(self.presence._try_reconnect())
        self.assertFalse(self.presence.connected)
        replacement.close.assert_called_once_with()
        self.assertEqual(self.presence.reconnect_attempts, 1)

        recovered = Mock()
        self.factory.return_value = recovered
        self.clock.return_value = 1030
        self.assertTrue(self.presence._try_reconnect())
        recovered.update.assert_called_once_with(
            details='Browsing Songs', state='In Menus', large_image='guitar',
            large_text='Rock Band 3 Deluxe')

    def test_cleared_idle_presence_stays_empty_on_reconnect(self):
        self.presence.connect()
        self.presence.set_idle()
        self.presence.connected = False
        self.presence.clear_presence()
        replacement = Mock()
        self.factory.return_value = replacement

        self.assertTrue(self.presence._try_reconnect())

        replacement.update.assert_not_called()


if __name__ == '__main__':
    unittest.main()
