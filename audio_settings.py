"""
audio_settings.py - validated sound-settings store for the music player.

Owns the song-transition / equalizer / loudness-leveling settings as JSON.
A hand-edited or corrupt file must never crash the player: every bad value
falls back to its default via normalize().
"""

import json
import math
import os

TRANSITION_MODES = ('off', 'crossfade', 'smart')
MIN_TRANSITION_SEC = 1
MAX_TRANSITION_SEC = 12
EQ_DB_LIMIT = 12.0
EQ_BAND_COUNT = 10
MAX_USER_PRESETS = 100
MAX_NAME_LEN = 64

DEFAULTS = {
    'version': 1,
    'transition_mode': 'off',
    'transition_seconds': 6,
    'eq_enabled': False,
    'eq_preset': 'Flat',
    'eq_bands_db': [0.0] * 10,
    'eq_preamp_db': 0.0,
    'eq_user_presets': {},
    'leveling_enabled': False,
}


def _is_number(x):
    """A number means an int or float that is not a bool and is finite."""
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return False
    try:
        return math.isfinite(x)
    except OverflowError:  # int too large for a float (hand-edited file)
        return False


def _clamp_db(x):
    """Clamp to [-EQ_DB_LIMIT, EQ_DB_LIMIT] and round to 1 decimal, as float."""
    return float(round(min(EQ_DB_LIMIT, max(-EQ_DB_LIMIT, x)), 1))


def normalize(raw):
    """Return a new settings dict with exactly the DEFAULTS keys.

    Never raises: every bad value falls back to its default.  Returned
    lists / dicts are deep copies, shared with neither DEFAULTS nor raw.
    """
    out = {k: v for k, v in DEFAULTS.items()}
    out['eq_bands_db'] = list(DEFAULTS['eq_bands_db'])
    out['eq_user_presets'] = {}
    if not isinstance(raw, dict):
        return out

    if raw.get('transition_mode') in TRANSITION_MODES:
        out['transition_mode'] = raw['transition_mode']

    x = raw.get('transition_seconds')
    if _is_number(x):
        out['transition_seconds'] = int(min(MAX_TRANSITION_SEC,
                                            max(MIN_TRANSITION_SEC,
                                                math.floor(x + 0.5))))

    if isinstance(raw.get('eq_enabled'), bool):
        out['eq_enabled'] = raw['eq_enabled']
    if isinstance(raw.get('leveling_enabled'), bool):
        out['leveling_enabled'] = raw['leveling_enabled']

    # Missing key keeps the default ('Flat'); a present but invalid name means a custom curve ('').
    if 'eq_preset' in raw:
        p = raw['eq_preset']
        out['eq_preset'] = p if isinstance(p, str) and len(p) <= MAX_NAME_LEN else ''

    bands = raw.get('eq_bands_db')
    if (isinstance(bands, list) and len(bands) == EQ_BAND_COUNT
            and all(_is_number(v) for v in bands)):
        out['eq_bands_db'] = [_clamp_db(v) for v in bands]

    if _is_number(raw.get('eq_preamp_db')):
        out['eq_preamp_db'] = _clamp_db(raw['eq_preamp_db'])

    presets = raw.get('eq_user_presets')
    if isinstance(presets, dict):
        kept = {}
        for name, value in presets.items():
            if len(kept) >= MAX_USER_PRESETS:
                break
            if not (isinstance(name, str) and 0 < len(name) <= MAX_NAME_LEN):
                continue
            if not isinstance(value, dict):
                continue
            pbands = value.get('bands_db')
            if (not isinstance(pbands, list) or len(pbands) != EQ_BAND_COUNT
                    or not all(_is_number(v) for v in pbands)):
                continue
            if not _is_number(value.get('preamp_db')):
                continue
            kept[name] = {
                'bands_db': [_clamp_db(v) for v in pbands],
                'preamp_db': _clamp_db(value['preamp_db']),
            }
        out['eq_user_presets'] = kept

    return out


def load(path):
    """Load settings; defaults for missing / unreadable / invalid JSON."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError, RecursionError):
        return normalize(None)
    return normalize(data)


def save(path, settings):
    """Atomically write the settings store."""
    tmp = f"{path}.tmp"
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(json.dumps(normalize(settings), indent=2))
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
