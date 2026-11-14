"""
tests/test_listening_stats_handcheck.py - hand-worked checks for listening_stats.py core (hidden from the worker).

Run with:
    python -m unittest tests.test_listening_stats_handcheck -v
"""

import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import listening_stats as ls


def ts(*a):
    return datetime(*a).timestamp()


A = ls.Place('art a', 'Art A', 'alb1', 'Album 1')
B = ls.Place('art b', 'Art B', 'alb2', 'Album 2')
PLACES = {'/a1': A, '/a2': A, '/b1': B}
locate = PLACES.get


def rec(path, start, heard, q, skip=False, ctx=None, title='', artist='', album=''):
    r = {'v': 1, 'start': int(start), 'end': int(start + heard), 'path': path, 'title': title, 'artist': artist,
         'album': album, 'genre': '', 'dur': 200.0, 'heard': float(heard), 'reason': 'natural_end', 'q': q,
         'skip': skip, 'tr': 'cut'}
    if ctx:
        r['ctx'] = ctx
    return r


RECS = [
    rec('/a1', ts(2026, 11, 18, 10), 3600, True, title='A1 rec'),
    rec('/a1', ts(2026, 11, 18, 11), 1800, True, ctx='album', title='A1 rec'),
    rec('/a2', ts(2026, 11, 18, 22), 3600, True, title='A2 rec'),
    rec('/b1', ts(2026, 11, 19, 8), 60, False, skip=True, title='B1 rec'),
    rec('/b1', ts(2026, 11, 19, 9), 60, False, skip=True, title='B1 rec'),
    rec('/b1', ts(2026, 11, 19, 10), 7200, True, title='B1 rec'),
    rec('/a1', ts(2026, 10, 30, 12), 1800, True, title='A1 rec'),
    rec('/gone', ts(2026, 11, 17, 9), 900, True, title='Gone Song', artist=' Old Band ', album=''),
]
TRACKS = {
    '/a1': ls.TrackInfo('/a1', 'A One', A, date_added=int(ts(2026, 11, 17))),
    '/a2': ls.TrackInfo('/a2', 'A Two', A, date_added=int(ts(2026, 10, 1))),
    '/b1': ls.TrackInfo('/b1', 'B One', B),
}
WEEK = (ts(2026, 11, 16), ts(2026, 11, 23))


class BoundsHandcheck(unittest.TestCase):
    def test_bounds(self):
        now = ts(2026, 11, 18, 15, 30)
        self.assertEqual(ls.period_bounds('week', now), WEEK)
        self.assertEqual(ls.period_bounds('week', ts(2026, 11, 16)), WEEK)
        self.assertEqual(ls.period_bounds('month', now), (ts(2026, 11, 1), ts(2026, 12, 1)))
        self.assertEqual(ls.month_bounds(2026, 12), (ts(2026, 12, 1), ts(2027, 1, 1)))
        self.assertEqual(ls.period_bounds('year', now), (ts(2026, 1, 1), ts(2027, 1, 1)))
        self.assertEqual(ls.period_bounds('all', now), (None, None))
        with self.assertRaises(ValueError):
            ls.period_bounds('decade', now)


class PeriodHandcheck(unittest.TestCase):
    def setUp(self):
        self.s = ls.period_stats(RECS, WEEK[0], WEEK[1], locate, TRACKS)

    def test_tops(self):
        s = self.s
        self.assertEqual([r.key for r in s.top_songs], ['/a1', '/b1', '/a2', '/gone'])
        a1 = s.top_songs[0]
        self.assertEqual((a1.name, a1.sub, a1.plays, a1.starts, a1.skips), ('A One', 'Art A', 2, 2, 0))
        self.assertAlmostEqual(a1.hours, 1.5)
        gone = s.top_songs[3]
        self.assertEqual((gone.name, gone.sub), ('Gone Song', 'Old Band'))
        self.assertEqual([r.name for r in s.top_artists], ['Art A', 'Art B', 'Old Band'])
        self.assertEqual(s.top_artists[0].plays, 3)
        self.assertAlmostEqual(s.top_artists[0].hours, 2.5)
        self.assertEqual([r.key for r in s.top_albums], ['alb1', 'alb2', 'tag|old band|(no album)'])
        self.assertEqual(s.top_albums[2].sub, 'Old Band')
        self.assertEqual(len(ls.period_stats(RECS, WEEK[0], WEEK[1], locate, TRACKS, top_n=2).top_songs), 2)

    def test_skips(self):
        self.assertEqual([(r.key, r.starts, r.skips) for r in self.s.skipped_songs], [('/b1', 3, 2)])
        self.assertEqual(self.s.skipped_artists, [])

    def test_clock(self):
        h, w = self.s.by_hour, self.s.by_weekday
        self.assertEqual((len(h), len(w)), (24, 7))
        self.assertAlmostEqual(h[10], 3.0)
        self.assertAlmostEqual(h[11], 0.5)
        self.assertAlmostEqual(h[9], 960 / 3600)
        self.assertAlmostEqual(h[12], 0.0)
        self.assertAlmostEqual(w[1], 0.25)
        self.assertAlmostEqual(w[2], 2.5)
        self.assertAlmostEqual(w[3], 7320 / 3600)
        self.assertAlmostEqual(w[0], 0.0)

    def test_summary(self):
        m = self.s.summary
        total = (3600 + 1800 + 3600 + 7320 + 900) / 3600
        self.assertAlmostEqual(m.hours, total)
        self.assertEqual((m.plays, m.songs, m.artists, m.new_songs), (5, 4, 3, 1))
        self.assertAlmostEqual(m.shuffle_share, (total - 0.5) / total)

    def test_all_and_empty(self):
        s = ls.period_stats(RECS, None, None, locate, TRACKS)
        self.assertEqual(s.summary.plays, 6)
        self.assertEqual(s.summary.new_songs, 1)        # a2 was added before the first record
        e = ls.period_stats([], None, None, locate, TRACKS)
        self.assertEqual((e.summary.hours, e.summary.plays, e.summary.new_songs, e.summary.shuffle_share),
                         (0, 0, 0, None))
        self.assertEqual((e.top_songs, e.top_artists, e.skipped_songs), ([], [], []))
        self.assertEqual(sum(e.by_hour), 0)


class MonthsHandcheck(unittest.TestCase):
    def test_months(self):
        rows = ls.months(RECS, locate)
        self.assertEqual([(r.year, r.month) for r in rows], [(2026, 11), (2026, 10)])
        nov, oct_ = rows
        self.assertEqual((nov.plays, nov.top_artist, nov.top_song), (5, 'Art A', 'A1 rec'))
        self.assertAlmostEqual(oct_.hours, 0.5)
        self.assertEqual((oct_.plays, oct_.top_artist, oct_.top_song), (1, 'Art A', 'A1 rec'))
        self.assertEqual(ls.months([], locate), [])


if __name__ == '__main__':
    unittest.main()
