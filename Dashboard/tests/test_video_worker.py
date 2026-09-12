"""Exercise the receive loop with blocked video services and no real network."""

from queue import Empty, Queue
import socket
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rb3dashboard.listener import UnifiedRB3EListener


def packet(event_type, payload):
    return b'RB3E' + bytes((0, event_type, len(payload), 0)) + payload


class QueuedDatagramSocket:
    def __init__(self):
        self.datagrams = Queue()

    def setsockopt(self, *args):
        pass

    def settimeout(self, *args):
        pass

    def bind(self, *args):
        pass

    def recvfrom(self, size):
        try:
            data = self.datagrams.get(timeout=2)
        except Empty:
            raise socket.timeout()
        if data is None:
            raise OSError('Socket closed')
        return data, ('192.0.2.10', 21070)

    def close(self):
        self.datagrams.put(None)


class VideoWorkerTests(unittest.TestCase):
    def setUp(self):
        self.sock = QueuedDatagramSocket()
        self.started = threading.Event()
        self.release = threading.Event()
        self.ended = threading.Event()
        self.markers = []
        self.searches = []
        self.extractions = []
        self.plays = []
        self.logs = []
        self.listener = UnifiedRB3EListener(
            stagekit_callback=lambda left, right: self.markers[left].set(),
            song_ended_callback=lambda *args: self.ended.set(),
            gui_callback=self.logs.append,
        )
        self.searcher = SimpleNamespace(search_video=self.search,
                                        get_cached_title=lambda video_id: video_id)
        self.extractor = SimpleNamespace(get_stream_url=self.extract)
        self.listener.set_video_components(
            self.searcher,
            SimpleNamespace(play_video=lambda *args: self.plays.append(args),
                            stop_current_video=lambda: None),
            self.extractor,
        )
        self.settings = {'video_start_delay': 5}
        self.listener.update_video_settings(self.settings, enabled=True)
        self.socket_patch = patch('rb3dashboard.listener.socket.socket', return_value=self.sock)
        self.socket_patch.start()
        self.receiver = threading.Thread(target=self.listener.start_listening, daemon=True)
        self.receiver.start()
        self.addCleanup(self.cleanup_listener)

    def cleanup_listener(self):
        self.listener.stop()
        self.release.set()
        self.receiver.join(timeout=2)
        worker = getattr(self.listener, '_video_lookup_thread', None)
        if worker is not None:
            worker.join(timeout=2)
        self.socket_patch.stop()
        self.assertFalse(self.receiver.is_alive(), 'Receive loop did not stop')
        if worker is not None:
            self.assertFalse(worker.is_alive(), 'Lookup worker did not finish')

    def send(self, event_type, payload):
        self.sock.datagrams.put(packet(event_type, payload))

    def select_song(self, song):
        self.send(2, song.encode())
        self.send(3, b'Artist')
        self.send(4, song.lower().encode())

    def drain_packets(self):
        marker = threading.Event()
        index = len(self.markers)
        self.markers.append(marker)
        self.send(6, bytes((index, 0)))
        self.assertTrue(marker.wait(timeout=2), 'Video lookup blocked lighting packet dispatch')

    def finish_lookup(self):
        worker = getattr(self.listener, '_video_lookup_thread', None)
        if worker is not None:
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive(), 'Lookup worker did not finish')

    def search(self, artist, song):
        self.searches.append((artist, song))
        return song + '-video'

    def extract(self, video_id):
        self.extractions.append(video_id)
        return 'https://video.invalid/' + video_id

    def block_search(self, artist, song):
        self.searches.append((artist, song))
        if len(self.searches) == 1:
            self.started.set()
            self.release.wait(timeout=10)
        return song + '-video'

    def test_blocked_search_does_not_delay_lighting_or_song_state(self):
        self.listener.update_video_settings({'video_start_delay': 0}, enabled=True)
        self.searcher.search_video = self.block_search
        self.select_song('First')
        self.assertTrue(self.started.wait(timeout=2))
        self.send(1, b'\x01')
        self.drain_packets()
        self.assertEqual(self.listener.game_state, 1)
        self.assertEqual(self.plays, [])
        self.release.set()
        self.finish_lookup()
        self.assertEqual(len(self.plays), 1)
        self.assertEqual(self.plays[0][1:4], ('First-video', 'Artist', 'First'))

    def test_blocked_extraction_does_not_delay_menu_and_result_is_discarded(self):
        def extract(video_id):
            self.started.set()
            self.release.wait(timeout=10)
            return self.extract(video_id)

        self.extractor.get_stream_url = extract
        self.select_song('First')
        self.assertTrue(self.started.wait(timeout=2))
        self.send(1, b'\x01')
        self.send(1, b'\x00')
        self.drain_packets()
        self.assertTrue(self.ended.is_set())
        self.release.set()
        self.finish_lookup()
        self.assertIsNone(self.listener.pending_video)
        self.assertEqual(self.plays, [])

    def test_only_latest_waiting_song_is_looked_up_after_active_search(self):
        self.searcher.search_video = self.block_search
        self.select_song('First')
        self.assertTrue(self.started.wait(timeout=2))
        for song in ('Second', 'Third', 'Latest'):
            self.select_song(song)
        self.drain_packets()
        self.assertEqual(self.searches, [('Artist', 'First')])
        self.release.set()
        self.finish_lookup()
        self.assertEqual(self.searches, [('Artist', 'First'), ('Artist', 'Latest')])
        self.assertEqual(self.extractions, ['Latest-video'])
        self.assertEqual(self.listener.pending_video[3], 'Latest')

    def test_repeated_metadata_during_lookup_does_not_queue_duplicate_search(self):
        self.searcher.search_video = self.block_search
        self.select_song('First')
        self.assertTrue(self.started.wait(timeout=2))
        for _ in range(10):
            self.select_song('First')
        self.drain_packets()
        self.release.set()
        self.finish_lookup()
        self.assertEqual(self.searches, [('Artist', 'First')])
        self.assertEqual(self.extractions, ['First-video'])

    def test_stop_drops_waiting_work_without_waiting_for_active_search(self):
        self.searcher.search_video = self.block_search
        self.select_song('First')
        self.assertTrue(self.started.wait(timeout=2))
        self.select_song('Next')
        self.drain_packets()
        stopped = threading.Event()

        def stop():
            self.listener.stop()
            stopped.set()

        stopper = threading.Thread(target=stop, daemon=True)
        stopper.start()
        self.assertTrue(stopped.wait(timeout=2), 'Stop waited for the video service')
        stopper.join(timeout=2)
        self.release.set()
        self.finish_lookup()
        self.assertEqual(self.searches, [('Artist', 'First')])
        self.assertEqual(self.extractions, [])
        self.assertIsNone(self.listener.pending_video)

    def test_disable_drops_waiting_work_and_reenable_can_request_again(self):
        self.searcher.search_video = self.block_search
        self.select_song('First')
        self.assertTrue(self.started.wait(timeout=2))
        self.select_song('Next')
        self.drain_packets()
        self.listener.update_video_settings(self.settings, enabled=False)
        self.release.set()
        self.finish_lookup()
        self.assertEqual(self.searches, [('Artist', 'First')])
        self.assertEqual(self.extractions, [])
        self.assertIsNone(self.listener.pending_video)
        self.listener.update_video_settings(self.settings, enabled=True)
        self.listener.prepare_video()
        self.finish_lookup()
        self.assertEqual(self.listener.pending_video[3], 'Next')

    def test_failed_lookup_does_not_strand_newer_waiting_song(self):
        def search(artist, song):
            self.block_search(artist, song)
            if song == 'First':
                raise RuntimeError('Service unavailable')
            return song + '-video'

        self.searcher.search_video = search
        self.select_song('First')
        self.assertTrue(self.started.wait(timeout=2))
        self.select_song('Next')
        self.drain_packets()
        self.release.set()
        self.finish_lookup()
        self.assertEqual(self.listener.pending_video[3], 'Next')

    def test_lookup_can_retry_after_failure_without_changing_song(self):
        self.searcher.search_video = lambda *args: None
        self.select_song('First')
        self.drain_packets()
        self.finish_lookup()
        self.assertIsNone(self.listener.pending_video)
        self.searcher.search_video = self.search
        self.listener.prepare_video()
        self.finish_lookup()
        self.assertEqual(self.listener.pending_video[3], 'First')


if __name__ == '__main__':
    unittest.main()
