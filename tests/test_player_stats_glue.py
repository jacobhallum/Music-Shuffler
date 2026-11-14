"""
tests/test_player_stats_glue.py - how MusicPlayerGUI opens and feeds the Listening Stats window, with no Tk.

The real MusicPlayerGUI methods are bound onto a small fake (same pattern as tests/test_player_engine_glue.py).

Run with:
    python -m unittest tests.test_player_stats_glue -v
"""

import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import library_index  # noqa: E402
import listening_log  # noqa: E402
import music_player  # noqa: E402
from music_player import MusicPlayerGUI  # noqa: E402


START = datetime(2026, 11, 10, 12).timestamp()


class _Track:
    def __init__(self, path, n, artist='', album='', play_count=0):
        self.filepath = path
        self.title = os.path.basename(path.replace('\\', '/'))
        self.artist = artist
        self.album = album
        self.year = ''
        self.track_number = str(n)
        self.duration = 200
        self.play_count = play_count
        self.qualified_play_count = None
        self.manual_skip_count = 2
        self.last_played = None
        self.date_added = 1000
        self.loved = True


class _Window:
    made = []

    def __init__(self, master, on_period, menu_items, theme):
        self.on_period, self.menu_items, self.theme = on_period, menu_items, theme
        self.lifted = 0
        self.loading = []
        self.shown = []
        self.alive = True
        _Window.made.append(self)

    def winfo_exists(self):
        return self.alive

    def lift(self):
        self.lifted += 1

    def show_loading(self, label):
        self.loading.append(label)

    def show(self, *result):
        self.shown.append(result)

    def show_error(self, text):
        self.shown.append(('error', text))


class _Fake:
    def __init__(self, log_path):
        self.root = None
        self.themes = {'dark': {'bg': 'd'}, 'light': {'bg': 'l'}}
        self.dark_mode = True
        self.library_tracks = [_Track(r'X:\Lib\A\One\01 a.mp3', 1, artist='Tag A', album='One', play_count=5),
                               _Track(r'X:\Lib\A\One\02 b.mp3', 2, artist='Tag A', album='One')]
        self.library_index = library_index.build_index(self.library_tracks, roots=['X:\\Lib'])
        self.listening_log_file = log_path
        self._stats_window = None
        self._stats_data = None
        self._stats_records = None
        self._stats_token = 0
        self.posted = []
        self.posted_event = threading.Event()
        self.calls = []

    def _post_to_main(self, fn):
        self.posted.append(fn)
        self.posted_event.set()

    def run_posted(self, timeout=5):
        self.assert_posted(timeout)
        while self.posted:
            self.posted.pop(0)()

    def assert_posted(self, timeout=5):
        if not self.posted_event.wait(timeout):
            raise AssertionError("worker never posted a result")
        self.posted_event.clear()

    def queue_track(self, track, play_next=False):
        self.calls.append(('queue', track.title, play_next))

    def play_album(self, key, start_track=None):
        self.calls.append(('album', key))

    def play_artist(self, key):
        self.calls.append(('artist', key))

    def queue_album(self, key):
        self.calls.append(('queue_album', key))

    open_stats_window = MusicPlayerGUI.open_stats_window
    _stats_snapshot = MusicPlayerGUI._stats_snapshot
    _stats_compute = staticmethod(MusicPlayerGUI._stats_compute)
    _stats_period = MusicPlayerGUI._stats_period
    _stats_menu_items = MusicPlayerGUI._stats_menu_items
    _stats_album_items = MusicPlayerGUI._stats_album_items
    _album_action = MusicPlayerGUI._album_action


class StatsGlueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.tmp.name, 'listening_log.jsonl')
        listening_log.append(self.log, listening_log.make_record(
            started_at=START, ended_at=START + 200, path=r'X:\Lib\A\One\01 a.mp3', title='01 a.mp3', artist='Tag A',
            album='One', genre='', duration_sec=200, audible_sec=200, reason='natural_end', qualified=True,
            skip=False, transition='cut', ctx='album'))
        _Window.made = []
        patcher = mock.patch.object(music_player, 'StatsWindow', _Window)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.p = _Fake(self.log)

    def test_open_twice_raises_one_window(self):
        p = self.p
        p.open_stats_window()
        p.run_posted()
        p.open_stats_window()
        self.assertEqual(len(_Window.made), 1)
        self.assertEqual(_Window.made[0].lifted, 1)
        self.assertEqual(_Window.made[0].theme, {'bg': 'd'})
        self.assertEqual(_Window.made[0].loading, ['This week'])

    def test_snapshot_uses_index_places_and_tag_fallback(self):
        tracks, places, by_path = self.p._stats_snapshot()
        a = tracks[0]
        self.assertEqual((a.place.artist_key, a.place.album_key), ('a', library_index.norm_path(r'X:\Lib\A\One')))
        self.assertEqual((a.play_count, a.qualified_play_count, a.manual_skip_count, a.loved), (5, 0, 2, True))
        self.assertIs(by_path[a.path], self.p.library_tracks[0])
        self.p.library_index = None
        tracks, places, _ = self.p._stats_snapshot()
        self.assertEqual(places, {})
        self.assertEqual((tracks[0].place.artist_key, tracks[0].place.album_key), ('tag a', 'tag|tag a|one'))

    def test_stats_compute_runs_off_the_main_thread(self):
        p = self.p
        seen = []
        real_read = listening_log.read

        def read(path):
            seen.append(threading.current_thread() is threading.main_thread())
            return real_read(path)

        with mock.patch.object(music_player.listening_log, 'read', read):
            p.open_stats_window()
            p.run_posted()
        self.assertEqual(seen, [False])
        label, stats, months, favorites, never, all_time = _Window.made[0].shown[0]
        self.assertEqual(label, 'This week')
        self.assertFalse(all_time)
        self.assertEqual(len(months), 1)
        self.assertEqual([t.title for t in favorites], ['01 a.mp3', '02 b.mp3'])
        self.assertEqual([t.title for t in never], ['02 b.mp3'])

    def test_period_switch_reuses_loaded_records(self):
        p = self.p
        reads = []
        real_read = listening_log.read
        with mock.patch.object(music_player.listening_log, 'read', lambda path: reads.append(1) or real_read(path)):
            p.open_stats_window()
            p.run_posted()
            p._stats_period('all')
            p.run_posted()
        self.assertEqual(len(reads), 1)
        label, stats, *_rest, all_time = _Window.made[0].shown[1]
        self.assertTrue(all_time)
        self.assertTrue(label.startswith('All time · summary since '))
        self.assertEqual(stats.top_songs[0].plays, 5)               # from play_count, not the log

    def test_only_the_newest_period_is_shown(self):
        p = self.p
        p.open_stats_window()
        p.assert_posted()
        p._stats_period((2025, 1))
        p.assert_posted()
        for fn in list(p.posted):
            fn()
        self.assertEqual(len(_Window.made[0].shown), 1)
        self.assertEqual(_Window.made[0].shown[0][0], 'January 2025')

    def test_a_log_line_with_a_bad_start_is_skipped(self):
        listening_log.append(self.log, dict(listening_log.read(self.log)[0], start=5))
        self.p.open_stats_window()
        self.p.run_posted()
        label, stats, months, *_ = _Window.made[0].shown[0]
        self.assertEqual(len(months), 1)

    def test_a_failed_load_says_so(self):
        p = self.p
        with mock.patch.object(music_player.listening_stats, 'period_stats', side_effect=RuntimeError('boom')):
            p.open_stats_window()
            p.run_posted()
        self.assertEqual(_Window.made[0].shown, [('error', "Couldn't load stats: boom")])

    def test_actions_map_to_player_methods(self):
        p = self.p
        p.open_stats_window()
        p.run_posted()
        path = library_index.norm_path(r'X:\Lib\A\One\01 a.mp3')
        items = dict(p._stats_menu_items('song', path))
        self.assertEqual(list(items)[:2], ['Play Next', 'Add to Up Next'])
        items['Play Next']()
        items['Add album to Up Next']()
        items['Play artist']()
        album_key = library_index.norm_path(r'X:\Lib\A\One')
        self.assertEqual(p.calls, [('queue', '01 a.mp3', True), ('queue_album', album_key), ('artist', 'a')])
        self.assertEqual(p._stats_menu_items('song', 'x:\\gone.mp3'), [])
        self.assertEqual(p._stats_menu_items('album', 'tag|old|x'), [])
        dict(p._stats_menu_items('artist', 'a'))['Play artist']()
        self.assertEqual(p.calls[-1], ('artist', 'a'))


if __name__ == '__main__':
    unittest.main()
