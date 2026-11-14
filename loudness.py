"""
loudness.py - EBU R128 loudness parsing, gain math and JSON cache.

Each song is measured once by feeding FFmpeg's ``ebur128`` filter output
(stderr text) into parse_ebur128; the player then applies the common
leveling gain (leveling_gain_db) so every track plays near
TARGET_LUFS, true-peak limited to PEAK_CEILING_DBTP.

Results are cached in a JSON file as compact "tenths of a dB" records
(to_record / from_record) keyed by cache_key.
"""

import json
import math
import os
import re
from dataclasses import dataclass

TARGET_LUFS = -14.0
PEAK_CEILING_DBTP = -1.0
SILENCE_LUFS = -70.0
FRAME_SEC = 0.1
EDGE_FRAMES = 300

CACHE_VERSION = 1

_FRAME_RE = re.compile(r'\bt:\s*([0-9.]+)\s.*?\bM:\s*(\S+)')


@dataclass(frozen=True)
class TrackLoudness:
    integrated_lufs: float
    true_peak_dbtp: float
    duration_sec: float
    head: tuple      # momentary loudness (LUFS) of the first frames, one value per 100 ms
    tail: tuple      # momentary loudness (LUFS) of the last frames


def _m_value(token):
    """Clamp/normalise one momentary-loudness token to a float."""
    try:
        m = float(token)
    except ValueError:
        return SILENCE_LUFS
    if not math.isfinite(m) or m <= SILENCE_LUFS:  # nan / +-inf / silent / below floor
        return SILENCE_LUFS
    return round(m, 1)


def _summary_value(lines, prefix):
    """First stripped line after Summary: starting with *prefix* -> float."""
    for line in lines:
        s = line.strip()
        if s.startswith(prefix):
            token = s[len(prefix):].split()[0] if len(s) > len(prefix) else ''
            try:
                return float(token)
            except ValueError:
                return None
    return None


def parse_ebur128(stderr_text: str) -> TrackLoudness:
    """Parse FFmpeg ebur128 stderr output into a TrackLoudness."""
    frames = []
    for line in stderr_text.splitlines():
        match = _FRAME_RE.search(line)
        if match:
            frames.append((float(match.group(1)), match.group(2)))

    values = tuple(_m_value(tok) for _, tok in frames)
    duration = frames[-1][0] if frames else 0.0
    head = values[:EDGE_FRAMES]
    tail = values[-EDGE_FRAMES:]

    summary_at = None
    for i, line in enumerate(stderr_text.splitlines()):
        if 'Summary:' in line:
            summary_at = i + 1
            break
    if summary_at is None:
        raise ValueError('no ebur128 Summary block in output')

    rest = stderr_text.splitlines()[summary_at:]
    if not any(line.strip().startswith('I:') for line in rest):
        raise ValueError('no integrated loudness (I:) in ebur128 Summary')
    integrated = _summary_value(rest, 'I:')
    if integrated is None or integrated != integrated or integrated <= SILENCE_LUFS:
        integrated = SILENCE_LUFS

    peak = _summary_value(rest, 'Peak:')
    if peak is None or peak != peak or peak == float('-inf'):
        peak = -120.0

    return TrackLoudness(
        integrated_lufs=integrated,
        true_peak_dbtp=peak,
        duration_sec=duration,
        head=head,
        tail=tail,
    )


def leveling_gain_db(integrated_lufs: float, true_peak_dbtp: float, *,
                     target: float = TARGET_LUFS,
                     ceiling: float = PEAK_CEILING_DBTP) -> float:
    """Gain (dB) to reach *target* loudness, limited so true peak stays
    under *ceiling*.  Silence gets no gain."""
    if integrated_lufs <= SILENCE_LUFS:
        return 0.0
    return round(min(target - integrated_lufs, ceiling - true_peak_dbtp), 1)


def album_gain_db(songs, *, target: float = TARGET_LUFS,
                  ceiling: float = PEAK_CEILING_DBTP) -> float:
    """Duration-weighted energy mean gain for a whole album, silence left
    out, so the album keeps its own quiet and loud songs."""
    kept = [s for s in songs
            if s.integrated_lufs > SILENCE_LUFS and s.duration_sec > 0]
    if not kept:
        return 0.0
    total_energy = sum(s.duration_sec * 10 ** (s.integrated_lufs / 10)
                       for s in kept)
    total_duration = sum(s.duration_sec for s in kept)
    l = 10 * math.log10(total_energy / total_duration)
    peak = max(s.true_peak_dbtp for s in kept)
    return leveling_gain_db(l, peak, target=target, ceiling=ceiling)


def cache_key(path: str, size: int, mtime: float) -> str:
    """Stable cache key: normalised path + size + whole-second mtime."""
    return (os.path.normcase(os.path.normpath(path or ""))
            + '|' + str(size) + '|' + str(int(mtime)))


def to_record(t: TrackLoudness) -> dict:
    """Compact JSON record: ints in tenths of a dB, lists not tuples."""
    return {
        'i': round(t.integrated_lufs * 10),
        'p': round(t.true_peak_dbtp * 10),
        'd': round(t.duration_sec, 1),
        'h': [round(v * 10) for v in t.head],
        't': [round(v * 10) for v in t.tail],
    }


def from_record(rec) -> 'TrackLoudness | None':
    """Inverse of to_record; None for malformed records."""
    if not isinstance(rec, dict):
        return None
    if not all(k in rec for k in ('i', 'p', 'd', 'h', 't')):
        return None
    i, p, d, h, tt = rec['i'], rec['p'], rec['d'], rec['h'], rec['t']
    if isinstance(i, bool) or not isinstance(i, int):
        return None
    if isinstance(p, bool) or not isinstance(p, int):
        return None
    if isinstance(d, bool) or not isinstance(d, (int, float)):
        return None
    for seq in (h, tt):
        if not isinstance(seq, list):
            return None
        if any(isinstance(v, bool) or not isinstance(v, int) for v in seq):
            return None
    return TrackLoudness(
        integrated_lufs=i / 10.0,
        true_peak_dbtp=p / 10.0,
        duration_sec=float(d),
        head=tuple(v / 10.0 for v in h),
        tail=tuple(v / 10.0 for v in tt),
    )


def load_cache(path: str) -> dict:
    """Load the loudness cache; {} for missing / corrupt / wrong-version."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get('version') != CACHE_VERSION:
        return {}
    tracks = data.get('tracks')
    if not isinstance(tracks, dict):
        return {}
    return tracks


def save_cache(path: str, tracks: dict) -> None:
    """Atomically write the loudness cache."""
    payload = json.dumps({'version': CACHE_VERSION, 'tracks': tracks},
                         separators=(',', ':'))
    tmp = f"{path}.tmp"
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(payload)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
