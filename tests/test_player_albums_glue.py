"""
tests/test_player_albums_glue.py - playing albums and artists in order, album leveling and the log's ctx field,
with no Tk or audio.

The real MusicPlayerGUI methods are bound onto a small fake (same pattern as tests/test_player_engine_glue.py).

Run with:
    python -m unittest tests.test_player_albums_glue -v
"""

import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import library_index  # noqa: E402
import listening_log  # noqa: E402
from loudness import TrackLoudness  # noqa: E402
from music_player import MusicPlayerGUI  # noqa: E402

ROOT = 'X:\\Lib'


class _Track:
    def __init__(self, path, n=None, artist='', album='', year=''):
        self.filepath = path
        self.original_filepath = path
        self.title = os.path.basename(path.replace('\\', '/'))
        self.artist = artist
        self.album = album
        self.genre = ''
        self.year = year
        self.track_number = n
        self.duration = 200
        self.last_played = None
        self.qualified_play_count = 0
        self.manual_skip_count = 0

    def __repr__(self):
        return f'_Track({self.title!r})'


def _lib():
    """Artist A: album One (2001, 3 songs), album Two (1999, 2 songs). Artist B: album Three (2 songs)."""
    one = [_Track(rf'X:\Lib\A\One\0{i} a{i}.mp3', n=str(i), album='One', year='2001') for i in (3, 1, 2)]
    two = [_Track(rf'X:\Lib\A\Two\0{i} b{i}.mp3', n=str(i), album='Two', year='1999') for i in (2, 1)]
    three = [_Track(rf'X:\Lib\B\Three\0{i} c{i}.mp3', n=str(i), album='Three') for i in (1, 2)]
    return one + two + three


def _key(folder):
    return library_index.norm_path(folder)


ONE, TWO, THREE = _key(r'X:\Lib\A\One'), _key(r'X:\Lib\A\Two'), _key(r'X:\Lib\B\Three')


def titles(tracks):
    return [t.title for t in tracks]


class _Status:
    def __init__(self):
        self.text = None

    def config(self, text=None, **kw):
        self.text = text


class _Var:
    def __init__(self, v=''):
        self.v = v

    def set(self, v):
        self.v = v

    def get(self):
        return self.v


class _Loud:
    def __init__(self, table=None, bad=()):
        self.table = dict(table or {})
        self.bad = set(bad)

    def get(self, path):
        return self.table.get(path)

    def known_bad(self, path):
        return path in self.bad


def L(i, p=-6.0, d=200.0):
    return TrackLoudness(i, p, d, (), ())


class _Fake:
    def __init__(self):
        self.library_tracks = _lib()
        self.library_index = library_index.build_index(self.library_tracks, roots=[ROOT])
        self.current_playlist = []
        self.current_index = 0
        self.queued_count = 0
        self.is_temp_view = False
        self.filtered_playlist = []
        self.search_var = _Var('')
        self.status_label = _Status()
        self._play_context = None
        self.audio_settings = {'eq_enabled': False, 'eq_bands_db': [0.0] * 10, 'eq_preamp_db': 0.0,
                               'leveling_enabled': True}
        self._loudness = _Loud()
        self.albums_tab = False
        self.played = []
        self.displays = 0
        self.analyses = []      # (paths, callback)
        self.prepared = 0

    # stand-ins
    def play_track_at_index(self, i):
        self.played.append(i)
        self.current_index = i

    def display_playlist(self):
        self.displays += 1

    def _analyze_then(self, tracks, callback):
        self.analyses.append(([t.filepath for t in tracks], callback))

    def _prepare_next(self, analyse=True):
        self.prepared += 1

    def _albums_tab_showing(self):
        return self.albums_tab

    def _set_navigation_enabled(self, on):
        pass

    def by_title(self, title):
        return next(t for t in self.library_tracks if t.title == title)

    def playing_shuffled(self, n=4):
        """A shuffled list of other songs, playing index 0, with queued songs after it."""
        self.current_playlist = [_Track(rf'X:\Lib\Z\S\{i:02} s{i}.mp3', n=str(i)) for i in range(n)]
        self.current_index = 0

    # real methods
    _takeover_playlist = MusicPlayerGUI._takeover_playlist
    play_album = MusicPlayerGUI.play_album
    play_artist = MusicPlayerGUI.play_artist
    queue_album = MusicPlayerGUI.queue_album
    queue_track = MusicPlayerGUI.queue_track
    _get_queued_tracks = MusicPlayerGUI._get_queued_tracks
    _norm_path = MusicPlayerGUI._norm_path
    _measure_context = MusicPlayerGUI._measure_context
    _context_albums = MusicPlayerGUI._context_albums
    _context_album_for = MusicPlayerGUI._context_album_for
    _context_kind_for = MusicPlayerGUI._context_kind_for
    _album_gain_for = MusicPlayerGUI._album_gain_for
    _filters_for = MusicPlayerGUI._filters_for
    _loudness_for = MusicPlayerGUI._loudness_for
    _apply_shuffle_results = MusicPlayerGUI._apply_shuffle_results


class PickTests(unittest.TestCase):
    def test_album_pick_takes_over_in_order_with_context(self):
        p = _Fake()
        p.play_album(ONE)
        self.assertEqual(titles(p.current_playlist), ['01 a1.mp3', '02 a2.mp3', '03 a3.mp3'])
        self.assertEqual(p.played, [0])
        self.assertEqual(p._play_context, ('album', ONE))
        self.assertTrue(p.is_temp_view)
        self.assertEqual(p.queued_count, 0)

    def test_song_pick_starts_at_that_song(self):
        p = _Fake()
        p.play_album(ONE, start_track=p.by_title('02 a2.mp3'))
        self.assertEqual(p.played, [1])
        self.assertEqual(p._play_context, ('album', ONE))

    def test_artist_pick_plays_albums_in_order(self):
        p = _Fake()
        p.play_artist('a')
        self.assertEqual(titles(p.current_playlist),
                         ['01 b1.mp3', '02 b2.mp3', '01 a1.mp3', '02 a2.mp3', '03 a3.mp3'])  # 1999 first
        self.assertEqual(p._play_context, ('artist', 'a'))

    def test_pick_carries_up_next_after_the_album(self):
        p = _Fake()
        p.playing_shuffled()
        p.queued_count = 2                       # s1, s2 are Up Next; s3 is shuffle
        p.play_album(THREE)
        self.assertEqual(titles(p.current_playlist), ['01 c1.mp3', '02 c2.mp3', '01 s1.mp3', '02 s2.mp3'])
        self.assertEqual(p.queued_count, 0)

    def test_pick_drops_carried_songs_already_in_album(self):
        p = _Fake()
        p.playing_shuffled()
        p.current_playlist.insert(1, p.by_title('02 c2.mp3'))
        p.queued_count = 2                       # c2 and s1 queued
        p.play_album(THREE)
        self.assertEqual(titles(p.current_playlist), ['01 c1.mp3', '02 c2.mp3', '01 s1.mp3'])

    def test_pick_clears_search_only_from_the_playlist_tab(self):
        p = _Fake()
        p.search_var.set('c1')
        p.albums_tab = True
        p.play_album(THREE)
        self.assertEqual(p.search_var.get(), 'c1')
        p.albums_tab = False
        p.play_album(THREE)
        self.assertEqual(p.search_var.get(), '')

    def test_search_takeover_still_drops_queue_and_context(self):
        p = _Fake()
        p.play_album(ONE)
        p.queued_count = 1
        p._takeover_playlist([p.by_title('01 c1.mp3')], 0)
        self.assertIsNone(p._play_context)
        self.assertEqual(titles(p.current_playlist), ['01 c1.mp3'])
        self.assertEqual(p.queued_count, 0)

    def test_unknown_album_does_nothing(self):
        p = _Fake()
        p.play_album('nope')
        p.play_artist('nope')
        self.assertEqual(p.played, [])


class QueueAlbumTests(unittest.TestCase):
    def test_queue_album_skips_playing_song_keeps_order(self):
        p = _Fake()
        p.playing_shuffled()
        p.current_playlist[0] = p.by_title('02 a2.mp3')     # a2 is playing
        p.queue_album(ONE)
        self.assertEqual(titles(p._get_queued_tracks()), ['01 a1.mp3', '03 a3.mp3'])
        self.assertEqual(p.current_index, 0)
        self.assertEqual(p.status_label.text, 'Added 2 songs to Up Next')

    def test_queue_album_goes_after_existing_up_next(self):
        p = _Fake()
        p.playing_shuffled()
        p.queued_count = 1
        p.queue_album(THREE)
        self.assertEqual(titles(p._get_queued_tracks()), ['01 s1.mp3', '01 c1.mp3', '02 c2.mp3'])

    def test_queue_album_refreshes_once(self):
        p = _Fake()
        p.playing_shuffled()
        p.queue_album(ONE)
        self.assertEqual(p.displays, 1)

    def test_queue_album_does_not_set_a_context(self):
        p = _Fake()
        p.playing_shuffled()
        p.queue_album(ONE)
        self.assertIsNone(p._play_context)


class ContextLifetimeTests(unittest.TestCase):
    def test_context_clears_on_shuffle(self):
        p = _Fake()
        p.play_album(ONE)
        p._apply_shuffle_results([p.by_title('01 c1.mp3')], 'Smart Shuffle', auto_play=True)
        self.assertIsNone(p._play_context)

    def test_context_clears_on_stop(self):
        p = _Fake()
        p.play_album(ONE)
        p.audio_available = True
        p._engine = type('E', (), {'stop': lambda self: None})()
        p._engine_lock = threading.Lock()
        p._finalize_playback = lambda reason: None
        p._cancel_pending_start = lambda: False
        p.now_playing_label = p.now_playing_artist = p.time_label = _Status()
        p._show_placeholder_artwork = lambda: None
        p.progress_var = _Var(0)
        MusicPlayerGUI.stop(p)
        self.assertIsNone(p._play_context)

    def test_new_pick_replaces_context(self):
        p = _Fake()
        p.play_album(ONE)
        p.play_artist('b')
        self.assertEqual(p._play_context, ('artist', 'b'))


class AlbumGainTests(unittest.TestCase):
    def setUp(self):
        self.p = _Fake()
        # album One: songs at -10 and -20 for equal time -> album gain -1.4; a1 alone would get -4.0
        one = self.p.library_index.album_tracks(ONE)
        self.p._loudness.table = {one[0].filepath: L(-10.0, -8.0), one[1].filepath: L(-20.0, -8.0),
                                  one[2].filepath: L(-10.0, -8.0, 0)}   # zero length: left out of the mean
        for t in self.p.library_index.album_tracks(TWO) + self.p.library_index.album_tracks(THREE):
            self.p._loudness.table[t.filepath] = L(-9.0)
        self.a1 = self.p.by_title('01 a1.mp3')

    def test_filters_use_album_gain_in_context(self):
        self.p.play_album(ONE)
        self.assertEqual(self.p._filters_for(self.a1), 'volume=-1.40dB')
        self.p.play_artist('a')
        self.assertEqual(self.p._filters_for(self.a1), 'volume=-1.40dB')

    def test_filters_use_song_gain_outside_context_and_for_up_next_strangers(self):
        self.assertEqual(self.p._filters_for(self.a1), 'volume=-4.00dB')      # shuffle
        self.p.play_album(THREE)
        self.assertEqual(self.p._filters_for(self.a1), 'volume=-4.00dB')      # not part of album Three

    def test_song_gain_until_album_fully_measured(self):
        a3 = self.p.by_title('03 a3.mp3')
        del self.p._loudness.table[a3.filepath]
        self.p.play_album(ONE)
        self.assertEqual(self.p._filters_for(self.a1), 'volume=-4.00dB')
        paths, callback = self.p.analyses[0]
        self.assertEqual(paths, [a3.filepath])
        self.p._loudness.table[a3.filepath] = L(-10.0, -8.0, 0)
        callback()
        self.assertEqual(self.p.prepared, 1)                                   # next song re-prepared
        self.assertEqual(self.p._filters_for(self.a1), 'volume=-1.40dB')

    def test_album_gain_ignores_known_bad_songs(self):
        a3 = self.p.by_title('03 a3.mp3')
        del self.p._loudness.table[a3.filepath]
        self.p._loudness.bad.add(a3.filepath)
        self.p.play_album(ONE)
        self.assertEqual(self.p.analyses, [])
        self.assertEqual(self.p._filters_for(self.a1), 'volume=-1.40dB')

    def test_artist_measures_album_by_album_and_stops_when_context_changes(self):
        p = self.p
        b1 = p.by_title('01 b1.mp3')
        a3 = p.by_title('03 a3.mp3')
        del p._loudness.table[b1.filepath]
        del p._loudness.table[a3.filepath]
        p.play_artist('a')
        self.assertEqual(p.analyses[0][0], [b1.filepath])     # album Two (1999) first
        p._loudness.table[b1.filepath] = L(-9.0)
        p.analyses[0][1]()
        self.assertEqual(p.analyses[1][0], [a3.filepath])     # then album One
        p.play_album(THREE)                                   # context changes before One finishes
        before = p.prepared
        p.analyses[1][1]()
        self.assertEqual(p.prepared, before)
        self.assertEqual(len(p.analyses), 2)

    def test_no_measuring_when_leveling_is_off(self):
        self.p.audio_settings['leveling_enabled'] = False
        del self.p._loudness.table[self.a1.filepath]
        self.p.play_album(ONE)
        self.assertEqual(self.p.analyses, [])
        self.assertEqual(self.p._filters_for(self.a1), '')


class TabSwitchTests(unittest.TestCase):
    """The one search box: a tab switch with unchanged text doesn't redraw the whole playlist."""

    def _p(self):
        p = _Fake()
        p._playlist_query = ''
        p._search_job = None
        p.current_playlist = list(p.library_tracks)
        p.filter_playlist = lambda: MusicPlayerGUI.filter_playlist(p)
        p._filter_albums = lambda: None
        return p

    def test_tab_switch_with_same_text_does_not_redraw_playlist(self):
        p = self._p()
        MusicPlayerGUI._run_filter(p)
        self.assertEqual(p.displays, 0)
        p.search_var.set('A1')
        MusicPlayerGUI._run_filter(p)
        self.assertEqual(p.displays, 1)
        self.assertEqual(titles(p.filtered_playlist), ['01 a1.mp3'])
        MusicPlayerGUI._run_filter(p)               # e.g. back from the Albums tab
        self.assertEqual(p.displays, 1)

    def test_text_kept_by_an_album_pick_filters_the_new_list(self):
        p = self._p()
        p.search_var.set('c1')
        MusicPlayerGUI._run_filter(p)
        p.albums_tab = True
        p.play_album(THREE)
        p.albums_tab = False                        # switch to Playlist
        MusicPlayerGUI._run_filter(p)
        self.assertEqual(titles(p.filtered_playlist), ['01 c1.mp3'])


class LogContextTests(unittest.TestCase):
    def test_log_record_gets_ctx(self):
        p = _Fake()
        p._pb_seq = 0
        p._pb_tracker = p._pb_track = None
        p._update_track_metadata = lambda track: None
        start = MusicPlayerGUI._start_playback_tracking
        finalize = MusicPlayerGUI._finalize_playback
        with tempfile.TemporaryDirectory() as d:
            p.listening_log_file = os.path.join(d, 'log.jsonl')
            p.play_album(ONE)
            start(p, p.by_title('01 a1.mp3'))
            finalize(p, 'stop')
            start(p, p.by_title('01 c1.mp3'))          # Up Next stranger inside the album context
            finalize(p, 'stop')
            p._play_context = None
            start(p, p.by_title('01 a1.mp3'))          # shuffle
            finalize(p, 'stop')
            recs = listening_log.read(p.listening_log_file)
        self.assertEqual([r.get('ctx') for r in recs], ['album', None, None])
        self.assertNotIn('ctx', recs[2])


if __name__ == '__main__':
    unittest.main()
