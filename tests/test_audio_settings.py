"""
tests/test_audio_settings.py - stdlib-only unit tests for audio_settings.py

Run with:
    python -m unittest tests/test_audio_settings.py -v
"""

import json
import os
import sys
import tempfile
import unittest

# Allow running from project root or tests/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import audio_settings as aus


class TmpMixin:
    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self.path = os.path.join(self._dir, 'audio_settings.json')

    def tearDown(self):
        for f in os.listdir(self._dir):
            os.unlink(os.path.join(self._dir, f))
        os.rmdir(self._dir)


class TestNormalizeDefaults(unittest.TestCase):
    def test_none_gives_defaults(self):
        self.assertEqual(aus.normalize(None), dict(aus.DEFAULTS))

    def test_non_dict_gives_defaults(self):
        for raw in ([], 'x', 42, True, None):
            self.assertEqual(aus.normalize(raw), dict(aus.DEFAULTS))

    def test_result_is_deep_copy(self):
        raw = {
            'eq_bands_db': [1.0] * 10,
            'eq_user_presets': {'Mine': {'bands_db': [0.0] * 10, 'preamp_db': 1.0}},
        }
        out = aus.normalize(raw)
        self.assertIsNot(out['eq_bands_db'], aus.DEFAULTS['eq_bands_db'])
        self.assertIsNot(out['eq_bands_db'], raw['eq_bands_db'])
        self.assertIsNot(out['eq_user_presets'], aus.DEFAULTS['eq_user_presets'])
        self.assertIsNot(out['eq_user_presets'], raw['eq_user_presets'])
        out['eq_bands_db'][0] = 9.9
        out['eq_user_presets']['Mine']['preamp_db'] = 9.9
        self.assertEqual(aus.DEFAULTS['eq_bands_db'], [0.0] * 10)
        self.assertEqual(raw['eq_bands_db'], [1.0] * 10)
        self.assertEqual(raw['eq_user_presets']['Mine']['preamp_db'], 1.0)

    def test_unknown_keys_dropped(self):
        out = aus.normalize({'version': 99, 'bogus': 1, 'transition_mode': 'crossfade'})
        self.assertEqual(set(out), set(aus.DEFAULTS))
        self.assertEqual(out['version'], 1)
        self.assertEqual(out['transition_mode'], 'crossfade')


class TestTransition(unittest.TestCase):
    def test_invalid_mode_falls_back(self):
        for bad in ('fade', 1, None, True):
            self.assertEqual(aus.normalize({'transition_mode': bad})['transition_mode'], 'off')

    def test_seconds_clamped_both_ends(self):
        self.assertEqual(aus.normalize({'transition_seconds': 0})['transition_seconds'], 1)
        self.assertEqual(aus.normalize({'transition_seconds': -5})['transition_seconds'], 1)
        self.assertEqual(aus.normalize({'transition_seconds': 13})['transition_seconds'], 12)
        self.assertEqual(aus.normalize({'transition_seconds': 100.9})['transition_seconds'], 12)

    def test_seconds_half_up_rounding(self):
        self.assertEqual(aus.normalize({'transition_seconds': 5.5})['transition_seconds'], 6)
        self.assertEqual(aus.normalize({'transition_seconds': 6.5})['transition_seconds'], 7)
        self.assertEqual(aus.normalize({'transition_seconds': 6.4})['transition_seconds'], 6)

    def test_seconds_bool_str_nan_rejected(self):
        for bad in (True, False, '6', float('nan'), float('inf'), None):
            self.assertEqual(
                aus.normalize({'transition_seconds': bad})['transition_seconds'], 6)


class TestFlagsAndPreset(unittest.TestCase):
    def test_flags_accept_only_real_bools(self):
        self.assertTrue(aus.normalize({'eq_enabled': True})['eq_enabled'])
        self.assertFalse(aus.normalize({'eq_enabled': 1})['eq_enabled'])
        self.assertFalse(aus.normalize({'eq_enabled': 'true'})['eq_enabled'])
        self.assertFalse(aus.normalize({'eq_enabled': None})['eq_enabled'])
        self.assertTrue(aus.normalize({'leveling_enabled': True})['leveling_enabled'])
        self.assertFalse(aus.normalize({'leveling_enabled': 1})['leveling_enabled'])

    def test_eq_preset(self):
        self.assertEqual(aus.normalize({'eq_preset': 'Rock'})['eq_preset'], 'Rock')
        self.assertEqual(aus.normalize({'eq_preset': 'x' * 64})['eq_preset'], 'x' * 64)
        self.assertEqual(aus.normalize({'eq_preset': 'x' * 65})['eq_preset'], '')
        self.assertEqual(aus.normalize({'eq_preset': 7})['eq_preset'], '')


class TestEqBands(unittest.TestCase):
    def test_bands_clamped_and_rounded(self):
        out = aus.normalize({'eq_bands_db': [20, -20, 1.06, -1.06, 0, 0, 0, 0, 0, 0]})
        bands = out['eq_bands_db']
        self.assertEqual(bands[0], 12.0)
        self.assertEqual(bands[1], -12.0)
        self.assertEqual(bands[2], 1.1)
        self.assertEqual(bands[3], -1.1)
        self.assertTrue(all(isinstance(v, float) for v in bands))

    def test_wrong_length_falls_back(self):
        self.assertEqual(aus.normalize({'eq_bands_db': [1.0] * 9})['eq_bands_db'],
                         [0.0] * 10)
        self.assertEqual(aus.normalize({'eq_bands_db': [1.0] * 11})['eq_bands_db'],
                         [0.0] * 10)

    def test_non_number_element_falls_back(self):
        for bad in ('1', True, None, float('nan')):
            bands = [1.0] * 9 + [bad]
            self.assertEqual(aus.normalize({'eq_bands_db': bands})['eq_bands_db'],
                             [0.0] * 10)

    def test_preamp_clamped_and_defaulted(self):
        self.assertEqual(aus.normalize({'eq_preamp_db': 15})['eq_preamp_db'], 12.0)
        self.assertEqual(aus.normalize({'eq_preamp_db': -15})['eq_preamp_db'], -12.0)
        self.assertEqual(aus.normalize({'eq_preamp_db': 2.56})['eq_preamp_db'], 2.6)
        for bad in ('2', True, float('nan'), None):
            self.assertEqual(aus.normalize({'eq_preamp_db': bad})['eq_preamp_db'], 0.0)


class TestUserPresets(unittest.TestCase):
    @staticmethod
    def _valid(bands=0.0, preamp=0.0):
        return {'bands_db': [bands] * 10, 'preamp_db': preamp}

    def test_valid_kept_and_normalized(self):
        raw = {'Loud': self._valid(bands=20.0, preamp=-30.0)}
        out = aus.normalize({'eq_user_presets': raw})['eq_user_presets']
        self.assertEqual(out, {'Loud': {'bands_db': [12.0] * 10, 'preamp_db': -12.0}})

    def test_invalid_entries_dropped(self):
        raw = {
            '': self._valid(),               # empty name
            'x' * 65: self._valid(),         # name too long
            7: self._valid(),                # non-str name
            'BadCount': {'bands_db': [0.0] * 9, 'preamp_db': 0.0},
            'NotDict': 'nope',
            'NoPreamp': {'bands_db': [0.0] * 10},
            'BadPreamp': {'bands_db': [0.0] * 10, 'preamp_db': '1'},
        }
        self.assertEqual(aus.normalize({'eq_user_presets': raw})['eq_user_presets'], {})

    def test_limit_of_100(self):
        raw = {f'p{i:03d}': self._valid() for i in range(105)}
        raw['bogus'] = 'junk'
        out = aus.normalize({'eq_user_presets': raw})['eq_user_presets']
        self.assertEqual(len(out), 100)
        self.assertEqual(list(out), [f'p{i:03d}' for i in range(100)])
        self.assertNotIn('bogus', out)


class TestLoadSave(TmpMixin, unittest.TestCase):
    def test_missing_file_gives_defaults(self):
        self.assertEqual(aus.load(self.path), dict(aus.DEFAULTS))

    def test_corrupt_file_gives_defaults(self):
        with open(self.path, 'w') as f:
            f.write('{{bad')
        self.assertEqual(aus.load(self.path), dict(aus.DEFAULTS))

    def test_save_load_round_trip(self):
        settings = {
            'transition_mode': 'crossfade',
            'transition_seconds': 8.5,
            'eq_enabled': True,
            'eq_preset': 'Rock',
            'eq_bands_db': [3.0] * 10,
            'eq_preamp_db': -2.0,
            'eq_user_presets': {'Mine': {'bands_db': [1.0] * 10, 'preamp_db': 0.5}},
            'leveling_enabled': True,
            'junk': 'dropped',
        }
        aus.save(self.path, settings)
        loaded = aus.load(self.path)
        self.assertEqual(loaded['transition_mode'], 'crossfade')
        self.assertEqual(loaded['transition_seconds'], 9)
        self.assertTrue(loaded['eq_enabled'])
        self.assertEqual(loaded['eq_preset'], 'Rock')
        self.assertEqual(loaded['eq_bands_db'], [3.0] * 10)
        self.assertEqual(loaded['eq_preamp_db'], -2.0)
        self.assertEqual(loaded['eq_user_presets'],
                         {'Mine': {'bands_db': [1.0] * 10, 'preamp_db': 0.5}})
        self.assertTrue(loaded['leveling_enabled'])
        self.assertNotIn('junk', loaded)

    def test_save_writes_indent2_json_and_no_tmp(self):
        aus.save(self.path, {})
        with open(self.path, 'r', encoding='utf-8') as f:
            text = f.read()
        self.assertEqual(json.loads(text), dict(aus.DEFAULTS))
        self.assertIn('\n  "transition_mode"', text)
        self.assertEqual(os.listdir(self._dir), ['audio_settings.json'])


if __name__ == '__main__':
    unittest.main()
