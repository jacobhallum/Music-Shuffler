"""
tests/test_diagnostics.py - explain_preference_score stays in sync with the
real scorer (Task 9).

Run:
    python -m unittest tests.test_diagnostics -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from shuffle_core import (explain_preference_score, legacy_preference_score,
                          normalize_shuffle_config, weighted_score_cfg)

NOW = 1_700_000_000.0


class T:
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
        self.qualified_play_count = kw.get('qualified_play_count', None)
        self.manual_skip_count = kw.get('manual_skip_count', None)
        self.last_skipped_at = kw.get('last_skipped_at', None)


class TestExplain(unittest.TestCase):

    def test_score_matches_the_real_scorer_legacy(self):
        t = T(rating=80, play_count=3, loved=True)
        info = explain_preference_score(t, None, now=NOW)
        cfg = normalize_shuffle_config(None)
        self.assertAlmostEqual(info['score'],
                               legacy_preference_score(t, cfg, now=NOW), places=9)

    def test_score_matches_real_scorer_weighted(self):
        t = T(rating=100, loved=True, play_count=1)
        info = explain_preference_score(t, {'scoring_mode': 'weighted'}, now=NOW)
        cfg = weighted_score_cfg(normalize_shuffle_config(None))
        self.assertAlmostEqual(info['score'],
                               legacy_preference_score(t, cfg, now=NOW), places=9)

    def test_components_sum_to_score(self):
        # No artist_skip_rate / history passed here, so the components account
        # for the score exactly (bpm_bonus included).
        for bpm in (0, 128):
            t = T(rating=60, play_count=10, loved=False, last_played=NOW - 5 * 86400,
                  bpm=bpm, manual_skip_count=3, qualified_play_count=1,
                  last_skipped_at=NOW - 3600)
            info = explain_preference_score(t, None, now=NOW)
            comp_total = sum(v for _, v in info['components'])
            self.assertAlmostEqual(comp_total, info['score'], places=6,
                                   msg=f"bpm={bpm}")

    def test_weighted_mode_zeroes_rating_component(self):
        t = T(rating=100)
        info = explain_preference_score(t, {'scoring_mode': 'weighted'}, now=NOW)
        rating_comp = dict(info['components'])['rating']
        self.assertEqual(rating_comp, 0.0)

    def test_feedback_block_reports_counters(self):
        t = T(manual_skip_count=5, qualified_play_count=2)
        info = explain_preference_score(t, None, now=NOW)
        self.assertEqual(info['feedback']['manual_skip_count'], 5)
        self.assertLess(dict(info['components'])['feedback_penalty'], 0.0)


if __name__ == '__main__':
    unittest.main()
