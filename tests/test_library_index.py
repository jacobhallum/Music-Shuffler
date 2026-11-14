"""
tests/test_library_index.py - stdlib-only unit tests for library_index.py

Run with:
    python -m unittest tests/test_library_index.py -v
"""

import os
import sys
import unittest

# Allow running from project root or tests/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import ntpath

from library_index import (
    NO_ALBUM,
    UNKNOWN_ARTIST,
    VARIOUS_ARTISTS,
    artist_sort_key,
    build_index,
    song_sort_key,
)


class FakeTrack:
    def __init__(self, filepath, title='Title', artist='', album='',
                 year='', track_number=None):
        self.filepath = filepath
        self.title = title
        self.artist = artist
        self.album = album
        self.year = year
        self.track_number = track_number


def T(filepath, **kw):
    return FakeTrack(filepath, **kw)


def dir_key(path):
    return ntpath.normcase(ntpath.normpath(path))


class SortKeyTests(unittest.TestCase):
    def test_song_sort_key_variants(self):
        self.assertEqual(song_sort_key(T(r'X:\A\01-06 Title.mp3')),
                         (0, 1, 6, '01-06 title.mp3'))
        self.assertEqual(song_sort_key(T(r'X:\A\A - B - 02-03 T.mp3')),
                         (0, 2, 3, 'a - b - 02-03 t.mp3'))
        self.assertEqual(song_sort_key(T(r'X:\A\07 Lead.mp3')),
                         (0, 1, 7, '07 lead.mp3'))
        self.assertEqual(song_sort_key(T(r'X:\A\Song.mp3', track_number='3/12')),
                         (0, 1, 3, 'song.mp3'))
        self.assertEqual(song_sort_key(T(r'X:\A\Zed.mp3')),
                         (1, 0, 0, 'zed.mp3'))

    def test_artist_sort_key_ignores_the(self):
        self.assertEqual(artist_sort_key('The Beta '), ('beta', 'the beta'))
        self.assertEqual(artist_sort_key('Alpha'), ('alpha', 'alpha'))


class GroupingTests(unittest.TestCase):
    def test_grouping_by_folder_and_parent_artist(self):
        tracks = [
            T(r'X:\Lib\Artist A\Album 1\01 Song.mp3', title='S1', track_number='1'),
            T(r'X:\Lib\Artist A\Album 1\02 Song.mp3', title='S2', track_number='2'),
            T(r'X:\Lib\Artist A\Album 2\01 Other.mp3', title='O1', track_number='1'),
        ]
        idx = build_index(tracks)
        artist = idx.artist('artist a')
        self.assertIsNotNone(artist)
        self.assertEqual(artist.name, 'Artist A')
        self.assertEqual(len(artist.albums), 2)
        a1 = idx.album(dir_key(r'X:\Lib\Artist A\Album 1'))
        a2 = idx.album(dir_key(r'X:\Lib\Artist A\Album 2'))
        self.assertEqual([t.title for t in a1.tracks], ['S1', 'S2'])
        self.assertEqual([t.title for t in a2.tracks], ['O1'])
        self.assertEqual([t.title for t in idx.artist_tracks('artist a')],
                         ['S1', 'S2', 'O1'])

    def test_order_by_track_number_tag(self):
        tracks = [
            T(r'X:\Lib\Artist A\Album 1\01 Song.mp3', title='C Song', track_number='3/12'),
            T(r'X:\Lib\Artist A\Album 1\02 Song.mp3', title='A Song', track_number='1/12'),
            T(r'X:\Lib\Artist A\Album 1\03 Song.mp3', title='B Song', track_number='2/12'),
        ]
        idx = build_index(tracks)
        album = idx.album(dir_key(r'X:\Lib\Artist A\Album 1'))
        self.assertEqual([t.title for t in album.tracks],
                         ['A Song', 'B Song', 'C Song'])

    def test_order_by_disc_track_in_filename_disc2_last(self):
        tracks = [
            T(r'X:\Lib\Artist A\Album 1\01-06 Delta.mp3', title='Delta'),
            T(r'X:\Lib\Artist A\Album 1\01-02 Alpha.mp3', title='Alpha'),
            T(r'X:\Lib\Artist A\Album 1\01-11 Echo.mp3', title='Echo'),
            T(r'X:\Lib\Artist A\Album 1\02-01 Zulu.mp3', title='Zulu'),
        ]
        idx = build_index(tracks)
        album = idx.album(dir_key(r'X:\Lib\Artist A\Album 1'))
        self.assertEqual([t.title for t in album.tracks],
                         ['Alpha', 'Delta', 'Echo', 'Zulu'])

    def test_order_by_artist_album_disc_track_name(self):
        tracks = [
            T(r'X:\Lib\Artist A\Album 1\X - Y - 02-07 Alpha.mp3', title='Alpha'),
            T(r'X:\Lib\Artist A\Album 1\X - Y - 02-03 Bravo.mp3', title='Bravo'),
        ]
        idx = build_index(tracks)
        album = idx.album(dir_key(r'X:\Lib\Artist A\Album 1'))
        self.assertEqual([t.title for t in album.tracks], ['Bravo', 'Alpha'])

    def test_order_by_leading_number(self):
        tracks = [
            T(r'X:\Lib\Artist A\Album 1\07 Bravo.mp3', title='Bravo'),
            T(r'X:\Lib\Artist A\Album 1\02 Alpha.mp3', title='Alpha'),
        ]
        idx = build_index(tracks)
        album = idx.album(dir_key(r'X:\Lib\Artist A\Album 1'))
        self.assertEqual([t.title for t in album.tracks], ['Alpha', 'Bravo'])

    def test_unnumbered_last_by_filename(self):
        tracks = [
            T(r'X:\Lib\Artist A\Album 1\Zeta.mp3', title='Zeta'),
            T(r'X:\Lib\Artist A\Album 1\Alpha.mp3', title='Alpha'),
            T(r'X:\Lib\Artist A\Album 1\01 Numbered.mp3', title='Numbered'),
        ]
        idx = build_index(tracks)
        album = idx.album(dir_key(r'X:\Lib\Artist A\Album 1'))
        self.assertEqual([t.title for t in album.tracks],
                         ['Numbered', 'Alpha', 'Zeta'])

    def test_album_name_and_year_by_majority(self):
        tracks = [
            T(r'X:\Lib\Artist A\Greatest\01.mp3', album='Hits', year='2001', track_number='1'),
            T(r'X:\Lib\Artist A\Greatest\02.mp3', album='Best Of', year='2002', track_number='2'),
            T(r'X:\Lib\Artist A\Greatest\03.mp3', album='Hits', year='2001', track_number='3'),
            T(r'X:\Lib\Artist A\Greatest\04.mp3', album='Hits', year='2003', track_number='4'),
        ]
        idx = build_index(tracks)
        album = idx.album(dir_key(r'X:\Lib\Artist A\Greatest'))
        self.assertEqual(album.name, 'Hits')
        self.assertEqual(album.year, 2001)

        tracks3 = [
            T(r'X:\Lib\Artist A\Tie\01.mp3', year='2002', track_number='1'),
            T(r'X:\Lib\Artist A\Tie\02.mp3', year='2003', track_number='2'),
            T(r'X:\Lib\Artist A\Tie\03.mp3', year='2002', track_number='3'),
            T(r'X:\Lib\Artist A\Tie\04.mp3', year='2003', track_number='4'),
        ]
        idx3 = build_index(tracks3)
        self.assertEqual(idx3.album(dir_key(r'X:\Lib\Artist A\Tie')).year, 2002)

    def test_album_name_falls_back_to_folder_name(self):
        tracks = [T(r'X:\Lib\Artist A\Live 99\01.mp3', year='1999')]
        idx = build_index(tracks)
        album = idx.album(dir_key(r'X:\Lib\Artist A\Live 99'))
        self.assertEqual(album.name, 'Live 99')
        self.assertEqual(album.year, 1999)   # the year tag still counts

    def test_tag_fallback_direct_under_root(self):
        tracks = [
            T(r'X:\Lib\Big Album\01.mp3', artist='Ann', track_number='1'),
            T(r'X:\Lib\Big Album\02.mp3', artist='Ann', track_number='2'),
            T(r'X:\Lib\Big Album\03.mp3', artist='Bob', track_number='3'),
        ]
        idx = build_index(tracks, (r'X:\Lib',))
        album = idx.album(dir_key(r'X:\Lib\Big Album'))
        self.assertEqual(album.artist_key, 'ann')
        self.assertIsNotNone(idx.artist('ann'))

    def test_tag_fallback_under_compilations(self):
        tracks = [
            T(r'X:\Lib\Compilations\Summer\01.mp3', artist='Ann', track_number='1'),
            T(r'X:\Lib\Compilations\Summer\02.mp3', artist='Bob', track_number='2'),
            T(r'X:\Lib\Compilations\Summer\03.mp3', artist='Cal', track_number='3'),
        ]
        idx = build_index(tracks, (r'X:\Lib',))
        album = idx.album(dir_key(r'X:\Lib\Compilations\Summer'))
        self.assertEqual(album.artist_key, 'various artists')
        self.assertEqual(idx.artist('various artists').name, VARIOUS_ARTISTS)

    def test_tag_fallback_share_boundary(self):
        tracks = [
            T(r'X:\Lib\Mixed One\01.mp3', artist='ann x', track_number='1'),
            T(r'X:\Lib\Mixed One\02.mp3', artist='ANN X', track_number='2'),
            T(r'X:\Lib\Mixed One\03.mp3', artist='ann x', track_number='3'),
            T(r'X:\Lib\Mixed One\04.mp3', artist='bob y', track_number='4'),
            T(r'X:\Lib\Mixed One\05.mp3', artist='bob y', track_number='5'),
            T(r'X:\Lib\Mixed Two\01.mp3', artist='ann', track_number='1'),
            T(r'X:\Lib\Mixed Two\02.mp3', artist='bob', track_number='2'),
            T(r'X:\Lib\Mixed Two\03.mp3', artist='cal', track_number='3'),
            T(r'X:\Lib\Mixed Two\04.mp3', artist='ann', track_number='4'),
            T(r'X:\Lib\Mixed Two\05.mp3', artist='bob', track_number='5'),
        ]
        idx = build_index(tracks, (r'X:\Lib',))
        self.assertEqual(idx.artist('ann x').name, 'ann x')
        self.assertIsNotNone(idx.artist(VARIOUS_ARTISTS.casefold()))

    def test_loose_songs_grouped_by_album_tag(self):
        tracks = [
            T(r'X:\Lib\01 Solo Song.mp3', title='S2', artist='Solo', album='Loose LP', track_number='2/10'),
            T(r'X:\Lib\02 Solo Song.mp3', title='S1', artist='Solo', album='Loose LP', track_number='1/10'),
            T(r'X:\Lib\03 Solo Song.mp3', title='SX', artist='Solo'),
            T(r'X:\Lib\04 Duo Song.mp3', title='D1', artist='Duo', album='Loose LP'),
            T(r'X:\Lib\05 Mystery.mp3', title='M1', album='Ghost LP'),
            T(r'Y:\Music\Solo Song.mp3', title='Y1', artist='Solo', album='Loose LP'),
        ]
        idx = build_index(tracks, (r'X:\Lib', r'Y:\Music'))
        solo = idx.artist('solo')
        self.assertEqual(solo.name, 'Solo')
        self.assertEqual(len(solo.albums), 3)
        key_x = dir_key(r'X:\Lib') + '|solo|loose lp'
        key_none = dir_key(r'X:\Lib') + '|solo|'
        key_y = dir_key(r'Y:\Music') + '|solo|loose lp'
        self.assertEqual(idx.album(key_x).name, 'Loose LP')
        self.assertEqual([t.title for t in idx.album(key_x).tracks], ['S1', 'S2'])
        self.assertEqual(idx.album(key_none).name, NO_ALBUM)
        self.assertEqual(len(idx.album(key_y).tracks), 1)
        self.assertEqual(len(idx.artist('duo').albums), 1)
        ghost = idx.artist(UNKNOWN_ARTIST.casefold())
        self.assertEqual(ghost.name, UNKNOWN_ARTIST)
        self.assertEqual(idx.album(dir_key(r'X:\Lib') + '|unknown artist|ghost lp').name, 'Ghost LP')

    def test_artist_album_order_and_the_prefix(self):
        tracks = [
            T(r'X:\Lib\The B Side\Zed\01.mp3'),
            T(r'X:\Lib\The B Side\Alpha\01.mp3', year='2001'),
            T(r'X:\Lib\The B Side\Beta\01.mp3', year='2002'),
            T(r'X:\Lib\Alpha Man\Only\01.mp3'),
        ]
        idx = build_index(tracks)
        self.assertEqual([a.name for a in idx.artists], ['Alpha Man', 'The B Side'])
        names = [a.name for a in idx.artist('the b side').albums]
        self.assertEqual(names, ['Alpha', 'Beta', 'Zed'])

    def test_duplicate_and_empty_paths(self):
        p = r'X:\Lib\Artist A\Album 1\01 Song.mp3'
        tracks = [
            T(p, title='First'),
            T(p, title='Second'),
            T(p.lower(), title='NormDup'),
            T('', title='Empty'),
            T(None, title='Missing'),
        ]
        idx = build_index(tracks, (r'X:\Lib',))
        album = idx.album(dir_key(r'X:\Lib\Artist A\Album 1'))
        self.assertEqual([t.title for t in album.tracks], ['First'])
        self.assertEqual(len(idx.artists), 1)

    def test_album_of_and_list_copies(self):
        tracks = [
            T(r'X:\Lib\Artist A\Album 1\01.mp3', title='S1', track_number='1'),
            T(r'X:\Lib\Artist A\Album 1\02.mp3', title='S2', track_number='2'),
            T(r'X:\Lib\Artist A\Album 2\01.mp3', title='O1', track_number='1'),
        ]
        idx = build_index(tracks)
        a1 = idx.album(dir_key(r'X:\Lib\Artist A\Album 1'))
        self.assertIs(idx.album_of(tracks[0]), a1)
        self.assertIs(idx.album_of(tracks[1]), a1)
        self.assertIsNone(idx.album_of(T(r'X:\Nowhere\01.mp3')))
        copy = idx.album_tracks(a1.key)
        self.assertEqual([t.title for t in copy], ['S1', 'S2'])
        copy.append('junk')
        self.assertEqual(len(a1.tracks), 2)
        self.assertEqual(idx.album_tracks('nope'), [])
        self.assertEqual([t.title for t in idx.artist_tracks('artist a')],
                         ['S1', 'S2', 'O1'])
        self.assertEqual(idx.artist_tracks('nope'), [])
        self.assertIsNone(idx.album('nope'))
        self.assertIsNone(idx.artist('nope'))

    def test_search(self):
        tracks = [
            T(r'X:\Lib\Artist A\Alpha\01.mp3', title='Sunrise', track_number='1'),
            T(r'X:\Lib\Artist A\Alpha\02.mp3', title='Rainfall', track_number='2'),
            T(r'X:\Lib\Artist B\Beta\01.mp3', title='Sunset', track_number='1'),
        ]
        idx = build_index(tracks)
        self.assertEqual(idx.search('   '), [])
        hits = idx.search('Artist A')
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0].whole_album)
        self.assertEqual(hits[0].album.key, dir_key(r'X:\Lib\Artist A\Alpha'))
        self.assertEqual(len(hits[0].tracks), 2)
        hits = idx.search('sun')
        self.assertEqual(len(hits), 2)
        titles = set()
        for hit in hits:
            self.assertFalse(hit.whole_album)
            self.assertEqual(len(hit.tracks), 1)
            titles.add(hit.tracks[0].title)
        self.assertEqual(titles, {'Sunrise', 'Sunset'})

    def test_find_artist(self):
        tracks = [
            T(r'X:\Lib\Alpha Band\One\01.mp3'),
            T(r'X:\Lib\Beta Band\One\01.mp3'),
            T(r'X:\Lib\Gamma Ray\One\01.mp3'),
        ]
        idx = build_index(tracks)
        alpha = idx.artist('alpha band')
        beta = idx.artist('beta band')
        gamma = idx.artist('gamma ray')
        self.assertIs(idx.find_artist('beta'), beta)
        self.assertIs(idx.find_artist('gam'), gamma)
        self.assertIsNone(idx.find_artist(''))
        self.assertIsNone(idx.find_artist('zzz'))
        self.assertIs(idx.find_artist('gam', from_key='gamma ray'), gamma)
        self.assertIs(idx.find_artist('al', from_key='gamma ray'), alpha)
        self.assertIs(idx.find_artist('be', from_key='nope'), beta)

    def test_find_artist_skips_the(self):
        tracks = [
            T(r'X:\Lib\The B Side\One\01.mp3'),
            T(r'X:\Lib\Alpha Man\One\01.mp3'),
        ]
        idx = build_index(tracks)
        self.assertIs(idx.find_artist('b si'), idx.artist('the b side'))


if __name__ == '__main__':
    unittest.main()
