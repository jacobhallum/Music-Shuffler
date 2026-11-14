"""
listening_stats.py - listening statistics computed from listening-log records.

Pure functions only: no file access, no GUI. Input is a list of record dicts
as produced by listening_log.make_record(), plus a locate(path) function that
maps a path to a Place (artist/album identity), or None when unknown.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

PERIODS = ('week', 'month', 'year', 'all')
UNKNOWN_ARTIST = 'Unknown Artist'
NO_ALBUM = '(No album)'


@dataclass(frozen=True)
class Place:
    artist_key: str
    artist_name: str
    album_key: str
    album_name: str


@dataclass
class TrackInfo:
    path: str
    title: str
    place: Place
    duration: float = 0.0
    play_count: int = 0
    qualified_play_count: int = 0
    manual_skip_count: int = 0
    last_played: 'int | None' = None
    date_added: 'int | None' = None
    loved: bool = False


@dataclass
class Row:
    key: str
    name: str
    sub: str
    plays: int = 0
    hours: float = 0.0
    skips: int = 0
    starts: int = 0


@dataclass
class Summary:
    hours: float
    plays: int
    songs: int
    artists: int
    new_songs: int
    shuffle_share: 'float | None'


@dataclass
class PeriodStats:
    summary: Summary
    top_artists: list
    top_songs: list
    top_albums: list
    skipped_songs: list
    skipped_artists: list
    by_hour: list
    by_weekday: list


@dataclass
class MonthRow:
    year: int
    month: int
    hours: float
    plays: int
    top_artist: str
    top_song: str


def month_bounds(year: int, month: int) -> tuple:
    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)
    return start.timestamp(), end.timestamp()


def period_bounds(kind: str, now: float) -> tuple:
    d = datetime.fromtimestamp(now)
    if kind == 'week':
        start = datetime(d.year, d.month, d.day) - timedelta(days=d.weekday())
        end = start + timedelta(days=7)
        return start.timestamp(), end.timestamp()
    if kind == 'month':
        return month_bounds(d.year, d.month)
    if kind == 'year':
        return datetime(d.year, 1, 1).timestamp(), datetime(d.year + 1, 1, 1).timestamp()
    if kind == 'all':
        return None, None
    raise ValueError(f"unknown period: {kind!r}")


def place_for_record(rec: dict, locate) -> Place:
    p = locate(rec.get('path') or '')
    if p is not None:
        return p
    artist = (rec.get('artist') or '').strip() or UNKNOWN_ARTIST
    album = (rec.get('album') or '').strip() or NO_ALBUM
    artist_key = artist.casefold()
    return Place(artist_key, artist, f'tag|{artist_key}|{album.casefold()}', album)


def _in_period(rec, start, end):
    s = rec.get('start')
    if s is None:
        return False
    if start is not None and s < start:
        return False
    if end is not None and s >= end:
        return False
    return True


def period_stats(records, start, end, locate, tracks_by_path, top_n: int = 25) -> PeriodStats:
    songs = {}
    artists = {}
    albums = {}
    total_hours = 0.0
    plays = 0
    played_paths = set()
    played_artist_keys = set()
    no_ctx_hours = 0.0
    by_hour = [0.0] * 24
    by_weekday = [0.0] * 7
    in_period = []

    for rec in records:
        if not _in_period(rec, start, end):
            continue
        in_period.append(rec)
        path = rec.get('path') or ''
        place = place_for_record(rec, locate)
        heard = float(rec.get('heard') or 0)
        played = bool(rec.get('q'))
        skipped = bool(rec.get('skip'))
        if path in tracks_by_path:
            title = tracks_by_path[path].title
        else:
            title = rec.get('title') or ''

        total_hours += heard / 3600
        if played:
            plays += 1
            played_paths.add(path)
            played_artist_keys.add(place.artist_key)
        if 'ctx' not in rec or rec.get('ctx') is None:
            no_ctx_hours += heard / 3600
        t = datetime.fromtimestamp(rec['start'])
        by_hour[t.hour] += heard / 3600
        by_weekday[t.weekday()] += heard / 3600

        row = songs.get(path)
        if row is None:
            row = songs[path] = Row(key=path, name=title, sub=place.artist_name)
        row.starts += 1
        row.plays += 1 if played else 0
        row.skips += 1 if skipped else 0
        row.hours += heard / 3600

        row = artists.get(place.artist_key)
        if row is None:
            row = artists[place.artist_key] = Row(key=place.artist_key, name=place.artist_name, sub='')
        row.starts += 1
        row.plays += 1 if played else 0
        row.skips += 1 if skipped else 0
        row.hours += heard / 3600

        row = albums.get(place.album_key)
        if row is None:
            row = albums[place.album_key] = Row(key=place.album_key, name=place.album_name,
                                                sub=place.artist_name)
        row.starts += 1
        row.plays += 1 if played else 0
        row.skips += 1 if skipped else 0
        row.hours += heard / 3600

    top_songs = sorted(
        (r for r in songs.values() if r.plays >= 1),
        key=lambda r: (-r.plays, -r.hours, r.name.casefold(), r.key))[:top_n]
    top_artists = sorted(
        (r for r in artists.values() if r.plays >= 1 or r.hours > 0),
        key=lambda r: (-r.hours, -r.plays, r.name.casefold(), r.key))[:top_n]
    top_albums = sorted(
        (r for r in albums.values() if r.plays >= 1 or r.hours > 0),
        key=lambda r: (-r.hours, -r.plays, r.name.casefold(), r.key))[:top_n]
    skipped_songs = sorted(
        (r for r in songs.values() if r.starts >= 3 and r.skips >= 1),
        key=lambda r: (-(r.skips / r.starts), -r.skips, r.name.casefold(), r.key))[:top_n]
    skipped_artists = sorted(
        (r for r in artists.values() if r.starts >= 5 and r.skips >= 1),
        key=lambda r: (-(r.skips / r.starts), -r.skips, r.name.casefold(), r.key))[:top_n]

    if in_period:
        lo = start if start is not None else min(rec['start'] for rec in in_period)
    else:
        lo = start
    hi = end
    new_songs = 0
    seen = set()
    for rec in in_period:
        if not bool(rec.get('q')):
            continue
        path = rec.get('path') or ''
        if path in seen:
            continue
        seen.add(path)
        info = tracks_by_path.get(path)
        if info is None or info.date_added is None:
            continue
        if lo <= info.date_added and (hi is None or info.date_added < hi):
            new_songs += 1

    shuffle_share = (no_ctx_hours / total_hours) if total_hours != 0 else None
    summary = Summary(
        hours=total_hours,
        plays=plays,
        songs=len(played_paths),
        artists=len(played_artist_keys),
        new_songs=new_songs,
        shuffle_share=shuffle_share,
    )
    return PeriodStats(
        summary=summary,
        top_artists=top_artists,
        top_songs=top_songs,
        top_albums=top_albums,
        skipped_songs=skipped_songs,
        skipped_artists=skipped_artists,
        by_hour=by_hour,
        by_weekday=by_weekday,
    )


def months(records, locate) -> list:
    groups = {}
    for rec in records:
        s = rec.get('start')
        if s is None:
            continue
        t = datetime.fromtimestamp(s)
        groups.setdefault((t.year, t.month), []).append(rec)
    out = []
    for (year, month) in sorted(groups, reverse=True):
        recs = groups[(year, month)]
        start, end = month_bounds(year, month)
        stats = period_stats(recs, start, end, locate, {})
        out.append(MonthRow(
            year=year,
            month=month,
            hours=stats.summary.hours,
            plays=stats.summary.plays,
            top_artist=stats.top_artists[0].name if stats.top_artists else '',
            top_song=stats.top_songs[0].name if stats.top_songs else '',
        ))
    return out
