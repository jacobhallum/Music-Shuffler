"""
tests/test_loudness_handcheck.py - hand-worked checks for loudness.py

Run with:
    python -m unittest tests/test_loudness_handcheck.py -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from loudness import *  # noqa: F401,F403

FRAME = '[Parsed_ebur128_0 @ 0000] t: {t:<10} TARGET:-23 LUFS    M:{m:>6} S:-20.0     I: -20.0 LUFS       LRA:   0.0 LU  FTPK:  -4.0  -4.5 dBFS  TPK:  -4.0  -4.5 dBFS'
SUMMARY = ('[Parsed_ebur128_0 @ 0000] Summary:\n\n  Integrated loudness:\n    I:         {i} LUFS\n    Threshold: -23.6 LUFS\n\n'
           '  Loudness range:\n    LRA:         5.3 LU\n    Threshold: -33.6 LUFS\n    LRA low:   -18.0 LUFS\n    LRA high:  -12.7 LUFS\n\n'
           '  True peak:\n    Peak:       {p} dBFS\n')


def stderr(ms, i='-13.6', p='-1.9'):
    lines = [FRAME.format(t=f'{(k + 1) * 0.1 - 0.0000227:.6f}', m=m) for k, m in enumerate(ms)]
    return '\n'.join(lines) + '\n' + SUMMARY.format(i=i, p=p)


class LoudnessHandcheck(unittest.TestCase):
    def test_parse(self):
        t = parse_ebur128(stderr(['-120.7'] * 3 + ['-14.2'] * 637 + ['nan'] * 10))
        self.assertEqual((t.integrated_lufs, t.true_peak_dbtp), (-13.6, -1.9))
        self.assertAlmostEqual(t.duration_sec, 65.0, places=3)
        self.assertEqual((len(t.head), len(t.tail)), (300, 300))
        self.assertEqual((t.head[0], t.head[3], t.tail[-1], t.tail[-11]), (-70.0, -14.2, -70.0, -14.2))

    def test_short_and_silent(self):
        t = parse_ebur128(stderr(['-20.0'] * 50, i='-inf', p='-inf'))
        self.assertEqual(t.head, t.tail)
        self.assertEqual(len(t.head), 50)
        self.assertEqual((t.integrated_lufs, t.true_peak_dbtp), (-70.0, -120.0))
        with self.assertRaises(ValueError):
            parse_ebur128(FRAME.format(t='0.1', m='-20.0'))

    def test_gain(self):
        cases = [(-19.6, -3.0), (-9.4, -0.5), (-13.6, -1.9), (-75.0, -120.0), (-20.0, -10.0)]
        self.assertEqual([leveling_gain_db(*a) for a in cases], [2.0, -4.6, -0.4, 0.0, 6.0])

    def test_records_and_cache(self):
        t = TrackLoudness(-13.6, -1.9, 65.04, (-70.0, -14.2), (-14.2, -70.0))
        back = from_record(to_record(t))
        self.assertEqual((back.integrated_lufs, back.duration_sec, back.head), (-13.6, 65.0, (-70.0, -14.2)))
        self.assertIsNone(from_record({'i': 'x'}))
        p = '/music/A B/x.mp3'
        self.assertEqual(cache_key(p, 123, 456.9), os.path.normcase(os.path.normpath(p)) + '|123|456')
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, 'loudness_cache.json')
            self.assertEqual(load_cache(f), {})
            save_cache(f, {'k': to_record(t)})
            self.assertEqual(load_cache(f), {'k': to_record(t)})
            with open(f, 'w') as fh:
                fh.write('[1,2')
            self.assertEqual(load_cache(f), {})


if __name__ == '__main__':
    unittest.main()
