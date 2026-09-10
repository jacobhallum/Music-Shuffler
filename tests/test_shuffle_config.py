"""
tests/test_shuffle_config.py - normalize_shuffle_config validation/clamping.

Run:
    python -m unittest tests.test_shuffle_config -v
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from shuffle_core import normalize_shuffle_config, DEFAULT_SHUFFLE_CONFIG


class TestDefaults(unittest.TestCase):

    def test_none_yields_full_default_set(self):
        cfg = normalize_shuffle_config(None)
        for key in DEFAULT_SHUFFLE_CONFIG:
            self.assertIn(key, cfg)
        self.assertEqual(cfg['scoring_mode'], 'legacy')
        self.assertIn('config_version', cfg)

    def test_empty_dict_equals_none(self):
        self.assertEqual(normalize_shuffle_config({}), normalize_shuffle_config(None))

    def test_does_not_mutate_input(self):
        src = {'recent_artists': -5}
        normalize_shuffle_config(src)
        self.assertEqual(src, {'recent_artists': -5})


class TestWindowClamping(unittest.TestCase):

    def test_negative_window_becomes_zero(self):
        self.assertEqual(normalize_shuffle_config({'recent_artists': -3})['recent_artists'], 0)

    def test_fractional_window_truncates_to_int(self):
        cfg = normalize_shuffle_config({'recent_albums': 2.9})
        self.assertEqual(cfg['recent_albums'], 2)
        self.assertIsInstance(cfg['recent_albums'], int)

    def test_absurd_window_is_bounded(self):
        self.assertLessEqual(normalize_shuffle_config({'recent_genres': 10 ** 6})['recent_genres'], 50)

    def test_non_numeric_window_falls_back_to_default(self):
        cfg = normalize_shuffle_config({'recent_artists': 'lots'})
        self.assertEqual(cfg['recent_artists'], DEFAULT_SHUFFLE_CONFIG['recent_artists'])


class TestHistoryGuardSize(unittest.TestCase):

    def test_zero_is_preserved_as_disabled(self):
        self.assertEqual(normalize_shuffle_config({'history_guard_size': 0})['history_guard_size'], 0)

    def test_negative_becomes_zero(self):
        self.assertEqual(normalize_shuffle_config({'history_guard_size': -10})['history_guard_size'], 0)

    def test_upper_bound_200(self):
        self.assertEqual(normalize_shuffle_config({'history_guard_size': 999})['history_guard_size'], 200)


class TestLookahead(unittest.TestCase):

    def test_lower_bound_1(self):
        self.assertEqual(normalize_shuffle_config({'lookahead': 0})['lookahead'], 1)

    def test_upper_bound_1000(self):
        self.assertEqual(normalize_shuffle_config({'lookahead': 9999})['lookahead'], 1000)


class TestWeightValidation(unittest.TestCase):

    def test_nan_falls_back_to_default(self):
        cfg = normalize_shuffle_config({'rating_weight': float('nan')})
        self.assertEqual(cfg['rating_weight'], DEFAULT_SHUFFLE_CONFIG['rating_weight'])

    def test_inf_falls_back_to_default(self):
        cfg = normalize_shuffle_config({'novelty_weight': float('inf')})
        self.assertEqual(cfg['novelty_weight'], DEFAULT_SHUFFLE_CONFIG['novelty_weight'])

    def test_negative_weight_clamped_to_zero(self):
        self.assertEqual(normalize_shuffle_config({'loved_weight': -0.5})['loved_weight'], 0.0)

    def test_string_weight_falls_back(self):
        cfg = normalize_shuffle_config({'recency_weight': 'high'})
        self.assertEqual(cfg['recency_weight'], DEFAULT_SHUFFLE_CONFIG['recency_weight'])


class TestScoringMode(unittest.TestCase):

    def test_unknown_mode_falls_back_to_legacy(self):
        self.assertEqual(normalize_shuffle_config({'scoring_mode': 'turbo'})['scoring_mode'], 'legacy')

    def test_weighted_mode_preserved(self):
        self.assertEqual(normalize_shuffle_config({'scoring_mode': 'weighted'})['scoring_mode'], 'weighted')


class TestUnknownKeys(unittest.TestCase):

    def test_unknown_keys_pass_through(self):
        cfg = normalize_shuffle_config({'future_flag': 42})
        self.assertEqual(cfg['future_flag'], 42)


if __name__ == '__main__':
    unittest.main()
