"""
tests/test_library_index_handcheck.py - hand-worked checks for library_index.py (hidden from the worker).

Run with:
    python -m unittest tests.test_library_index_handcheck -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import library_index as li


class T:
    def __init__(self, path, title='', artist='', album='', year='', track_number=None):
        self.filepath = path
        self.title = title
        self.artist = artist
        self.album = album
        self.year = year
        self.track_number = track_number

    def __repr__(self):
        return f'T({self.filepath!r})'


ROOT = 'X:\\Lib'


def names(tracks):
    return [os.path.basename(t.filepath.replace('\\', '/')) for t in tracks]


class SortKeyHandcheck(unittest.TestCase):
    def test_keys(self):
        self.assertEqual(li.song_sort_key(T(r'X:\Lib\A\B\x.m4a', track_number='3/12')), (0, 1, 3, 'x.m4a'))
        self.assertEqual(li.song_sort_key(T(r'X:\Lib\A\B\A - B - 02-07 Song.flac')),
                         (0, 2, 7, 'a - b - 02-07 song.flac'))
        # tag wins over the filename's track, disc still from the filename
        self.assertEqual(li.song_sort_key(T(r'X:\Lib\A\B\1-04 Song.m4a', track_number='9')), (0, 1, 9, '1-04 song.m4a'))
        self.assertEqual(li.song_sort_key(T(r'X:\Lib\A\B\07. Song.mp3')), (0, 1, 7, '07. song.mp3'))
        self.assertEqual(li.song_sort_key(T(r'X:\Lib\A\B\Song.mp3', track_number='0')), (1, 0, 0, 'song.mp3'))
        self.assertEqual(li.song_sort_key(T(r'X:\Lib\A\B\1999 Song.mp3')), (1, 0, 0, '1999 song.mp3'))
        self.assertEqual(li.song_sort_key(T('X:/Lib/A/B/05 Fwd.mp3')), (0, 1, 5, '05 fwd.mp3'))

    def test_artist_sort_key(self):
        self.assertEqual(li.artist_sort_key('The Band'), ('band', 'the band'))
        self.assertEqual(li.artist_sort_key('Theo'), ('theo', 'theo'))


class BuildHandcheck(unittest.TestCase):
    def setUp(self):
        self.tracks = [
            T(r'X:\Lib\Zed\Late\02 b.m4a', album='Late LP', year='2019-05-01', track_number='2/3'),
            T(r'X:\Lib\Zed\Late\01 a.m4a', album='Late LP', year='2019', track_number='1/3'),
            T(r'X:\Lib\Zed\Late\c.m4a', album='Late', year='2018'),
            T(r'X:\Lib\Zed\Early\x.m4a', album='Early', year='2001', track_number='1'),
            T(r'X:\Lib\Zed\NoYear\y.m4a', album='', track_number='1'),
            T(r'X:\Lib\The Alpha\One\01 s.mp3', album='One', year='2010'),
            T(r'X:\Lib\Compilations\Mix\01 m1.mp3', artist='Guest', album='Mix'),
            T(r'X:\Lib\Compilations\Mix\02 m2.mp3', artist='Other', album='Mix'),
            T(r'X:\Lib\Compilations\Mix\03 m3.mp3', artist='Guest', album='Mix'),
            T(r'X:\Lib\Compilations\Duo\01 d1.mp3', artist='P', album='Duo'),
            T(r'X:\Lib\Compilations\Duo\02 d2.mp3', artist='Q', album='Duo'),
            T(r'X:\Lib\loose1.mp3', artist='Solo', album='Single'),
            T(r'X:\Lib\loose2.mp3', artist='solo', album=''),
            T(r'x:\lib\zed\late\01 A.M4A', album='dup'),          # duplicate of the second track
            T('', album='empty'),
        ]
        self.idx = li.build_index(self.tracks, roots=[ROOT])

    def test_artist_order_and_names(self):
        self.assertEqual([a.name for a in self.idx.artists],
                         ['The Alpha', 'Guest', 'Solo', 'Various Artists', 'Zed'])

    def test_album_order_names_years(self):
        zed = self.idx.artist('zed')
        self.assertEqual([(a.name, a.year) for a in zed.albums],
                         [('Early', 2001), ('Late LP', 2019), ('NoYear', None)])

    def test_album_song_order_and_dedup(self):
        late = self.idx.album(li.norm_path(r'X:\Lib\Zed\Late'))
        self.assertEqual(names(late.tracks), ['01 a.m4a', '02 b.m4a', 'c.m4a'])
        self.assertIs(late.tracks[0], self.tracks[1])

    def test_compilations(self):
        mix = self.idx.album(li.norm_path(r'X:\Lib\Compilations\Mix'))
        self.assertEqual(mix.artist_key, 'guest')          # 2 of 3 = 0.667 >= 0.6
        duo = self.idx.album(li.norm_path(r'X:\Lib\Compilations\Duo'))
        self.assertEqual(duo.artist_key, 'various artists')  # 1 of 2 = 0.5

    def test_loose_songs(self):
        solo = self.idx.artist('solo')
        self.assertEqual(solo.name, 'Solo')
        self.assertEqual(sorted(a.name for a in solo.albums), ['(No album)', 'Single'])
        key = li.norm_path(ROOT) + '|solo|single'
        self.assertEqual(names(self.idx.album_tracks(key)), ['loose1.mp3'])

    def test_lookups(self):
        a = self.idx.album_of(T(r'X:\LIB\Zed\Early\X.M4A'))
        self.assertEqual(a.name, 'Early')
        self.assertIsNone(self.idx.album_of(T(r'X:\Lib\Nope\n.mp3')))
        self.assertEqual(self.idx.album_tracks('nope'), [])
        self.assertEqual(self.idx.artist_tracks('nope'), [])
        got = self.idx.album_tracks(a.key)
        got.append('junk')
        self.assertEqual(len(self.idx.album(a.key).tracks), 1)
        self.assertEqual(names(self.idx.artist_tracks('zed')), ['x.m4a', '01 a.m4a', '02 b.m4a', 'c.m4a', 'y.m4a'])

    def test_search(self):
        hits = self.idx.search('  ZED ')
        self.assertEqual([(h.album.name, h.whole_album) for h in hits],
                         [('Early', True), ('Late LP', True), ('NoYear', True)])
        self.assertEqual(self.idx.search('m2'), [])   # filenames are not searched
        hits = self.idx.search('other')               # artist tag of one Mix song
        self.assertEqual([(h.album.name, h.whole_album, names(h.tracks)) for h in hits],
                         [('Mix', False, ['02 m2.mp3'])])

    def test_search_by_title(self):
        tracks = [T(r'X:\Lib\Ar\Al\01 a.mp3', title='Hello'), T(r'X:\Lib\Ar\Al\02 b.mp3', title='Bye')]
        idx = li.build_index(tracks, roots=[ROOT])
        hits = idx.search('hell')
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].tracks, [tracks[0]])
        self.assertFalse(hits[0].whole_album)
        self.assertEqual(idx.search('   '), [])

    def test_find_artist(self):
        f = self.idx.find_artist
        self.assertEqual(f('al').name, 'The Alpha')
        self.assertEqual(f('THE').name, 'The Alpha')
        self.assertEqual(f('z').name, 'Zed')
        self.assertEqual(f('s', from_key='solo').name, 'Solo')       # inclusive
        self.assertEqual(f('g', from_key='various artists').name, 'Guest')  # wraps
        self.assertIsNone(f('q'))
        self.assertIsNone(f(''))


if __name__ == '__main__':
    unittest.main()
