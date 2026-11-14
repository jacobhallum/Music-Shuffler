"""
tests/test_listening_stats.py - stdlib-only unit tests for listening_stats.py

Run with:
    python -m unittest tests/test_listening_stats.py -v
"""

import os
import sys
import unittest
from datetime import datetime

# Allow running from project root or tests/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import listening_stats
from listening_stats import (
    NO_ALBUM, UNKNOWN_ARTIST, Place, Row, TrackInfo,
    month_bounds, period_bounds, place_for_record, period_stats, months,
)


def ts(dt):
    """Timestamp of a naive local datetime, so tests are timezone-independent."""
    return dt.timestamp()


def rec(path, title='', artist='', album='', start=0, heard=0.0,
        q=False, skip=False, ctx=None):
    r = {
        'v': 1,
        'start': int(start),
        'end': int(start) + int(heard) or int(start) + 1,
        'path': path,
        'title': title,
        'artist': artist,
        'album': album,
        'genre': '',
        'dur': 0.0,
        'heard': heard,
        'reason': 'natural_end',
        'q': bool(q),
        'skip': bool(skip),
        'tr': 'gapless',
    }
    if ctx is not None:
        r['ctx'] = ctx
    return r


def make_locate(mapping):
    return lambda p: mapping.get(p)


PLACE_A = Place('artist one', 'Artist One', 'al-a', 'Album A')
PLACE_B = Place('artist two', 'Artist Two', 'al-b', 'Album B')
LOCATE = make_locate({'/a.mp3': PLACE_A, '/b.mp3': PLACE_B, '/c.mp3': PLACE_B})


def info(path, title, date_added=None):
    return TrackInfo(path=path, title=title, place=PLACE_A, date_added=date_added)


class PeriodBoundsTests(unittest.TestCase):
    def test_week_wednesday_back_to_monday(self):
        # 2026-11-11 is a Wednesday (weekday() == 2).
        start, end = period_bounds('week', ts(datetime(2026, 11, 11, 15, 30)))
        self.assertEqual(start, ts(datetime(2026, 11, 9)))      # that Monday 00:00
        self.assertEqual(end, start + 7 * 24 * 3600)

    def test_month_november(self):
        start, end = period_bounds('month', ts(datetime(2026, 11, 20)))
        self.assertEqual(start, ts(datetime(2026, 11, 1)))
        self.assertEqual(end, ts(datetime(2026, 12, 1)))

    def test_month_december_wraps_to_next_year(self):
        start, end = period_bounds('month', ts(datetime(2026, 12, 15)))
        self.assertEqual(start, ts(datetime(2026, 12, 1)))
        self.assertEqual(end, ts(datetime(2027, 1, 1)))

    def test_month_bounds_december(self):
        self.assertEqual(
            month_bounds(2026, 12),
            (ts(datetime(2026, 12, 1)), ts(datetime(2027, 1, 1))))

    def test_year(self):
        start, end = period_bounds('year', ts(datetime(2026, 6, 1)))
        self.assertEqual(start, ts(datetime(2026, 1, 1)))
        self.assertEqual(end, ts(datetime(2027, 1, 1)))

    def test_all(self):
        self.assertEqual(period_bounds('all', ts(datetime(2026, 6, 1))), (None, None))

    def test_bad_kind_raises(self):
        with self.assertRaises(ValueError):
            period_bounds('decade', ts(datetime(2026, 6, 1)))


class PlaceForRecordTests(unittest.TestCase):
    def test_located_path_wins(self):
        got = place_for_record(rec('/a.mp3', artist='Ignored', album='Ignored'), LOCATE)
        self.assertEqual(got, PLACE_A)

    def test_unknown_path_falls_back_to_tags(self):
        got = place_for_record(rec('/x.mp3', artist='  Bob ', album='Album X'), LOCATE)
        self.assertEqual(got, Place('bob', 'Bob', 'tag|bob|album x', 'Album X'))

    def test_empty_tags_give_unknown(self):
        got = place_for_record(rec('/x.mp3', artist='', album='  '), LOCATE)
        self.assertEqual(got.artist_name, UNKNOWN_ARTIST)
        self.assertEqual(got.album_name, NO_ALBUM)
        self.assertEqual(got.artist_key, 'unknown artist')
        self.assertEqual(got.album_key, 'tag|unknown artist|(no album)')


class TopListsTests(unittest.TestCase):
    def test_song_plays_hours_starts_skips(self):
        records = [
            rec('/a.mp3', title='Song A', artist='Artist One', album='Album A',
                heard=3600, q=True),
            rec('/a.mp3', title='Song A', artist='Artist One', album='Album A',
                heard=1800, skip=True),
        ]
        stats = period_stats(records, None, None, LOCATE, {})
        song = stats.top_songs[0]
        self.assertEqual(song.key, '/a.mp3')
        self.assertEqual(song.name, 'Song A')
        self.assertEqual(song.sub, 'Artist One')
        self.assertEqual(song.plays, 1)
        self.assertEqual(song.starts, 2)
        self.assertEqual(song.skips, 1)
        self.assertAlmostEqual(song.hours, 1.5)

    def test_top_songs_tie_on_plays_broken_by_hours(self):
        records = [
            rec('/a.mp3', title='AAA', heard=3600, q=True),   # 1 play, 1.0 h
            rec('/b.mp3', title='BBB', heard=7200, q=True),   # 1 play, 2.0 h
        ]
        stats = period_stats(records, None, None, LOCATE, {})
        self.assertEqual([r.key for r in stats.top_songs], ['/b.mp3', '/a.mp3'])

    def test_top_artists_ordered_by_hours(self):
        records = [
            rec('/b.mp3', title='B1', heard=1800, q=True),
            rec('/c.mp3', title='B2', heard=1800, q=True),   # Artist Two: 2 plays, 1 h
            rec('/a.mp3', title='A1', heard=7200, q=True),   # Artist One: 1 play, 2 h
        ]
        stats = period_stats(records, None, None, LOCATE, {})
        self.assertEqual([r.name for r in stats.top_artists], ['Artist One', 'Artist Two'])
        self.assertEqual(stats.top_artists[0].sub, '')

    def test_albums_grouped_by_album_key(self):
        # Two different paths of the same located album collapse to one album row.
        records = [
            rec('/a.mp3', heard=600, q=True),
            rec('/b.mp3', heard=1200, q=True),
        ]
        stats = period_stats(records, None, None, LOCATE, {})
        self.assertEqual(len(stats.top_albums), 2)
        album = stats.top_albums[0]
        self.assertEqual(album.key, 'al-b')
        self.assertEqual(album.name, 'Album B')
        self.assertEqual(album.sub, 'Artist Two')
        self.assertEqual(album.plays, 1)

    def test_tag_fallback_groups_same_album_from_two_paths(self):
        records = [
            rec('/x.mp3', title='X', artist='Bob', album='Same', heard=600, q=True),
            rec('/y.mp3', title='Y', artist='Bob', album='Same', heard=600, q=True),
        ]
        stats = period_stats(records, None, None, LOCATE, {})
        self.assertEqual(len(stats.top_albums), 1)
        self.assertEqual(stats.top_albums[0].plays, 2)

    def test_top_n_limits(self):
        records = [rec(f'/s{i}.mp3', title=f'S{i}', artist=f'Artist {i}',
                       heard=60, q=True) for i in range(5)]
        stats = period_stats(records, None, None, LOCATE, {}, top_n=3)
        self.assertEqual(len(stats.top_songs), 3)
        self.assertEqual(len(stats.top_artists), 3)


class SkippedTests(unittest.TestCase):
    def test_song_skip_threshold(self):
        records = [
            # 2 starts, 1 skip -> excluded
            rec('/low.mp3', title='Low', heard=10, skip=True),
            rec('/low.mp3', title='Low', heard=10, q=True),
            # 3 starts, 1 skip -> included
            rec('/ok.mp3', title='Ok', heard=10, skip=True),
            rec('/ok.mp3', title='Ok', heard=10, q=True),
            rec('/ok.mp3', title='Ok', heard=10, q=True),
        ]
        stats = period_stats(records, None, None, LOCATE, {})
        self.assertEqual([r.name for r in stats.skipped_songs], ['Ok'])

    def test_skip_rate_ordering(self):
        records = [
            # rate 1/3
            rec('/r1.mp3', title='R1', heard=10, skip=True),
            rec('/r1.mp3', title='R1', heard=10, q=True),
            rec('/r1.mp3', title='R1', heard=10, q=True),
            # rate 2/3
            rec('/r2.mp3', title='R2', heard=10, skip=True),
            rec('/r2.mp3', title='R2', heard=10, skip=True),
            rec('/r2.mp3', title='R2', heard=10, q=True),
        ]
        stats = period_stats(records, None, None, LOCATE, {})
        self.assertEqual([r.name for r in stats.skipped_songs], ['R2', 'R1'])

    def test_artist_skip_needs_five_starts(self):
        records = [
            # Artist One: 4 starts (2 per path), 2 skips -> excluded
            rec('/a.mp3', heard=10, skip=True),
            rec('/a.mp3', heard=10, q=True),
            rec('/b.mp3', heard=10, skip=True),
            rec('/b.mp3', heard=10, q=True),
            # Artist Two path: 5 starts, 1 skip -> included
            rec('/c.mp3', heard=10, skip=True),
            rec('/c.mp3', heard=10, q=True),
            rec('/c.mp3', heard=10, q=True),
            rec('/c.mp3', heard=10, q=True),
            rec('/c.mp3', heard=10, q=True),
        ]
        stats = period_stats(records, None, None, LOCATE, {})
        self.assertEqual([r.name for r in stats.skipped_artists], ['Artist Two'])


class PeriodFilterTests(unittest.TestCase):
    def test_out_of_period_records_ignored(self):
        start = ts(datetime(2026, 1, 1))
        end = ts(datetime(2026, 2, 1))
        records = [
            rec('/a.mp3', title='In', heard=3600, q=True, start=ts(datetime(2026, 1, 15))),
            rec('/b.mp3', title='Before', heard=3600, q=True, start=ts(datetime(2025, 12, 31))),
            rec('/c.mp3', title='After', heard=3600, q=True, start=ts(datetime(2026, 2, 1))),
        ]
        stats = period_stats(records, start, end, LOCATE, {})
        self.assertAlmostEqual(stats.summary.hours, 1.0)
        self.assertEqual(stats.summary.plays, 1)
        self.assertEqual([r.name for r in stats.top_songs], ['In'])

    def test_end_none_open(self):
        start = ts(datetime(2026, 1, 1))
        records = [
            rec('/a.mp3', title='In', heard=60, q=True, start=ts(datetime(2027, 5, 5))),
        ]
        stats = period_stats(records, start, None, LOCATE, {})
        self.assertEqual(stats.summary.plays, 1)

    def test_start_none_open(self):
        end = ts(datetime(2026, 1, 1))
        records = [
            rec('/a.mp3', title='In', heard=60, q=True, start=ts(datetime(2020, 5, 5))),
        ]
        stats = period_stats(records, None, end, LOCATE, {})
        self.assertEqual(stats.summary.plays, 1)


class BucketsTests(unittest.TestCase):
    def test_by_hour_and_weekday(self):
        # 2026-11-11 is a Wednesday (weekday 2); 2026-11-14 is a Saturday (5).
        records = [
            rec('/a.mp3', heard=3600, start=ts(datetime(2026, 11, 11, 9, 0))),
            rec('/b.mp3', heard=1800, start=ts(datetime(2026, 11, 14, 9, 0))),
            rec('/c.mp3', heard=600, start=ts(datetime(2026, 11, 14, 22, 0))),
        ]
        stats = period_stats(records, None, None, LOCATE, {})
        self.assertAlmostEqual(stats.by_hour[9], 1.5)
        self.assertAlmostEqual(stats.by_hour[22], 600 / 3600)
        self.assertAlmostEqual(sum(stats.by_hour), (3600 + 1800 + 600) / 3600)
        self.assertAlmostEqual(stats.by_weekday[2], 1.0)
        self.assertAlmostEqual(stats.by_weekday[5], 0.5 + 600 / 3600)
        self.assertAlmostEqual(sum(stats.by_weekday), (3600 + 1800 + 600) / 3600)


class SummaryTests(unittest.TestCase):
    def test_songs_artists_count_only_played(self):
        records = [
            rec('/a.mp3', heard=60, q=True),        # played -> counted
            rec('/a.mp3', heard=60, skip=True),     # same path again
            rec('/b.mp3', heard=60, skip=True),     # started but not played
            rec('/c.mp3', heard=60, q=True),        # Artist Two played
        ]
        stats = period_stats(records, None, None, LOCATE, {})
        self.assertEqual(stats.summary.songs, 2)
        self.assertEqual(stats.summary.artists, 2)
        self.assertEqual(stats.summary.plays, 2)

    def test_new_songs_inside_and_outside_period(self):
        start = ts(datetime(2026, 1, 1))
        end = ts(datetime(2026, 2, 1))
        tracks = {
            '/new': info('/new', 'New', date_added=ts(datetime(2026, 1, 15))),
            '/old': info('/old', 'Old', date_added=ts(datetime(2025, 6, 1))),
            '/none': info('/none', 'None', date_added=None),
            '/edge': info('/edge', 'Edge', date_added=ts(datetime(2026, 2, 1))),  # == end
        }
        records = [
            rec('/new', heard=60, q=True, start=ts(datetime(2026, 1, 20))),
            rec('/old', heard=60, q=True, start=ts(datetime(2026, 1, 20))),
            rec('/none', heard=60, q=True, start=ts(datetime(2026, 1, 20))),
            rec('/edge', heard=60, q=True, start=ts(datetime(2026, 1, 20))),
        ]
        stats = period_stats(records, start, end, LOCATE, tracks)
        self.assertEqual(stats.summary.new_songs, 1)

    def test_shuffle_share_with_ctx(self):
        records = [
            rec('/a.mp3', heard=3600, q=True, start=ts(datetime(2026, 1, 1))),
            rec('/b.mp3', heard=3600, q=True, ctx='album', start=ts(datetime(2026, 1, 2))),
        ]
        stats = period_stats(records, None, None, LOCATE, {})
        self.assertAlmostEqual(stats.summary.hours, 2.0)
        self.assertAlmostEqual(stats.summary.shuffle_share, 0.5)

    def test_empty_records_all_zero(self):
        stats = period_stats([], ts(datetime(2026, 1, 1)), ts(datetime(2026, 2, 1)), LOCATE, {})
        self.assertEqual(stats.summary.hours, 0.0)
        self.assertEqual(stats.summary.plays, 0)
        self.assertEqual(stats.summary.songs, 0)
        self.assertEqual(stats.summary.artists, 0)
        self.assertEqual(stats.summary.new_songs, 0)
        self.assertIsNone(stats.summary.shuffle_share)
        self.assertEqual(stats.top_songs, [])
        self.assertEqual(stats.top_artists, [])
        self.assertEqual(stats.top_albums, [])
        self.assertEqual(stats.skipped_songs, [])
        self.assertEqual(stats.skipped_artists, [])
        self.assertEqual(stats.by_hour, [0.0] * 24)
        self.assertEqual(stats.by_weekday, [0.0] * 7)


class MonthsTests(unittest.TestCase):
    def test_two_months_newest_first_with_names(self):
        records = [
            # November 2026: Artist One's song A plays most (2 h)
            rec('/a.mp3', title='Song A', artist='Artist One', album='Album A',
                heard=7200, q=True, start=ts(datetime(2026, 11, 5, 10, 0))),
            rec('/b.mp3', title='Song B', artist='Artist Two', album='Album B',
                heard=1800, q=True, start=ts(datetime(2026, 11, 6, 10, 0))),
            # October 2026: Artist Two
            rec('/c.mp3', title='Song C', artist='Artist Two', album='Album B',
                heard=3600, q=True, start=ts(datetime(2026, 10, 5, 10, 0))),
        ]
        rows = months(records, LOCATE)
        self.assertEqual(len(rows), 2)
        first, second = rows
        self.assertEqual((first.year, first.month), (2026, 11))
        self.assertAlmostEqual(first.hours, 2.5)
        self.assertEqual(first.plays, 2)
        self.assertEqual(first.top_artist, 'Artist One')
        self.assertEqual(first.top_song, 'Song A')
        self.assertEqual((second.year, second.month), (2026, 10))
        self.assertEqual(second.top_artist, 'Artist Two')
        self.assertEqual(second.top_song, 'Song C')

    def test_month_without_plays_has_empty_names(self):
        records = [
            # started but never qualified -> no top song; 60 s heard still makes Bob the top artist
            rec('/x.mp3', title='X', artist='Bob', album='Same',
                heard=60, skip=True, start=ts(datetime(2026, 3, 5, 10, 0))),
        ]
        rows = months(records, LOCATE)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].top_artist, 'Bob')
        self.assertEqual(rows[0].top_song, '')


if __name__ == '__main__':
    unittest.main()
