"""
shuffle_history.py - persisted ordered listening history (standalone, no GUI).

Stores the last MAX_HISTORY successful playback starts so Smart Shuffle can
keep session continuity across restarts:
  - recent tracks/artists get a soft score penalty (via to_recent_history)
  - the last few started tracks are protected from the first few positions of
    a fresh shuffle (via protected_paths), expiring after a wall-clock window

The file lives at <cache>/shuffle_history.json.  Missing or corrupt -> treated
as empty history.
"""

import json
import os
import time

HISTORY_VERSION = 1
MAX_HISTORY = 200

_UNKNOWN_ARTIST = {"", "unknown artist"}
_UNKNOWN_ALBUM  = {"", "unknown album"}
_UNKNOWN_GENRE  = {"", "unknown"}


def _norm_path(fp):
    return os.path.normcase(os.path.normpath(fp or ""))


def _norm_tag(value, unknown):
    v = (value or "").strip().lower()
    return v if v and v not in unknown else None


def make_entry(filepath, artist=None, album=None, genre=None, ts=None):
    """Build one history entry (normalised path + tag keys + timestamp)."""
    return {
        'path':   _norm_path(filepath),
        'artist': _norm_tag(artist, _UNKNOWN_ARTIST),
        'album':  _norm_tag(album, _UNKNOWN_ALBUM),
        'genre':  _norm_tag(genre, _UNKNOWN_GENRE),
        'ts':     float(ts if ts is not None else time.time()),
    }


def append(entries, entry):
    """Return a new list with *entry* appended, capped at MAX_HISTORY."""
    return (list(entries) + [entry])[-MAX_HISTORY:]


def load(path):
    """Load history entries; return [] for missing / corrupt / wrong-version."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or data.get('version') != HISTORY_VERSION:
        return []
    entries = data.get('entries')
    if not isinstance(entries, list):
        return []
    clean = []
    for e in entries:
        if not (isinstance(e, dict) and isinstance(e.get('path'), str)):
            continue
        try:
            e['ts'] = float(e.get('ts', 0.0))
        except (TypeError, ValueError):
            e['ts'] = 0.0
        clean.append(e)
    return clean[-MAX_HISTORY:]


def save(path, entries):
    """Atomically write history (capped at MAX_HISTORY) to *path*."""
    payload = {'version': HISTORY_VERSION, 'entries': list(entries)[-MAX_HISTORY:]}
    tmp = f"{path}.tmp"
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(payload, f)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def to_recent_history(entries):
    """Adapt entries to smart_shuffle's ``recent_history`` = [(path, artist)]."""
    return [(e['path'], e.get('artist')) for e in entries]


def protected_paths(entries, *, now=None, count=3, max_age_h=24):
    """Normalised paths of the last *count* starts still within *max_age_h*."""
    if now is None:
        now = time.time()
    cutoff = now - max_age_h * 3600.0
    out = []
    for e in entries[-count:]:
        try:
            ts = float(e.get('ts', 0))
        except (TypeError, ValueError):
            ts = 0.0
        if ts >= cutoff:
            out.append(e['path'])
    return out
