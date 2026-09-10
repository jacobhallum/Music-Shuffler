"""
tests/test_preference_score.py - deterministic scoring, separated from RNG.

Run:
    python -m unittest tests.test_preference_score -v
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from shuffle_core import (legacy_preference_score, normalize_shuffle_config)

NOW = 1_700_000_000.0


class PT:
    def __init__(self, **kw):
        self.filepath = kw.get('filepath', '/x.mp3')
        self.artist = kw.get('artist', 'A')
        self.rating = kw.get('rating', 0)
        self.play_count = kw.get('play_count', 0)
        self.skips = kw.get('skips', 0)
        self.loved = kw.get('loved', None)
        self.last_played = kw.get('last_played', None)
        self.date_added = kw.get('date_added', None)
        self.bpm = kw.get('bpm', 0)


class TestLegacyPreferenceScore(unittest.TestCase):

    def _cfg(self):
        return normalize_shuffle_config(None)

    def test_returns_finite_float(self):
        s = legacy_preference_score(PT(), self._cfg(), now=NOW)
        self.assertIsInstance(s, float)
        self.assertTrue(math.isfinite(s))

    def test_no_rng_dependence_is_deterministic(self):
        t = PT(rating=80, play_count=3)
        a = legacy_preference_score(t, self._cfg(), now=NOW)
        b = legacy_preference_score(t, self._cfg(), now=NOW)
        self.assertEqual(a, b)

    def test_higher_rating_scores_higher(self):
        cfg = self._cfg()
        lo = legacy_preference_score(PT(rating=20), cfg, now=NOW)
        hi = legacy_preference_score(PT(rating=100), cfg, now=NOW)
        self.assertGreater(hi, lo)

    def test_recent_track_is_penalised(self):
        cfg = self._cfg()
        t = PT(filepath='/song.mp3', artist='A')
        base = legacy_preference_score(t, cfg, now=NOW)
        penalised = legacy_preference_score(
            t, cfg, now=NOW,
            recent_tracks={os.path.normcase(os.path.normpath('/song.mp3'))})
        self.assertLess(penalised, base)

    def test_recent_artist_is_penalised(self):
        cfg = self._cfg()
        t = PT(artist='A')
        base = legacy_preference_score(t, cfg, now=NOW)
        penalised = legacy_preference_score(t, cfg, now=NOW, recent_artists={'a'})
        self.assertLess(penalised, base)

    def test_artist_skip_rate_lowers_score(self):
        cfg = self._cfg()
        t = PT(artist='A')
        base = legacy_preference_score(t, cfg, now=NOW)
        skipped = legacy_preference_score(t, cfg, now=NOW,
                                          artist_skip_rate={'a': 0.9})
        self.assertLess(skipped, base)


if __name__ == '__main__':
    unittest.main()
