"""
listening_log.py - append-only JSON Lines log of finished playbacks.

One record per finished song playback, written as a compact JSON object per
line, kept for a future "listening stats" screen:

  - append() never rewrites existing lines (safe with a running player)
  - read() skips blank / corrupt / foreign-version lines instead of failing

Records are built with make_record(); path is normalised the same way as
shuffle_history so the same file compares equal across sessions.
"""

import json
import math
import os

VERSION = 1

# reasons a playback can end (mirrors playback_feedback)
REASONS = ('natural_end', 'next', 'previous', 'select', 'stop', 'failure')

# how the next track started after this one
TRANSITIONS = ('cut', 'gapless', 'crossfade', 'smart')


def _seconds(x):
    """Round to 0.1 s; None, NaN, inf or a non-number become 0.0 so the line stays valid JSON."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 0.0
    return round(x, 1) if math.isfinite(x) else 0.0


def make_record(*, started_at, ended_at, path, title, artist, album, genre,
                duration_sec, audible_sec, reason, qualified, skip,
                transition):
    """Build one log record dict with fixed keys and normalised values."""
    if reason not in REASONS:
        raise ValueError(f"unknown reason: {reason!r}")
    if transition not in TRANSITIONS:
        raise ValueError(f"unknown transition: {transition!r}")
    return {
        'v':      VERSION,
        'start':  int(started_at),
        'end':    int(ended_at),
        'path':   os.path.normcase(os.path.normpath(path or "")),
        'title':  str(title) if title is not None else '',
        'artist': str(artist) if artist is not None else '',
        'album':  str(album) if album is not None else '',
        'genre':  str(genre) if genre is not None else '',
        'dur':    _seconds(duration_sec),
        'heard':  _seconds(audible_sec),
        'reason': reason,
        'q':      bool(qualified),
        'skip':   bool(skip),
        'tr':     transition,
    }


def append(path, record):
    """Append one record as a single JSON line (UTF-8, no ASCII escaping)."""
    line = json.dumps(record, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n'
    with open(path, 'a+b') as f:
        # A crash can leave the last line without its newline; start a fresh line so it can't swallow this one.
        f.seek(0, os.SEEK_END)
        if f.tell() > 0:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b'\n':
                line = '\n' + line
        f.seek(0, os.SEEK_END)
        f.write(line.encode('utf-8'))


def read(path):
    """Return the valid version-1 record dicts in file order; [] if missing."""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except OSError:
        return []
    out = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get('v') != VERSION:
            continue
        out.append(obj)
    return out
