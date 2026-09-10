"""
tests/test_player_shuffle.py - player-level reshuffle behaviour (Task 4).

Exercises MusicPlayerGUI._apply_shuffle_results against a minimal fake, with
no Tk / pygame.  Focus: the fixed prefix (active track + queued block)
survives a reshuffle, paused counts as active, stopped does not.

Run:
    python -m unittest tests.test_player_shuffle -v
"""

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import playlist_queue


class _Track:
    def __init__(self, path):
        self.filepath = path
        self.title = path

    def __repr__(self):
        return f"<{self.filepath}>"


class _FakePlayer:
    """Just enough surface for _apply_shuffle_results / the prefix snapshot."""

    def __init__(self, playlist, current_index, queued_count, state):
        self.current_playlist = list(playlist)
        self.current_index = current_index
        self.queued_count = queued_count
        self._playback_state = state
        self.is_temp_view = False
        self.filtered_playlist = ['stale']
        self.search_var = types.SimpleNamespace(set=lambda v: None)
        self.status_label = types.SimpleNamespace(config=lambda **k: None)
        self.played_index = None
        self._nav_enabled = None

    # --- real methods under test, bound from the class ---
    _apply_shuffle_results = None  # filled in below
    _active_track = None
    _get_queued_tracks = None

    # --- collaborators ---
    def _norm_path(self, p):
        return os.path.normcase(os.path.normpath(p or ""))

    def display_playlist(self):
        pass

    def _set_navigation_enabled(self, v):
        self._nav_enabled = v

    def play_track_at_index(self, i):
        self.played_index = i


# Bind the real implementations onto the fake (import lazily to avoid Tk at
# module import time is unnecessary - music_player imports tkinter but does not
# create a root until __main__).
from music_player import MusicPlayerGUI  # noqa: E402

_FakePlayer._apply_shuffle_results = MusicPlayerGUI._apply_shuffle_results
_FakePlayer._active_track = MusicPlayerGUI._active_track
_FakePlayer._get_queued_tracks = MusicPlayerGUI._get_queued_tracks


class ReshufflePrefixTests(unittest.TestCase):

    def _snapshot_prefix(self, player, auto_play):
        if auto_play:
            return None, []
        return player._active_track(), list(player._get_queued_tracks())

    def test_playing_track_stays_at_index_0_no_replay(self):
        lib = [_Track(f'/m/{i}.mp3') for i in range(6)]
        p = _FakePlayer(lib, current_index=2, queued_count=0, state='playing')
        active, queued = self._snapshot_prefix(p, auto_play=False)
        tail = playlist_queue.reshuffle_tail_input(lib, active, queued, norm_fn=p._norm_path)

        p._apply_shuffle_results(tail, "Smart Shuffle", auto_play=False,
                                 active_track=active, queued_block=queued)

        self.assertEqual(p.current_playlist[0].filepath, '/m/2.mp3')
        self.assertEqual(p.current_index, 0)
        self.assertIsNone(p.played_index, "must not restart the playing track")
        self.assertEqual(sorted(t.filepath for t in p.current_playlist),
                         sorted(t.filepath for t in lib))

    def test_paused_track_is_preserved(self):
        lib = [_Track(f'/m/{i}.mp3') for i in range(5)]
        p = _FakePlayer(lib, current_index=3, queued_count=0, state='paused')
        active, queued = self._snapshot_prefix(p, auto_play=False)
        self.assertIsNotNone(active)
        tail = playlist_queue.reshuffle_tail_input(lib, active, queued, norm_fn=p._norm_path)
        p._apply_shuffle_results(tail, "Smart Shuffle", auto_play=False,
                                 active_track=active, queued_block=queued)
        self.assertEqual(p.current_playlist[0].filepath, '/m/3.mp3')
        self.assertEqual(p.current_index, 0)

    def test_stopped_track_is_not_preserved(self):
        lib = [_Track(f'/m/{i}.mp3') for i in range(5)]
        p = _FakePlayer(lib, current_index=3, queued_count=0, state='stopped')
        active, queued = self._snapshot_prefix(p, auto_play=False)
        self.assertIsNone(active)

    def test_stopped_with_queue_composition(self):
        lib = [_Track(f'/m/{i}.mp3') for i in range(8)]
        p = _FakePlayer(lib, current_index=1, queued_count=2, state='stopped')
        active, queued = self._snapshot_prefix(p, auto_play=False)
        self.assertIsNone(active)
        self.assertEqual([t.filepath for t in queued], ['/m/2.mp3', '/m/3.mp3'])
        tail = playlist_queue.reshuffle_tail_input(lib, active, queued)
        p._apply_shuffle_results(tail, "Smart Shuffle", auto_play=False,
                                 active_track=active, queued_block=queued)
        self.assertEqual([t.filepath for t in p.current_playlist[:2]],
                         ['/m/2.mp3', '/m/3.mp3'])
        self.assertEqual(p.queued_count, 2)
        self.assertEqual(sorted(t.filepath for t in p.current_playlist),
                         sorted(t.filepath for t in lib))

    def test_duplicate_normalized_path_not_lost(self):
        # two distinct objects, same filepath
        dup_a, dup_b = _Track('/m/same.mp3'), _Track('/m/same.mp3')
        lib = [dup_a, _Track('/m/1.mp3'), dup_b, _Track('/m/2.mp3')]
        p = _FakePlayer(lib, current_index=0, queued_count=0, state='playing')  # active = dup_a
        active, queued = self._snapshot_prefix(p, auto_play=False)
        tail = playlist_queue.reshuffle_tail_input(lib, active, queued)
        # dup_b must survive in the tail even though it shares dup_a's path
        self.assertIn(dup_b, tail)
        self.assertNotIn(dup_a, tail)
        p._apply_shuffle_results(tail, "Smart Shuffle", auto_play=False,
                                 active_track=active, queued_block=queued)
        self.assertEqual(len(p.current_playlist), 4)

    def test_up_next_block_survives_reshuffle(self):
        lib = [_Track(f'/m/{i}.mp3') for i in range(8)]
        # current at 1, queued block = indices 2,3
        p = _FakePlayer(lib, current_index=1, queued_count=2, state='playing')
        active, queued = self._snapshot_prefix(p, auto_play=False)
        self.assertEqual([t.filepath for t in queued], ['/m/2.mp3', '/m/3.mp3'])

        tail = playlist_queue.reshuffle_tail_input(lib, active, queued, norm_fn=p._norm_path)
        p._apply_shuffle_results(tail, "Smart Shuffle", auto_play=False,
                                 active_track=active, queued_block=queued)

        self.assertEqual([t.filepath for t in p.current_playlist[:3]],
                         ['/m/1.mp3', '/m/2.mp3', '/m/3.mp3'])
        self.assertEqual(p.current_index, 0)
        self.assertEqual(p.queued_count, 2)
        self.assertEqual(sorted(t.filepath for t in p.current_playlist),
                         sorted(t.filepath for t in lib))

    def test_auto_play_starts_at_zero_fresh(self):
        lib = [_Track(f'/m/{i}.mp3') for i in range(5)]
        p = _FakePlayer(lib, current_index=3, queued_count=0, state='playing')
        # auto_play => no prefix
        p._apply_shuffle_results(list(lib), "Smart Shuffle", auto_play=True,
                                 active_track=None, queued_block=[])
        self.assertEqual(p.current_index, 0)
        self.assertEqual(p.played_index, 0)
        self.assertEqual(p.queued_count, 0)

    def test_active_track_changes_when_playback_advances(self):
        # This is the invariant apply_if_latest relies on to detect that
        # playback auto-advanced while a Smart-shuffle worker was running.
        lib = [_Track(f'/m/{i}.mp3') for i in range(5)]
        p = _FakePlayer(lib, current_index=2, queued_count=0, state='playing')
        snapshot = p._active_track()
        p.current_index = 3          # auto-advance happened
        self.assertIsNot(p._active_track(), snapshot)

    def test_active_track_none_when_stopped_mid_shuffle(self):
        lib = [_Track(f'/m/{i}.mp3') for i in range(5)]
        p = _FakePlayer(lib, current_index=2, queued_count=0, state='playing')
        snapshot = p._active_track()
        p._playback_state = 'stopped'
        self.assertIsNone(p._active_track())
        self.assertIsNotNone(snapshot)

    def test_stale_filter_and_temp_view_cleared(self):
        lib = [_Track(f'/m/{i}.mp3') for i in range(4)]
        p = _FakePlayer(lib, current_index=0, queued_count=0, state='playing')
        p.is_temp_view = True
        active, queued = self._snapshot_prefix(p, auto_play=False)
        tail = playlist_queue.reshuffle_tail_input(lib, active, queued, norm_fn=p._norm_path)
        p._apply_shuffle_results(tail, "Smart Shuffle", auto_play=False,
                                 active_track=active, queued_block=queued)
        self.assertFalse(p.is_temp_view)
        self.assertEqual(p.filtered_playlist, [])
        self.assertTrue(p._nav_enabled)


if __name__ == '__main__':
    unittest.main()
