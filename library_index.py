"""
library_index.py - group a music library into artists and albums.

A library is a flat list of tracks: any objects with string attributes
filepath, title, artist, album, year and a track_number (a str like
"3/12", an int, or None).  build_index groups them into folder-based
albums (one per directory, with songs in track order) and loose songs
sitting directly in a library root, decides each album's artist (parent
folder name, tag majority, or "Various Artists") and album name/year,
and returns a LibraryIndex for browsing and searching.

Paths are Windows paths, so all path work goes through ntpath.  The
module is pure and stdlib-only, so it can be unit tested in isolation
(see tests/test_library_index.py).
"""

import ntpath
import re
from dataclasses import dataclass, field

from transitions import track_number

VARIOUS_ARTISTS = 'Various Artists'
NO_ALBUM = '(No album)'
UNKNOWN_ARTIST = 'Unknown Artist'
FALLBACK_SHARE = 0.6

DISC_TRACK_RE = re.compile(r'(?:^| - )(\d{1,2})-(\d{1,3})(?= |$)')
LEAD_NUM_RE = re.compile(r'(\d{1,3})(?=[ ._-])')
YEAR_RE = re.compile(r'(?<!\d)(\d{4})(?!\d)')


def norm_path(p) -> str:
    """Normalised, case-folded form of a Windows path ('' for no path)."""
    if not p:
        return ''
    return ntpath.normcase(ntpath.normpath(p))


def song_sort_key(track) -> tuple:
    """Sort key putting numbered songs first, in disc/track/filename order."""
    p = ntpath.normpath(getattr(track, 'filepath', None) or '')
    fname = ntpath.basename(p)
    stem = ntpath.splitext(fname)[0]
    dt = None
    for m in DISC_TRACK_RE.finditer(stem):
        dt = m
    disc = int(dt.group(1)) if dt else 1
    num = track_number(getattr(track, 'track_number', None))
    if num is None and dt is not None:
        num = int(dt.group(2))
    if num == 0:
        num = None
    if num is None:
        lead = LEAD_NUM_RE.match(stem)
        if lead:
            num = int(lead.group(1))
    if num == 0:
        num = None
    if num is None:
        return (1, 0, 0, fname.casefold())
    return (0, disc, num, fname.casefold())


def artist_sort_key(name: str) -> tuple:
    """Sort key that ignores a leading 'The '."""
    n = (name or '').strip().casefold()
    s = n[4:] if n.startswith('the ') else n
    return (s, n)


@dataclass
class Album:
    key: str
    name: str
    year: 'int | None'
    artist_key: str
    tracks: list = field(default_factory=list)


@dataclass
class Artist:
    key: str
    name: str
    albums: list = field(default_factory=list)


@dataclass
class SearchHit:
    album: Album
    tracks: list
    whole_album: bool


def _attr(track, name) -> str:
    value = getattr(track, name, None)
    return '' if value is None else str(value)


def _stripped(track, name) -> str:
    return _attr(track, name).strip()


def _most_common(values):
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    best = None
    best_count = 0
    for v, c in counts.items():
        if c > best_count:
            best, best_count = v, c
    return best


def _album_year(tracks) -> 'int | None':
    years = []
    for t in tracks:
        m = YEAR_RE.search(_attr(t, 'year'))
        if m:
            years.append(int(m.group(1)))
    if not years:
        return None
    counts = {}
    for y in years:
        counts[y] = counts.get(y, 0) + 1
    top = max(counts.values())
    return min(y for y, c in counts.items() if c == top)


def _fallback_artist(tracks) -> str:
    counts = {}
    for t in tracks:
        name = _stripped(t, 'artist')
        if not name:
            continue
        cf = name.casefold()
        entry = counts.get(cf)
        if entry is None:
            counts[cf] = [1, name]
        else:
            entry[0] += 1
    if not counts:
        return VARIOUS_ARTISTS
    best_cf = None
    best_count = 0
    for cf, (c, _) in counts.items():
        if c > best_count:
            best_cf, best_count = cf, c
    if best_count >= FALLBACK_SHARE * len(tracks):
        return counts[best_cf][1]
    return VARIOUS_ARTISTS


def _folder_artist(folder, tracks, root_keys) -> str:
    parent = ntpath.dirname(folder)
    parent_name = ntpath.basename(parent)
    if (ntpath.normcase(parent) in root_keys
            or parent_name == ''
            or parent_name.casefold() == 'compilations'):
        return _fallback_artist(tracks)
    return parent_name


def _folder_album_name(tracks, folder) -> str:
    tags = [n for n in (_stripped(t, 'album') for t in tracks) if n]
    if tags:
        return _most_common(tags)
    name = ntpath.basename(folder)
    return name if name else NO_ALBUM


class LibraryIndex:
    """Browsable view over a built index: artists, albums and lookups."""

    def __init__(self, artists, albums_by_key, artists_by_key, tracks_by_path):
        self.artists = artists
        self._albums = albums_by_key
        self._artists = artists_by_key
        self._track_album = tracks_by_path

    def album(self, key) -> 'Album | None':
        return self._albums.get(key)

    def artist(self, key) -> 'Artist | None':
        return self._artists.get(key)

    def album_of(self, track) -> 'Album | None':
        np = norm_path(getattr(track, 'filepath', None))
        if not np:
            return None
        return self._track_album.get(np)

    def album_tracks(self, key) -> list:
        album = self._albums.get(key)
        return list(album.tracks) if album else []

    def artist_tracks(self, key) -> list:
        artist = self._artists.get(key)
        if artist is None:
            return []
        tracks = []
        for album in artist.albums:
            tracks.extend(album.tracks)
        return tracks

    def search(self, query: str) -> list:
        q = (query or '').strip().casefold()
        if q == '':
            return []
        hits = []
        for artist in self.artists:
            for album in artist.albums:
                if q in artist.name.casefold() or q in album.name.casefold():
                    hits.append(SearchHit(album, list(album.tracks), True))
                    continue
                matched = [t for t in album.tracks
                           if q in _attr(t, 'title').casefold()
                           or q in _attr(t, 'artist').casefold()
                           or q in _attr(t, 'album').casefold()]
                if matched:
                    hits.append(SearchHit(album, matched, False))
        return hits

    def find_artist(self, prefix: str, from_key=None) -> 'Artist | None':
        p = (prefix or '').casefold()
        n = len(self.artists)
        if p == '' or n == 0:
            return None
        start = 0
        if from_key is not None:
            artist = self._artists.get(from_key)
            if artist is not None:
                start = self.artists.index(artist)
        for i in range(n):
            artist = self.artists[(start + i) % n]
            if (artist.name.casefold().startswith(p)
                    or artist_sort_key(artist.name)[0].startswith(p)):
                return artist
        return None


def build_index(tracks, roots=()) -> LibraryIndex:
    """Group tracks into artists and albums; roots are the library roots."""
    root_keys = {norm_path(r) for r in roots if r}
    seen = set()
    dir_buckets = {}
    loose_buckets = {}
    bucket_order = []
    for t in tracks:
        np = norm_path(getattr(t, 'filepath', None))
        if not np or np in seen:
            continue
        seen.add(np)
        p = ntpath.normpath(getattr(t, 'filepath', None) or '')
        folder = ntpath.dirname(p)
        folder_key = ntpath.normcase(folder)
        if folder_key in root_keys:
            artist_cf = _stripped(t, 'artist').casefold() or UNKNOWN_ARTIST.casefold()
            album_cf = _stripped(t, 'album').casefold()
            bkey = ('loose', folder_key, artist_cf, album_cf)
            bucket = loose_buckets.setdefault(bkey, [])
        else:
            bkey = ('dir', folder_key)
            if bkey not in dir_buckets:
                dir_buckets[bkey] = (folder, [])
            bucket = dir_buckets[bkey][1]
        if not bucket:
            bucket_order.append(bkey)
        bucket.append(t)

    album_by_key = {}
    artist_by_key = {}
    track_by_path = {}
    artists = []
    for bkey in bucket_order:
        if bkey[0] == 'dir':
            folder, raw = dir_buckets[bkey]
            ordered = sorted(raw, key=song_sort_key)
            artist_name = _folder_artist(folder, ordered, root_keys)
            album_name = _folder_album_name(ordered, folder)
            key = bkey[1]
        else:
            _, folder_key, artist_cf, album_cf = bkey
            raw = loose_buckets[bkey]
            ordered = sorted(raw, key=song_sort_key)
            first = raw[0]
            artist_name = _stripped(first, 'artist') or UNKNOWN_ARTIST
            album_name = _stripped(first, 'album') or NO_ALBUM
            key = f'{folder_key}|{artist_cf}|{album_cf}'
        album = Album(key=key, name=album_name, year=_album_year(ordered),
                      artist_key=artist_name.casefold(), tracks=ordered)
        album_by_key[key] = album
        for t in ordered:
            track_by_path[norm_path(getattr(t, 'filepath', None))] = album
        artist = artist_by_key.get(album.artist_key)
        if artist is None:
            artist = Artist(key=album.artist_key, name=artist_name)
            artist_by_key[album.artist_key] = artist
            artists.append(artist)
        artist.albums.append(album)

    for artist in artists:
        artist.albums.sort(key=lambda a: (a.year is None, a.year or 0,
                                          a.name.casefold(), a.key))
    artists.sort(key=lambda a: artist_sort_key(a.name))
    return LibraryIndex(artists, album_by_key, artist_by_key, track_by_path)
