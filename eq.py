"""
eq.py - Winamp-style 10-band graphic equalizer (standalone, no GUI).

Owns the band table, Winamp's built-in presets, reading/writing Winamp
`.eqf` preset files, and building the FFmpeg audio-filter string that
applies the EQ.

Each slider is stored as a value 1..64 where 33 is flat (0 dB) and 64 is
the top (+12 dB); in `.eqf` files each byte is `64 - value`.
"""

import math

BAND_FREQS_HZ = (60, 170, 310, 600, 1000, 3000, 6000, 12000, 14000, 16000)
EQ_DB_LIMIT = 12.0
EQF_HEADER = b'Winamp EQ library file v1.1\x1a!--'
EQF_NAME_LEN = 257
EQF_FLAT_VALUE = 33

_EQF_RECORD_LEN = EQF_NAME_LEN + 11

# Winamp preset table: name -> (10 band values 1..64, preamp value).
_WINAMP_PRESET_VALUES = [
    ('Classical', (33, 33, 33, 33, 33, 33, 20, 20, 20, 16), 33),
    ('Club', (33, 33, 38, 42, 42, 42, 38, 33, 33, 33), 33),
    ('Dance', (48, 44, 36, 32, 32, 22, 20, 20, 32, 32), 33),
    ('Laptop speakers/headphones', (40, 50, 41, 26, 28, 35, 40, 48, 53, 56), 33),
    ('Large hall', (49, 49, 42, 42, 33, 24, 24, 24, 33, 33), 33),
    ('Party', (44, 44, 33, 33, 33, 33, 33, 33, 44, 44), 33),
    ('Pop', (29, 40, 44, 45, 41, 30, 28, 28, 29, 29), 33),
    ('Reggae', (33, 33, 31, 22, 33, 43, 43, 33, 33, 33), 33),
    ('Rock', (45, 40, 23, 19, 26, 39, 47, 50, 50, 50), 33),
    ('Soft', (40, 35, 30, 28, 30, 39, 46, 48, 50, 52), 33),
    ('Ska', (28, 24, 25, 31, 39, 42, 47, 48, 50, 48), 33),
    ('Full Bass', (48, 48, 48, 42, 35, 25, 18, 15, 14, 14), 33),
    ('Soft Rock', (39, 39, 36, 31, 25, 23, 26, 31, 37, 47), 33),
    ('Full Treble', (16, 16, 16, 25, 37, 50, 58, 58, 58, 60), 33),
    ('Full Bass & Treble', (44, 42, 33, 20, 24, 35, 46, 50, 52, 52), 33),
    ('Live', (24, 33, 39, 41, 42, 42, 39, 37, 37, 36), 33),
    ('Techno', (45, 42, 33, 23, 24, 33, 45, 48, 48, 47), 33),
]


def eqf_value_to_db(value):
    """Convert a Winamp slider value (1..64, 33 = flat) to band dB."""
    v = max(1, min(64, int(value)))
    db = round((v - EQF_FLAT_VALUE) * EQ_DB_LIMIT / 31, 1)
    return max(-EQ_DB_LIMIT, min(EQ_DB_LIMIT, db))


def db_to_eqf_value(db):
    """Convert band dB to a Winamp slider value (1..64, 33 = flat)."""
    v = round(EQF_FLAT_VALUE + _sane(db, limit=1e6) * 31 / EQ_DB_LIMIT)
    return max(1, min(64, int(v)))


def _sane(x, limit=EQ_DB_LIMIT):
    """A finite dB value clamped to +-limit; anything else (None, NaN, inf, non-number) is 0.0."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(x):
        return 0.0
    return max(-limit, min(limit, x))


def _preset_from_values(values):
    bands, preamp = values
    return [eqf_value_to_db(v) for v in bands], eqf_value_to_db(preamp)


BUILTIN_PRESETS = {'Flat': _preset_from_values(([EQF_FLAT_VALUE] * 10, EQF_FLAT_VALUE))}
for _name, _bands, _preamp in _WINAMP_PRESET_VALUES:
    BUILTIN_PRESETS[_name] = _preset_from_values((_bands, _preamp))


def parse_eqf(data):
    """Parse a Winamp .eqf file into [(name, bands_db, preamp_db), ...]."""
    if not data.startswith(EQF_HEADER):
        raise ValueError('not a Winamp .eqf file (bad header)')
    body = data[len(EQF_HEADER):]
    if len(body) == 0:
        raise ValueError('no presets in .eqf data')
    if len(body) % _EQF_RECORD_LEN != 0:
        raise ValueError('truncated .eqf record')
    presets = []
    for offset in range(0, len(body), _EQF_RECORD_LEN):
        record = body[offset:offset + _EQF_RECORD_LEN]
        name_bytes = record[:EQF_NAME_LEN]
        name = name_bytes.split(b'\x00', 1)[0].decode('latin-1')
        values = [64 - b for b in record[EQF_NAME_LEN:]]
        bands_db = [eqf_value_to_db(v) for v in values[:10]]
        preamp_db = eqf_value_to_db(values[10])
        presets.append((name, bands_db, preamp_db))
    return presets


def build_eqf(presets):
    """Build Winamp .eqf bytes from [(name, bands_db, preamp_db), ...]."""
    out = bytearray(EQF_HEADER)
    for name, bands_db, preamp_db in presets:
        name_bytes = name.encode('latin-1', errors='replace')[:EQF_NAME_LEN - 1]
        out += name_bytes + b'\x00' * (EQF_NAME_LEN - len(name_bytes))
        for db in list(bands_db) + [preamp_db]:
            out.append(64 - db_to_eqf_value(db))
    return bytes(out)


def filter_chain(bands_db, preamp_db, gain_db=0.0, *, enabled=True):
    """Build the FFmpeg audio-filter string applying the EQ."""
    # One bad value would make FFmpeg reject the graph and every song fail to decode: sanitise everything.
    parts = []
    total = (_sane(preamp_db) if enabled else 0.0) + _sane(gain_db, limit=30.0)
    if abs(total) >= 0.05:
        parts.append(f'volume={total:.2f}dB')
    if enabled:
        bands = (list(bands_db) + [0.0] * len(BAND_FREQS_HZ))[:len(BAND_FREQS_HZ)]
        for i, g in enumerate(bands):
            g = _sane(g)
            if abs(g) < 0.05:
                continue
            if i == 0:
                parts.append(f'lowshelf=f={BAND_FREQS_HZ[0]}:g={g:.1f}')
            elif i == 9:
                parts.append(f'highshelf=f={BAND_FREQS_HZ[9]}:g={g:.1f}')
            else:
                parts.append(f'equalizer=f={BAND_FREQS_HZ[i]}:t=q:w=1:g={g:.1f}')
    return ','.join(parts)
