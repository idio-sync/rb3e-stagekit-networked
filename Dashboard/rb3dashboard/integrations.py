"""Last.fm scrobbling and Discord presence, independent of the desktop UI."""

import hashlib
import time
from typing import Optional

import requests

try:
    from pypresence import Presence
    DISCORD_AVAILABLE = True
except ImportError:
    DISCORD_AVAILABLE = False


# =============================================================================
# LAST.FM SCROBBLER
# =============================================================================

class LastFmScrobbler:
    """Last.fm scrobbling integration"""

    API_URL = "https://ws.audioscrobbler.com/2.0/"

    def __init__(self, api_key: str = "", api_secret: str = "", session_key: str = "",
                 gui_callback=None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.session_key = session_key
        self.gui_callback = gui_callback
        self.enabled = False
        self.current_song_start = None
        self.current_song = None

    def is_configured(self) -> bool:
        """Check if Last.fm is properly configured"""
        return bool(self.api_key and self.api_secret and self.session_key)

    def _sign_call(self, params: dict) -> str:
        """Create API signature"""
        sorted_params = sorted(params.items())
        signature_string = ''.join(f"{k}{v}" for k, v in sorted_params)
        signature_string += self.api_secret
        return hashlib.md5(signature_string.encode('utf-8')).hexdigest()

    def _api_call(self, method: str, params: dict, http_method: str = 'POST') -> dict:
        """Make an API call to Last.fm"""
        params['method'] = method
        params['api_key'] = self.api_key
        params['sk'] = self.session_key
        params['api_sig'] = self._sign_call(params)
        params['format'] = 'json'

        try:
            if http_method == 'POST':
                response = requests.post(self.API_URL, data=params, timeout=10)
            else:
                response = requests.get(self.API_URL, params=params, timeout=10)
            return response.json()
        except Exception as e:
            if self.gui_callback:
                self.gui_callback(f"Last.fm API error: {e}")
            return {}

    def update_now_playing(self, artist: str, track: str, album: str = ""):
        """Update Now Playing status on Last.fm"""
        if not self.enabled or not self.is_configured():
            return

        self.current_song = {'artist': artist, 'track': track, 'album': album}
        self.current_song_start = time.time()

        params = {
            'artist': artist,
            'track': track
        }
        if album:
            params['album'] = album

        result = self._api_call('track.updateNowPlaying', params)

        if 'error' in result:
            if self.gui_callback:
                self.gui_callback(f"Last.fm now playing error: {result.get('message', 'Unknown error')}")
        elif self.gui_callback:
            self.gui_callback(f"Last.fm: Now playing - {artist} - {track}")

    def scrobble(self, artist: str, track: str, album: str = "", duration: int = 0):
        """Scrobble a track to Last.fm

        Should only be called after playing 50% of the track or 4 minutes
        """
        if not self.enabled or not self.is_configured():
            return

        params = {
            'artist': artist,
            'track': track,
            'timestamp': str(int(time.time()))
        }
        if album:
            params['album'] = album
        if duration:
            params['duration'] = str(duration)

        result = self._api_call('track.scrobble', params)

        if 'error' in result:
            if self.gui_callback:
                self.gui_callback(f"Last.fm scrobble error: {result.get('message', 'Unknown error')}")
        elif self.gui_callback:
            self.gui_callback(f"Last.fm: Scrobbled - {artist} - {track}")

    def should_scrobble(self, duration_seconds: int, elapsed_seconds: int) -> bool:
        """Check if track should be scrobbled based on Last.fm rules

        Rules: Track must be played for at least 50% or 4 minutes
        """
        if elapsed_seconds >= 240:  # 4 minutes
            return True
        if duration_seconds > 0 and elapsed_seconds >= duration_seconds * 0.5:
            return True
        return False

    def get_auth_token(self) -> Optional[str]:
        """Get authentication token for user authorization"""
        if not self.api_key:
            return None

        params = {
            'method': 'auth.gettoken',
            'api_key': self.api_key,
            'format': 'json'
        }

        try:
            response = requests.get(self.API_URL, params=params, timeout=10)
            result = response.json()
            return result.get('token')
        except Exception:
            return None

    def get_auth_url(self, token: str) -> str:
        """Get URL for user to authorize the application"""
        return f"https://www.last.fm/api/auth/?api_key={self.api_key}&token={token}"

    def get_session_key(self, token: str) -> Optional[str]:
        """Exchange authorized token for session key"""
        params = {
            'method': 'auth.getSession',
            'api_key': self.api_key,
            'token': token
        }
        params['api_sig'] = self._sign_call(params)
        params['format'] = 'json'

        try:
            response = requests.get(self.API_URL, params=params, timeout=10)
            result = response.json()
            if 'session' in result:
                return result['session']['key']
            return None
        except Exception:
            return None


# =============================================================================
# DISCORD RICH PRESENCE
# =============================================================================

class DiscordPresence:
    """Discord Rich Presence integration with auto-reconnect"""

    # Default Discord Application ID - uses RB3 Deluxe app which has proper assets
    # Users can create their own at discord.com/developers/applications
    DEFAULT_CLIENT_ID = "1125571051607298190"  # RB3 Deluxe Discord Application

    # Reconnection settings
    MAX_RECONNECT_ATTEMPTS = 5
    RECONNECT_DELAYS = [5, 10, 30, 60, 120]  # Backoff delays in seconds

    def __init__(self, client_id: str = None, gui_callback=None):
        self.client_id = client_id or self.DEFAULT_CLIENT_ID
        self.gui_callback = gui_callback
        self.enabled = False
        self.connected = False
        self.rpc = None
        self.current_song = None
        self.current_artist = None
        self.start_time = None
        # Reconnection state
        self.reconnect_attempts = 0
        self.last_reconnect_time = 0
        self.reconnecting = False

    def connect(self) -> bool:
        """Connect to Discord"""
        if not DISCORD_AVAILABLE:
            if self.gui_callback:
                self.gui_callback("Discord Rich Presence not available (pypresence not installed)")
            return False

        if not self.client_id:
            if self.gui_callback:
                self.gui_callback("Discord: No Client ID configured")
            return False

        try:
            self.rpc = Presence(self.client_id)
            self.rpc.connect()
            self.connected = True
            self.reconnect_attempts = 0  # Reset on successful connection
            if self.gui_callback:
                self.gui_callback("Discord Rich Presence connected")
            return True
        except Exception as e:
            self.connected = False
            self.rpc = None
            if self.gui_callback:
                self.gui_callback(f"Discord connection failed: {e}")
            return False

    def _try_reconnect(self) -> bool:
        """Attempt to reconnect to Discord with backoff"""
        if self.reconnecting:
            return False

        if self.reconnect_attempts >= self.MAX_RECONNECT_ATTEMPTS:
            # Check if enough time has passed to reset attempts
            if time.time() - self.last_reconnect_time > 300:  # 5 minutes
                self.reconnect_attempts = 0
            else:
                return False

        # Check backoff delay
        delay_index = min(self.reconnect_attempts, len(self.RECONNECT_DELAYS) - 1)
        required_delay = self.RECONNECT_DELAYS[delay_index]
        if time.time() - self.last_reconnect_time < required_delay:
            return False

        self.reconnecting = True
        self.last_reconnect_time = time.time()
        self.reconnect_attempts += 1

        if self.gui_callback:
            self.gui_callback(f"Discord: Attempting reconnect ({self.reconnect_attempts}/{self.MAX_RECONNECT_ATTEMPTS})...")

        # Clean up old connection
        if self.rpc:
            try:
                self.rpc.close()
            except Exception:
                pass
            self.rpc = None

        try:
            self.rpc = Presence(self.client_id)
            self.rpc.connect()
            self.connected = True
            self.reconnect_attempts = 0
            self.reconnecting = False
            if self.gui_callback:
                self.gui_callback("Discord: Reconnected successfully")
            # Restore presence if we had one
            self._restore_presence()
            return True
        except Exception as e:
            self.connected = False
            self.rpc = None
            self.reconnecting = False
            if self.gui_callback:
                self.gui_callback(f"Discord: Reconnect failed - {e}")
            return False

    def _restore_presence(self):
        """Restore the last known presence after reconnection"""
        if not self.connected or not self.rpc:
            return
        if self.current_song and self.current_artist:
            try:
                details = f"{self.current_song}"[:128]
                state_text = f"by {self.current_artist}"[:128]
                self.rpc.update(
                    details=details,
                    state=state_text,
                    large_image="guitar",
                    large_text="Rock Band 3 Deluxe",
                    start=int(self.start_time) if self.start_time else None
                )
            except Exception:
                pass

    def disconnect(self):
        """Disconnect from Discord"""
        if self.rpc and self.connected:
            try:
                self.rpc.clear()
                self.rpc.close()
            except Exception:
                pass
            self.connected = False
            self.rpc = None
            if self.gui_callback:
                self.gui_callback("Discord Rich Presence disconnected")

    def update_presence(self, artist: str, song: str, state: str = "Playing"):
        """Update Discord presence with current song"""
        if not self.enabled:
            return

        # Store the info even if not connected (for restore after reconnect)
        self.current_artist = artist
        self.current_song = song
        self.start_time = time.time()

        # Try to reconnect if not connected
        if not self.connected or not self.rpc:
            self._try_reconnect()
            if not self.connected:
                return

        try:
            # Truncate if too long (Discord limits)
            details = f"{song}"[:128] if song else "Unknown Song"
            state_text = f"by {artist}"[:128] if artist else state

            # Update Discord Rich Presence
            result = self.rpc.update(
                details=details,
                state=state_text,
                large_image="guitar",  # RB3 Deluxe app asset
                large_text="Rock Band 3 Deluxe",
                start=int(self.start_time)
            )

            if self.gui_callback:
                self.gui_callback(f"Discord: Now playing - {artist} - {song}")
                # Log result for debugging
                if result:
                    self.gui_callback(f"Discord: Update response received")

        except Exception as e:
            self.connected = False
            if self.gui_callback:
                self.gui_callback(f"Discord update failed: {e}")
            # Schedule reconnect attempt
            self._try_reconnect()

    def clear_presence(self):
        """Clear Discord presence (when returning to menu)"""
        # Clear stored state regardless of connection
        self.current_song = None
        self.current_artist = None
        self.start_time = None

        if not self.connected or not self.rpc:
            return

        try:
            self.rpc.clear()
        except Exception:
            self.connected = False
            # Don't try to reconnect just to clear - will reconnect on next update

    def set_idle(self):
        """Set presence to idle/browsing state"""
        if not self.enabled:
            return

        # Try to reconnect if not connected
        if not self.connected or not self.rpc:
            self._try_reconnect()
            if not self.connected:
                return

        try:
            self.rpc.update(
                details="Browsing Songs",
                state="In Menus",
                large_image="guitar",
                large_text="Rock Band 3 Deluxe"
            )
        except Exception:
            self.connected = False
            self._try_reconnect()
