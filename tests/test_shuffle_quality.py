"""
tests/test_shuffle_quality.py - measurable spacing/variety regression tests.

Several tests here are @unittest.expectedFailure against the current
draft-and-repair core.  Task 2 (bucketed stride-interleave) removes the
decorator as each defect is fixed - a test that starts passing early will
flag itself as an "unexpected success".

Run:
    python -m unittest tests.test_shuffle_quality -v
"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from shuffle_core import CustomShuffleAlgorithm

NOW = 1_700_000_000.0


class QTrack:
    __slots__ = ('filepath', 'artist', 'album', 'genre', 'rating',
                 'play_count', 'skips', 'loved', 'last_played', 'date_added', 'bpm')

    def __init__(self, filepath, artist='', album='', genre='',
                 rating=0, play_count=0, skips=0):
        self.filepath = filepath
        self.artist = artist
        self.album = album
        self.genre = genre
        self.rating = rating
        self.play_count = play_count
        self.skips = skips
        self.loved = None
        self.last_played = None
        self.date_added = None
        self.bpm = 0


def _adjacent(seq, key):
    return sum(1 for a, b in zip(seq, seq[1:]) if key(a) == key(b) and key(a))


def _longest_run(seq, key):
    longest = run = 0
    prev = object()
    for t in seq:
        k = key(t)
        run = run + 1 if (k == prev and k) else 1
        prev = k
        longest = max(longest, run)
    return longest


class TestBalancedTwoArtist(unittest.TestCase):
    """10 A + 10 B, no album/genre constraint: perfect alternation is feasible."""

    def _lib(self):
        return ([QTrack(f'/a{i}.mp3', 'A') for i in range(10)] +
                [QTrack(f'/b{i}.mp3', 'B') for i in range(10)])

    @unittest.expectedFailure  # fixed by Task 2b (stride-interleave)
    def test_no_adjacent_repeats_across_seeds(self):
        total = 0
        for seed in range(100):
            out = CustomShuffleAlgorithm.smart_shuffle(
                self._lib(), {'adaptive_constraints': True},
                rng=random.Random(seed), now=NOW)
            total += _adjacent(out, lambda t: t.artist)
        self.assertEqual(total, 0,
                         f"perfect A/B alternation is feasible; got {total} "
                         f"adjacent repeats over 100 seeds")

    def test_completeness(self):
        out = CustomShuffleAlgorithm.smart_shuffle(
            self._lib(), {}, rng=random.Random(0), now=NOW)
        self.assertEqual(len(out), 20)
        self.assertEqual({t.filepath for t in out},
                         {t.filepath for t in self._lib()})


class TestSkewedLibrary(unittest.TestCase):
    """700 dominant + 300 spread: min adjacent 399, but runs should be short."""

    def _lib(self):
        return ([QTrack(f'/d{i}.mp3', 'Dom') for i in range(700)] +
                [QTrack(f'/o{i}.mp3', f'Art{i % 30}') for i in range(300)])

    @unittest.expectedFailure  # fixed by Task 2b (stride priority)
    def test_dominant_artist_not_one_long_tail_run(self):
        out = CustomShuffleAlgorithm.smart_shuffle(
            self._lib(), {}, rng=random.Random(0), now=NOW)
        self.assertLessEqual(_longest_run(out, lambda t: t.artist), 5,
                             "dominant artist should be spread as short runs, "
                             "not dumped as one tail block")

    def test_no_tracks_lost_on_skew(self):
        lib = self._lib()
        out = CustomShuffleAlgorithm.smart_shuffle(
            list(lib), {}, rng=random.Random(0), now=NOW)
        self.assertEqual(len(out), len(lib))


class TestHistoryGuardSizeZero(unittest.TestCase):

    @unittest.expectedFailure  # fixed by Task 2a (normalize_shuffle_config: 0 disables)
    def test_zero_guard_size_disables_history(self):
        """history_guard_size=0 must mean 'no guard', not '[-0:] == whole list'.

        Only the first 10 (unique-artist) tracks are 'recent'.  With the guard
        genuinely disabled, passing that history changes nothing; the current
        `[-0:]` bug still penalises those 10 and reorders them.
        """
        tracks = [QTrack(f'/t{i:03d}.mp3', f'Art{i}', play_count=i % 7)
                  for i in range(40)]
        recent = [(t.filepath, t.artist.lower()) for t in tracks[:10]]

        cfg = {'history_guard_size': 0, 'history_track_penalty': 0.9,
               'random_min': 0.0, 'random_range': 0.0,
               'adaptive_constraints': False,
               'recent_artists': 1, 'recent_albums': 1, 'recent_genres': 1}

        with_hist = CustomShuffleAlgorithm.smart_shuffle(
            list(tracks), cfg, recent_history=recent,
            rng=random.Random(1), now=NOW)
        without = CustomShuffleAlgorithm.smart_shuffle(
            list(tracks), cfg, recent_history=None,
            rng=random.Random(1), now=NOW)

        self.assertEqual([t.filepath for t in with_hist],
                         [t.filepath for t in without],
                         "guard size 0 must be a no-op")


class TestUnknownTagPositionalSpacing(unittest.TestCase):

    @unittest.expectedFailure  # fixed by Task 2b (synthetic bucket + positional advance)
    def test_unknown_artist_songs_advance_the_window(self):
        """Songs with no artist must still consume spacing positions.

        Layout: A, <unknown>, <unknown>, A, ...  With a positional window of 3
        the two A's are 3 apart and should be accepted; the bug counts only
        tagged entries so unknowns are invisible to the cooldown.
        """
        tracks = []
        for block in range(20):
            tracks.append(QTrack(f'/a{block}.mp3', 'A'))
            tracks.append(QTrack(f'/u{block}a.mp3', ''))
            tracks.append(QTrack(f'/u{block}b.mp3', ''))

        out = CustomShuffleAlgorithm.smart_shuffle(
            list(tracks), {'adaptive_constraints': False, 'recent_artists': 2,
                           'recent_albums': 0, 'recent_genres': 0},
            rng=random.Random(0), now=NOW)

        # No two 'A' tracks adjacent, and every unknown still placed.
        self.assertEqual(_adjacent(out, lambda t: t.artist), 0)
        self.assertEqual(len(out), 60)


if __name__ == '__main__':
    unittest.main()
