"""
tests/test_audio_settings_handcheck.py - hand-worked checks for audio_settings.py

Run with:
    python -m unittest tests/test_audio_settings_handcheck.py -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from audio_settings import *  # noqa: F401,F403


class AudioSettingsHandcheck(unittest.TestCase):
    def test_defaults(self):
        d = normalize(None)
        self.assertEqual(d, DEFAULTS)
        self.assertIsNot(d, DEFAULTS)
        self.assertIsNot(d['eq_bands_db'], DEFAULTS['eq_bands_db'])

    def test_seconds(self):
        def f(v):
            return normalize({'transition_seconds': v})['transition_seconds']
        self.assertEqual([f(12.6), f(0), f(3.5), f(2.5), f(True), f('7'), f(float('nan'))], [12, 1, 4, 3, 6, 6, 6])

    def test_bands(self):
        n = normalize({'eq_bands_db': [15, -13.04, 3.14159] + [0] * 7, 'eq_preamp_db': -20})
        self.assertEqual(n['eq_bands_db'][:3], [12.0, -12.0, 3.1])
        self.assertEqual(n['eq_preamp_db'], -12.0)
        self.assertEqual(normalize({'eq_bands_db': [1] * 9})['eq_bands_db'], [0.0] * 10)
        self.assertEqual(normalize({'eq_bands_db': [1] * 9 + ['x']})['eq_bands_db'], [0.0] * 10)

    def test_mode_bools_unknown_keys(self):
        n = normalize({'transition_mode': 'automix', 'eq_enabled': 1, 'leveling_enabled': True, 'bogus': 1})
        self.assertEqual((n['transition_mode'], n['eq_enabled'], n['leveling_enabled']), ('off', False, True))
        self.assertNotIn('bogus', n)

    def test_user_presets(self):
        n = normalize({'eq_user_presets': {'Mine': {'bands_db': [2] * 10, 'preamp_db': -3},
                                           '': {'bands_db': [0] * 10, 'preamp_db': 0},
                                           'Bad': {'bands_db': [1] * 3, 'preamp_db': 0}}})
        self.assertEqual(n['eq_user_presets'], {'Mine': {'bands_db': [2.0] * 10, 'preamp_db': -3.0}})

    def test_never_raises_on_hostile_input(self):
        n = normalize({'transition_seconds': 10 ** 400, 'eq_bands_db': [10 ** 400] * 10, 'eq_preamp_db': 10 ** 400})
        self.assertEqual((n['transition_seconds'], n['eq_bands_db'], n['eq_preamp_db']), (6, [0.0] * 10, 0.0))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'audio_settings.json')
            with open(p, 'w') as fh:
                fh.write('[' * 100000)
            self.assertEqual(load(p), DEFAULTS)

    def test_load_save(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'audio_settings.json')
            self.assertEqual(load(p), DEFAULTS)
            with open(p, 'w') as fh:
                fh.write('{not json')
            self.assertEqual(load(p), DEFAULTS)
            s = normalize({'transition_mode': 'smart', 'transition_seconds': 9, 'eq_enabled': True})
            save(p, s)
            self.assertEqual(load(p), s)
            self.assertEqual(os.listdir(d), ['audio_settings.json'])


if __name__ == '__main__':
    unittest.main()
