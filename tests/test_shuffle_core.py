"""
tests/test_shuffle_core.py - stdlib-only unit tests for shuffle_core.py

Run with:
    python -m unittest tests/test_shuffle_core.py -v
"""

import json
import os
import random
import sys
import unittest

# Allow running from project root or tests/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from shuffle_core import CustomShuffleAlgorithm


class FakeTrack:
    """Minimal track-like object for testing (no GUI dependencies)."""

    def __init__(self, filepath, artist='', album='', genre='',
                 play_count=0, skips=0, rating=0, loved=None,
                 last_played=None, date_added=None, bpm=0):
        self.filepath    = filepath
        self.artist      = artist
        self.album       = album
        self.genre       = genre
        self.play_count  = play_count
        self.skips       = skips
        self.rating      = rating
        self.loved       = loved
        self.last_played = last_played
        self.date_added  = date_added
        self.bpm         = bpm


def _make_tracks(n, prefix='track', artist_pool=None, genre_pool=None):
    """Generate *n* FakeTrack objects with varied metadata."""
    tracks = []
    for i in range(n):
        artist = (artist_pool[i % len(artist_pool)]
                  if artist_pool else f"Artist_{i % max(1, n // 5)}")
        genre  = (genre_pool[i % len(genre_pool)]
                  if genre_pool else f"Genre_{i % 4}")
        tracks.append(FakeTrack(
            filepath   = f"/music/{prefix}_{i:04d}.mp3",
            artist     = artist,
            album      = f"Album_{i % max(1, n // 10)}",
            genre      = genre,
            play_count = i % 20,
            skips      = i % 3,
            rating     = (i % 5) + 1,
        ))
    return tracks


class TestSmartShuffleCompleteness(unittest.TestCase):
    """smart_shuffle must return every input track exactly once."""

    def _assert_permutation(self, tracks, result):
        self.assertEqual(len(result), len(tracks),
                         "Output length differs from input length")
        self.assertEqual(set(id(t) for t in result),
                         set(id(t) for t in tracks),
                         "Output is not a permutation of the input")

    def test_small_library(self):
        tracks = _make_tracks(10)
        result = CustomShuffleAlgorithm.smart_shuffle(tracks)
        self._assert_permutation(tracks, result)

    def test_medium_library(self):
        tracks = _make_tracks(500)
        result = CustomShuffleAlgorithm.smart_shuffle(tracks)
        self._assert_permutation(tracks, result)

    def test_single_track(self):
        tracks = _make_tracks(1)
        result = CustomShuffleAlgorithm.smart_shuffle(tracks)
        self._assert_permutation(tracks, result)

    def test_empty_library(self):
        result = CustomShuffleAlgorithm.smart_shuffle([])
        self.assertEqual(result, [])


class TestSmartShuffleRobustness(unittest.TestCase):
    """smart_shuffle must not crash on missing / None / empty metadata."""

    def test_none_metadata(self):
        tracks = [FakeTrack(
            filepath   = f"/music/t{i}.mp3",
            artist     = None,
            album      = None,
            genre      = None,
            play_count = None,
            skips      = None,
            rating     = None,
            loved      = None,
            last_played = None,
            date_added  = None,
            bpm         = None,
        ) for i in range(20)]
        result = CustomShuffleAlgorithm.smart_shuffle(tracks)
        self.assertEqual(len(result), 20)

    def test_empty_strings(self):
        tracks = [FakeTrack(
            filepath = f"/music/t{i}.mp3",
            artist='', album='', genre='',
        ) for i in range(20)]
        result = CustomShuffleAlgorithm.smart_shuffle(tracks)
        self.assertEqual(len(result), 20)

    def test_zero_numeric_fields(self):
        tracks = [FakeTrack(
            filepath   = f"/music/t{i}.mp3",
            play_count = 0,
            skips      = 0,
            rating     = 0,
            bpm        = 0,
        ) for i in range(20)]
        result = CustomShuffleAlgorithm.smart_shuffle(tracks)
        self.assertEqual(len(result), 20)


class TestAdaptiveConstraints(unittest.TestCase):
    """Adaptive constraints should increase spacing for large artist pools."""

    def _count_adjacent_artist_repeats(self, result):
        repeats = 0
        for i in range(1, len(result)):
            if result[i].artist and result[i].artist == result[i - 1].artist:
                repeats += 1
        return repeats

    def _longest_artist_run(self, result):
        longest = run = 0
        for i, t in enumerate(result):
            if i and t.artist and t.artist == result[i - 1].artist:
                run += 1
            else:
                run = 1
            longest = max(longest, run)
        return longest

    def test_adaptive_raises_a_too_small_cooldown_ceiling(self):
        """adaptive_constraints lifts the per-artist cooldown ceiling to >= 3.

        6 artists x 8 tracks: with the ceiling forced to 1 (adaptive off,
        recent_artists=1) the dominant-ish spacing is looser than with adaptive
        on (ceiling >= 3).  Metric-based, seed-injected.
        """
        artists = [f"Artist_{i}" for i in range(6)]
        tracks  = _make_tracks(48, artist_pool=artists)

        adaptive_on = CustomShuffleAlgorithm.smart_shuffle(
            list(tracks), {'adaptive_constraints': True, 'recent_artists': 1},
            rng=random.Random(42))
        adaptive_off = CustomShuffleAlgorithm.smart_shuffle(
            list(tracks), {'adaptive_constraints': False, 'recent_artists': 1},
            rng=random.Random(42))

        self.assertLessEqual(self._longest_artist_run(adaptive_on),
                             self._longest_artist_run(adaptive_off))
        self.assertLessEqual(self._count_adjacent_artist_repeats(adaptive_on),
                             self._count_adjacent_artist_repeats(adaptive_off))

    def test_adaptive_reduces_clumping_vs_minimal_constraints(self):
        """With many artists, adaptive mode should reduce same-artist adjacency."""
        artists = [f"Artist_{i}" for i in range(50)]
        tracks  = _make_tracks(200, artist_pool=artists)

        config_adaptive = {'adaptive_constraints': True,  'recent_artists': 2}
        config_off      = {'adaptive_constraints': False, 'recent_artists': 1, 'recent_albums': 1, 'recent_genres': 1}

        result_adaptive = CustomShuffleAlgorithm.smart_shuffle(
            list(tracks), config_adaptive, rng=random.Random(42))
        result_off = CustomShuffleAlgorithm.smart_shuffle(
            list(tracks), config_off, rng=random.Random(42))

        self.assertLessEqual(self._count_adjacent_artist_repeats(result_adaptive),
                             self._count_adjacent_artist_repeats(result_off),
                             "Adaptive constraints should not produce more repeats than minimal constraints")

    def test_adaptive_enabled_by_default(self):
        """Calling without config should use adaptive_constraints=True."""
        tracks = _make_tracks(100)
        result = CustomShuffleAlgorithm.smart_shuffle(tracks)
        self.assertEqual(len(result), 100)


class TestHistoryGuard(unittest.TestCase):
    """History guard should lower early-rank frequency of recent tracks/artists."""

    def _rank_of_recent(self, result, recent_fps):
        """Return mean rank (0-indexed) of tracks whose filepath is in recent_fps."""
        n = len(result)
        ranks = [i for i, t in enumerate(result) if t.filepath in recent_fps]
        if not ranks:
            return n  # Not found - best case (pushed to end)
        return sum(ranks) / len(ranks)

    def test_history_guard_depresses_recent_tracks(self):
        """Tracks in recent history should appear later on average.

        Determinism lever: injected ``rng`` + zeroed additive randomness +
        fixed ``now`` (legacy scoring).  When ``weighted`` mode lands it gets a
        parallel test using positive ``beta`` instead.
        """
        n = 100
        # One unique artist per track → repair phase does no swaps at all
        tracks = [FakeTrack(
            filepath   = f"/music/t{i:04d}.mp3",
            artist     = f"UniqueArtist_{i}",
            album      = f"UniqueAlbum_{i}",
            genre      = f"UniqueGenre_{i % 5}",
            play_count = i % 10,   # varied novelty
        ) for i in range(n)]

        # Mark first 10 tracks as recently played
        recent_fps     = {t.filepath for t in tracks[:10]}
        recent_history = [(t.filepath, t.artist.lower()) for t in tracks[:10]]

        config = {
            'history_guard_size':     10,
            'history_track_penalty':  0.9,  # strong penalty
            'history_artist_penalty': 0.0,
            'random_min':   0.0,
            'random_range': 0.0,
            'adaptive_constraints': False,
            'recent_artists': 1,
            'recent_albums':  1,
            'recent_genres':  1,
        }
        fixed_now = 1_700_000_000.0

        result_with    = CustomShuffleAlgorithm.smart_shuffle(
            list(tracks), config, recent_history=recent_history,
            rng=random.Random(3), now=fixed_now)
        result_without = CustomShuffleAlgorithm.smart_shuffle(
            list(tracks), config, recent_history=None,
            rng=random.Random(3), now=fixed_now)

        mean_rank_with    = self._rank_of_recent(result_with,    recent_fps)
        mean_rank_without = self._rank_of_recent(result_without, recent_fps)

        self.assertGreater(mean_rank_with, mean_rank_without,
                           "Recent tracks should rank later when history guard is active")

    def test_no_crash_with_empty_history(self):
        tracks = _make_tracks(50)
        result = CustomShuffleAlgorithm.smart_shuffle(tracks, recent_history=[])
        self.assertEqual(len(result), 50)


class TestDeterminism(unittest.TestCase):
    """Output must be deterministic under a fixed random seed."""

    def test_same_seed_same_result(self):
        tracks = _make_tracks(100)
        config = {}
        fixed_now = 1_700_000_000.0

        result_a = CustomShuffleAlgorithm.smart_shuffle(
            list(tracks), config, rng=random.Random(7), now=fixed_now)
        result_b = CustomShuffleAlgorithm.smart_shuffle(
            list(tracks), config, rng=random.Random(7), now=fixed_now)

        fps_a = [t.filepath for t in result_a]
        fps_b = [t.filepath for t in result_b]
        self.assertEqual(fps_a, fps_b,
                         "Same injected rng seed must produce identical shuffle order")


class TestRngClockSeam(unittest.TestCase):
    """Task 0a: injectable rng/now, behaviour-preserving when rng is None."""

    def test_injected_rng_is_isolated_from_global_random(self):
        tracks = _make_tracks(60)

        random.seed(999)
        out_a = [t.filepath for t in CustomShuffleAlgorithm.smart_shuffle(
            list(tracks), {}, rng=random.Random(42))]

        random.seed(111)
        out_b = [t.filepath for t in CustomShuffleAlgorithm.smart_shuffle(
            list(tracks), {}, rng=random.Random(42))]

        self.assertEqual(out_a, out_b,
                         "Same injected rng seed must yield same order "
                         "regardless of global random state")

    # Bit-identical backward compatibility of the rng=None path is locked by
    # TestLegacyGolden (a frozen pre-seam snapshot), not here.

    def test_now_is_injectable_without_error(self):
        tracks = _make_tracks(10)
        CustomShuffleAlgorithm.smart_shuffle(
            list(tracks), {}, rng=random.Random(1), now=1_000_000.0)

    def test_truly_random_accepts_injected_rng(self):
        tracks = _make_tracks(30)
        a = [t.filepath for t in
             CustomShuffleAlgorithm.truly_random(list(tracks), rng=random.Random(5))]
        b = [t.filepath for t in
             CustomShuffleAlgorithm.truly_random(list(tracks), rng=random.Random(5))]
        self.assertEqual(a, b)


def _golden_tracks(n, now):
    """Deterministic library that exercises every legacy scoring branch.

    Unlike the bare `_make_tracks`, this populates `last_played`, `date_added`,
    `loved` and `bpm` so `_recency_boost`, `_newness_boost`, `_love_score` and
    the bpm bonus all contribute - and so the injected `now` is load-bearing.
    """
    tracks = _make_tracks(n)
    for i, t in enumerate(tracks):
        t.last_played = None if i % 7 == 0 else now - (i * 3600) - 60
        t.date_added  = now - ((i % 90) + 1) * 86400
        t.loved       = (None, True, False)[i % 3]
        t.bpm         = (0, 0, 120, 140)[i % 4]
    return tracks


class TestLegacyScoring(unittest.TestCase):
    """Characterization of the legacy preference score.

    Task 2b replaces the draft+repair ORDERING for every mode, so a frozen
    final-order snapshot is no longer meaningful.  What `scoring_mode='legacy'`
    still guarantees is the legacy preference *score* - frozen here so a future
    refactor cannot silently change the weighting users have tuned against.
    """

    def test_legacy_preference_score_snapshot(self):
        from shuffle_core import legacy_preference_score, normalize_shuffle_config
        cfg = normalize_shuffle_config(None)
        now = 1_700_000_000.0
        cases = [
            dict(rating=0,   play_count=0,   last_played=None,           loved=None,  bpm=0),
            dict(rating=100, play_count=1,   last_played=now - 86400,    loved=True,  bpm=128),
            dict(rating=20,  play_count=50,  last_played=now - 3600,     loved=False, bpm=0),
            dict(rating=60,  play_count=5,   last_played=now - 30*86400, loved=None,  bpm=140),
            dict(rating=255, play_count=0,   last_played=0,              loved=True,  bpm=0),
        ]
        expected = [1.1450, 1.1544, 0.1619, 1.0008, 1.6150]
        for spec, exp in zip(cases, expected):
            t = _make_tracks(1)[0]
            for k, v in spec.items():
                setattr(t, k, v)
            got = legacy_preference_score(t, cfg, now=now)
            self.assertAlmostEqual(got, exp, places=3,
                                   msg=f"legacy score drift for {spec}")

    def test_legacy_order_is_stable_for_fixed_rng(self):
        """Regression guard on the stride-interleave legacy ordering itself."""
        fixture_path = os.path.join(os.path.dirname(__file__),
                                    'fixtures_shuffle_legacy_golden.json')
        with open(fixture_path) as f:
            g = json.load(f)
        random.seed(g['seed'])
        tracks = _golden_tracks(g['n'], g['now'])
        result = CustomShuffleAlgorithm.smart_shuffle(
            list(tracks), g['config'], now=g['now'])
        self.assertEqual([t.filepath for t in result], g['expected'])


class TestTrulyRandom(unittest.TestCase):
    def test_permutation(self):
        tracks = _make_tracks(50)
        result = CustomShuffleAlgorithm.truly_random(tracks)
        self.assertEqual(len(result), 50)
        self.assertEqual(set(id(t) for t in result), set(id(t) for t in tracks))

    def test_does_not_mutate_input(self):
        tracks = _make_tracks(20)
        original_order = [t.filepath for t in tracks]
        CustomShuffleAlgorithm.truly_random(tracks)
        self.assertEqual([t.filepath for t in tracks], original_order)


if __name__ == '__main__':
    unittest.main()
