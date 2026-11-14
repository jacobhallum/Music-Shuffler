"""
tests/test_album_gain_handcheck.py - hand-worked checks for loudness.album_gain_db and the log ctx field
(hidden from the worker).

Run with:
    python -m unittest tests.test_album_gain_handcheck -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import listening_log
from loudness import TrackLoudness, album_gain_db


def L(i, p=-6.0, d=200.0):
    return TrackLoudness(i, p, d, (), ())


class AlbumGainHandcheck(unittest.TestCase):
    def test_values(self):
        self.assertEqual(album_gain_db([L(-9.0), L(-9.0)]), -5.0)
        # energy mean of -10 and -20 dB for equal time = 10*log10((0.1 + 0.01)/2) = -12.596
        self.assertEqual(album_gain_db([L(-10.0, -8.0, 100), L(-20.0, -8.0, 100)]), -1.4)
        # 300 s at -10, 100 s at -20: 10*log10((3*0.1 + 0.01)/4) = -11.1095 -> -2.9
        self.assertEqual(album_gain_db([L(-10.0, -8.0, 300), L(-20.0, -8.0, 100)]), -2.9)
        # quiet album wants +6 but the loudest peak (-2.5) only allows +1.5
        self.assertEqual(album_gain_db([L(-20.0, -9.0), L(-20.0, -2.5)]), 1.5)
        # silence and zero-length songs do not count
        # (their 0.0 dBTP peaks would cap the gain at -1.0 if they counted)
        self.assertEqual(album_gain_db([L(-20.0, -9.0), L(-70.0, 0.0), L(-80.0, 0.0), L(-30.0, 0.0, 0)]), 6.0)
        self.assertEqual(album_gain_db([]), 0.0)
        self.assertEqual(album_gain_db([L(-70.0)]), 0.0)
        self.assertEqual(album_gain_db(iter([L(-16.0, -9.0)]), target=-18.0), -2.0)


class CtxHandcheck(unittest.TestCase):
    BASE = dict(started_at=1, ended_at=2, path='a.mp3', title='t', artist='a', album='b', genre='',
                duration_sec=10, audible_sec=10, reason='natural_end', qualified=True, skip=False,
                transition='cut')

    def test_ctx(self):
        self.assertNotIn('ctx', listening_log.make_record(**self.BASE))
        rec = listening_log.make_record(**self.BASE, ctx='artist')
        self.assertEqual(rec['ctx'], 'artist')
        self.assertEqual(list(rec)[-1], 'ctx')
        with self.assertRaises(ValueError):
            listening_log.make_record(**self.BASE, ctx='shuffle')
        self.assertEqual(listening_log.VERSION, 1)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'log.jsonl')
            listening_log.append(p, rec)
            self.assertEqual(listening_log.read(p)[0]['ctx'], 'artist')


if __name__ == '__main__':
    unittest.main()
