"""One desktop-owned thread for Last.fm and Discord side effects; no Tk calls."""

from collections import deque
import threading


class DesktopIntegrationWorker:
    """Serialize service ownership. Stop/close discard work without waiting on I/O."""

    def __init__(self, scrobbler, discord, log_callback=None):
        self._scrobbler = scrobbler
        self._discord = discord
        self._log = log_callback
        self._condition = threading.Condition()
        self._pending = deque()
        self._generation = 0
        self._active = False
        self._closed = False
        self._thread = threading.Thread(target=self._run,
                                        name='RB3E-desktop-integrations', daemon=True)
        self._thread.start()

    def _invalidate_locked(self):
        self._generation += 1
        # Cleanup from an earlier stop must precede a later start/configuration.
        self._pending = deque(job for job in self._pending if job[0] is None)

    def configure(self, settings, status_callback=None):
        """Copy settings on the caller; change services only on their owner."""
        with self._condition:
            if self._closed:
                return
            # Settings are ordered with songs; only stop/close cancel the session.
            self._active = True
            token = self._generation
            self._pending.append((token, self._configure,
                                  (token, settings.copy(), status_callback)))
            self._condition.notify()

    def _current(self, token):
        with self._condition:
            return not self._closed and self._active and token == self._generation

    def _configure(self, token, settings, status_callback):
        scrobbler, discord = self._scrobbler, self._discord
        scrobbler.api_key = settings.get('lastfm_api_key', '')
        scrobbler.api_secret = settings.get('lastfm_api_secret', '')
        scrobbler.session_key = settings.get('lastfm_session_key', '')
        scrobbler.enabled = settings.get('scrobble_enabled', False)
        enabled = settings.get('discord_enabled', False)
        client_id = settings.get('discord_client_id') or discord.DEFAULT_CLIENT_ID
        if not enabled or discord.client_id != client_id:
            discord.disconnect()
        if not enabled:
            discord.clear_presence()
        discord.client_id = client_id
        discord.enabled = enabled
        status, color = 'Disabled', 'gray'
        if enabled and self._current(token):
            already_connected = discord.connected
            connected = already_connected or discord.connect()
            status, color = ('Connected', 'green') if connected else ('Connection failed', 'red')
            if connected and not already_connected and self._current(token):
                if discord.current_song or discord.current_artist:
                    discord.update_presence(discord.current_artist, discord.current_song)
                else:
                    discord.set_idle()
        if status_callback and self._current(token):
            status_callback(status, color)

    def _submit(self, callback, *args):
        with self._condition:
            if self._closed or not self._active:
                return False
            self._pending.append((self._generation, callback, args))
            self._condition.notify()
            return True

    def song_started(self, artist, song):
        self._submit(self._scrobbler.update_now_playing, artist, song)
        self._submit(self._discord.update_presence, artist, song)

    def idle(self):
        self._submit(self._discord.set_idle)

    def scrobble(self, artist, song):
        self._submit(self._scrobbler.scrobble, artist, song)

    def stop(self):
        with self._condition:
            if self._closed:
                return
            self._invalidate_locked()
            self._active = False
            self._pending.append((None, self._disconnect, ()))
            self._condition.notify()

    def _disconnect(self):
        self._discord.disconnect()
        self._discord.clear_presence()

    def close(self):
        """Return immediately; final cleanup follows any in-flight service call."""
        with self._condition:
            self._closed = True
            self._active = False
            self._pending.clear()
            self._condition.notify()

    def _call(self, callback, args):
        try:
            callback(*args)
        except Exception as exc:
            if self._log:
                try:
                    self._log(f'Desktop integration error: {exc}')
                except Exception:
                    pass

    def _run(self):
        try:
            while True:
                with self._condition:
                    while not self._closed and not self._pending:
                        self._condition.wait()
                    if self._closed:
                        return
                    token, callback, args = self._pending.popleft()
                if token is None or self._current(token):
                    self._call(callback, args)
        finally:
            self._call(self._discord.disconnect, ())
