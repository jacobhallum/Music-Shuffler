"""
preferences.py - durable favorite store (standalone, no GUI).

Favorites are the Smart Shuffle taste signal (Task 7), so they must NOT live
in the disposable cache.  This module owns <parent-of-cache>/shuffler_preferences.json
and is authoritative for `loved` / `loved_source` on load - the player
metadata file still carries a copy, but a cache wipe can't destroy the store.

favorites dict: { normalised_path: {'loved': bool, 'source': str|None, 'ts': float} }
"""

import json
import os
import time

PREFERENCES_VERSION = 1


def _norm(fp):
    return os.path.normcase(os.path.normpath(fp or ""))


def set_favorite(favorites, filepath, loved, *, source='user', ts=None):
    """Return a new dict with the favorite set (loved is True/False)."""
    out = dict(favorites)
    out[_norm(filepath)] = {
        'loved': bool(loved),
        'source': source,
        'ts': float(ts if ts is not None else time.time()),
    }
    return out


def get(favorites, filepath):
    """Return (loved, source) or (None, None) if unknown."""
    entry = favorites.get(_norm(filepath))
    if not isinstance(entry, dict) or 'loved' not in entry:
        return None, None
    return bool(entry['loved']), entry.get('source')


def load(path):
    """Load favorites; {} for missing / corrupt / wrong-version."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get('version') != PREFERENCES_VERSION:
        return {}
    favs = data.get('favorites')
    if not isinstance(favs, dict):
        return {}
    clean = {}
    for k, v in favs.items():
        if isinstance(v, dict) and isinstance(v.get('loved'), bool):
            clean[_norm(k)] = {
                'loved': v['loved'],
                'source': v.get('source'),
                'ts': float(v['ts']) if isinstance(v.get('ts'), (int, float)) else 0.0,
            }
    return clean


def save(path, favorites):
    """Atomically write the favorites store."""
    payload = {'version': PREFERENCES_VERSION, 'favorites': favorites}
    tmp = f"{path}.tmp"
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=1)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def merge_into_metadata(favorites, player_metadata):
    """Overlay favorites onto an in-memory player_metadata dict (keyed by
    normalised path).  Preferences win for `loved` / `loved_source`."""
    for norm_path, entry in favorites.items():
        md = player_metadata.setdefault(norm_path, {})
        md['loved'] = entry['loved']
        md['loved_source'] = entry.get('source')
