"""
tests/test_eq_handcheck.py - hand-worked checks for eq.py

Run with:
    python -m unittest tests/test_eq_handcheck.py -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from eq import *  # noqa: F401,F403


class EqHandcheck(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual([eqf_value_to_db(v) for v in (33, 64, 1, 2, 45, 60, 20)], [0.0, 12.0, -12.0, -12.0, 4.6, 10.5, -5.0])
        self.assertEqual([db_to_eqf_value(d) for d in (0, 12, -12, 99, -99)], [33, 64, 2, 64, 1])

    def test_presets(self):
        self.assertEqual(len(BUILTIN_PRESETS), 18)
        self.assertEqual(BUILTIN_PRESETS['Flat'], ([0.0] * 10, 0.0))
        self.assertEqual(BUILTIN_PRESETS['Rock'], ([4.6, 2.7, -3.9, -5.4, -2.7, 2.3, 5.4, 6.6, 6.6, 6.6], 0.0))
        self.assertEqual(BUILTIN_PRESETS['Full Treble'][0][9], 10.5)

    def test_eqf_round_trip(self):
        raw = EQF_HEADER + b'Test' + b'\x00' * 253 + bytes([0] * 10 + [31])
        self.assertEqual(len(raw), 299)
        self.assertEqual(parse_eqf(raw), [('Test', [12.0] * 10, 0.0)])
        self.assertEqual(build_eqf([('Test', [12.0] * 10, 0.0)]), raw)
        self.assertEqual(len(parse_eqf(raw + raw[31:])), 2)
        with self.assertRaises(ValueError):
            parse_eqf(b'Winamp EQ library file v1.0' + raw[27:])
        with self.assertRaises(ValueError):
            parse_eqf(raw[:-1])

    def test_non_finite_and_bad_lengths_are_safe(self):
        nan, inf = float('nan'), float('inf')
        self.assertEqual(filter_chain([nan, inf] + [0] * 8, inf, nan), '')
        self.assertEqual(filter_chain([20] + [0] * 9, 0, 0), 'lowshelf=f=60:g=12.0')
        self.assertEqual(filter_chain([3, 0, 0], 0, 0), 'lowshelf=f=60:g=3.0')
        self.assertEqual(filter_chain([0] * 10 + [5], 0, 0), '')
        self.assertEqual(db_to_eqf_value(nan), 33)
        self.assertEqual(len(build_eqf([('x', [nan] * 10, inf)])), 299)

    def test_filter_chain(self):
        b = [3, 0, 0, 0, 0, 0, 0, 0, 0, -2]
        self.assertEqual(filter_chain(b, -3, -5.5), 'volume=-8.50dB,lowshelf=f=60:g=3.0,highshelf=f=16000:g=-2.0')
        self.assertEqual(filter_chain(b, -3, -5.5, enabled=False), 'volume=-5.50dB')
        self.assertEqual(filter_chain([0, 4] + [0] * 8, 0, 1.0), 'volume=1.00dB,equalizer=f=170:t=q:w=1:g=4.0')
        self.assertEqual(filter_chain([0] * 10, 0, 0), '')


if __name__ == '__main__':
    unittest.main()
