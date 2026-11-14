"""
tests/test_listening_stats_b_handcheck.py - hand-worked checks for listening_stats all time and library lists
(hidden from the worker).

Run with:
    python -m unittest tests.test_listening_stats_b_handcheck -v
"""

import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import listening_stats as ls


def ts(*a):
    return datetime(*a).timestamp()


NOW = ts(2026, 11, 18, 12)
DAY = 86400
A = ls.Place('art a', 'Art A', 'alb1', 'Album 1')
B = ls.Place('art b', 'Art B', 'alb2', 'Album 2')
C = ls.Place('art c', 'Art C', 'alb3', 'Album 3')
T = [
    ls.TrackInfo('/a1', 'A1', A, duration=200, play_count=18, qualified_play_count=3, manual_skip_count=0,
                 last_played=int(NOW - 10 * DAY), loved=True),
    ls.TrackInfo('/a2', 'A2', A, duration=100, play_count=36, qualified_play_count=1, manual_skip_count=3,
                 last_played=int(NOW - 100 * DAY), loved=True),
    ls.TrackInfo('/b1', 'B1', B, duration=300, play_count=0, loved=True, date_added=int(ts(2026, 5, 1))),
    ls.TrackInfo('/b2', 'B2', B, duration=None, play_count=None, qualified_play_count=None, manual_skip_count=None,
                 date_added=int(ts(2026, 9, 1))),
    ls.TrackInfo('/c1', 'C1', C, duration=240, play_count=6, qualified_play_count=0, manual_skip_count=4,
                 last_played=int(NOW - 200 * DAY)),
]
PLACES = {t.path: t.place for t in T}


def rec(path, start, heard):
    return {'v': 1, 'start': int(start), 'end': int(start + heard), 'path': path, 'title': '', 'artist': '',
            'album': '', 'genre': '', 'dur': 200.0, 'heard': float(heard), 'reason': 'natural_end', 'q': True,
            'skip': False, 'tr': 'cut'}


RECS = [rec('/a1', ts(2026, 11, 17, 9), 1800), rec('/b2', ts(2026, 11, 17, 21), 600)]


class AllTimeHandcheck(unittest.TestCase):
    def test_all_time(self):
        s = ls.all_time_stats(RECS, T, PLACES.get)
        self.assertEqual([r.key for r in s.top_songs], ['/a2', '/a1', '/c1'])
        self.assertEqual((s.top_songs[0].plays, s.top_songs[0].starts, s.top_songs[0].skips), (36, 4, 3))
        self.assertAlmostEqual(s.top_songs[0].hours, 1.0)
        self.assertEqual([r.key for r in s.top_artists], ['art a', 'art c'])
        self.assertEqual(s.top_artists[0].plays, 54)
        self.assertAlmostEqual(s.top_artists[0].hours, 2.0)
        self.assertEqual([r.key for r in s.top_albums], ['alb1', 'alb3'])
        self.assertEqual([r.key for r in s.skipped_songs], ['/c1', '/a2'])
        self.assertEqual([(r.key, r.starts, r.skips) for r in s.skipped_artists], [('art a', 7, 3)])
        base = ls.period_stats(RECS, None, None, PLACES.get, {t.path: t for t in T})
        self.assertEqual(s.summary, base.summary)
        self.assertEqual(s.by_hour, base.by_hour)
        self.assertEqual(s.by_weekday, base.by_weekday)
        self.assertEqual(len(ls.all_time_stats(RECS, T, PLACES.get, top_n=1).top_songs), 1)


class LibraryHandcheck(unittest.TestCase):
    def test_forgotten(self):
        self.assertEqual([t.path for t in ls.forgotten_favorites(T, NOW)], ['/b1', '/a2'])
        self.assertEqual([t.path for t in ls.forgotten_favorites(T, NOW, days=5)], ['/b1', '/a2', '/a1'])
        self.assertEqual([t.path for t in ls.forgotten_favorites(T, NOW, limit=1)], ['/b1'])

    def test_never_played(self):
        self.assertEqual([t.path for t in ls.never_played(T, [])], ['/b2', '/b1'])
        self.assertEqual([t.path for t in ls.never_played(T, RECS)], ['/b1'])


if __name__ == '__main__':
    unittest.main()
