"""
shuffle_core.py - Custom shuffle algorithms (standalone, no GUI dependencies)

Provides CustomShuffleAlgorithm with:
  - smart_shuffle: Draft + Repair (O(n log n)) with adaptive spacing and history guard
  - truly_random: Standard random shuffle

New in v2:
  - adaptive_constraints: auto-scales spacing windows from library cardinality
  - recent_history guard: penalises recently-played tracks/artists in weight phase
  - Precomputed per-track keys: eliminates repeated string normalisation in repair pass
"""

import math
import os
import random
import time
from collections import defaultdict, deque

CONFIG_VERSION = 2

# Single source of truth for shuffle defaults (music_player imports these).
DEFAULT_SHUFFLE_CONFIG = {
    'scoring_mode':          'legacy',   # 'legacy' | 'weighted'
    'beta':                  1.0,        # weighted-mode preference strength (0-3)

    'random_min':            0.20,
    'random_range':          0.50,
    'rating_weight':         0.55,
    'novelty_weight':        0.55,
    'recency_weight':        0.45,
    'newness_weight':        0.20,
    'loved_weight':          0.30,
    'skip_penalty_weight':   0.40,
    'artist_skip_weight':    0.10,
    'bpm_bonus':             0.05,

    'recent_artists':        3,
    'recent_albums':         2,
    'recent_genres':         2,
    'lookahead':             400,
    'adaptive_constraints':  True,

    'history_guard_size':    30,
    'history_track_penalty': 0.35,
    'history_artist_penalty': 0.15,
}

_WINDOW_KEYS  = ('recent_artists', 'recent_albums', 'recent_genres')
_WEIGHT_KEYS  = ('random_min', 'random_range', 'rating_weight', 'novelty_weight',
                 'recency_weight', 'newness_weight', 'loved_weight',
                 'skip_penalty_weight', 'artist_skip_weight', 'bpm_bonus',
                 'history_track_penalty', 'history_artist_penalty', 'beta')
_SCORING_MODES = ('legacy', 'weighted')
_WINDOW_MAX = 50


def _as_finite_float(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def normalize_shuffle_config(config):
    """Return a validated, clamped copy of *config* over the defaults.

    - windows -> non-negative ints, capped at 50
    - history_guard_size -> 0..200 (0 explicitly disables the guard)
    - lookahead -> 1..1000
    - weights -> finite floats >= 0 (non-finite / non-numeric fall back per field)
    - scoring_mode -> 'legacy' | 'weighted' (anything else -> 'legacy')
    - unknown keys pass through untouched (forward compatibility)
    """
    cfg = dict(DEFAULT_SHUFFLE_CONFIG)
    cfg['config_version'] = CONFIG_VERSION
    if config:
        for k, v in config.items():
            if k not in DEFAULT_SHUFFLE_CONFIG:
                cfg[k] = v  # unknown key - leave as-is

    src = config or {}

    for k in _WINDOW_KEYS:
        if k in src:
            f = _as_finite_float(src[k])
            cfg[k] = DEFAULT_SHUFFLE_CONFIG[k] if f is None \
                else max(0, min(_WINDOW_MAX, int(f)))

    if 'history_guard_size' in src:
        f = _as_finite_float(src['history_guard_size'])
        cfg['history_guard_size'] = DEFAULT_SHUFFLE_CONFIG['history_guard_size'] \
            if f is None else max(0, min(200, int(f)))

    if 'lookahead' in src:
        f = _as_finite_float(src['lookahead'])
        cfg['lookahead'] = DEFAULT_SHUFFLE_CONFIG['lookahead'] if f is None \
            else max(1, min(1000, int(f)))

    for k in _WEIGHT_KEYS:
        if k in src:
            f = _as_finite_float(src[k])
            cfg[k] = DEFAULT_SHUFFLE_CONFIG[k] if f is None else max(0.0, f)

    if 'adaptive_constraints' in src:
        cfg['adaptive_constraints'] = bool(src['adaptive_constraints'])

    if 'scoring_mode' in src:
        cfg['scoring_mode'] = src['scoring_mode'] \
            if src['scoring_mode'] in _SCORING_MODES else 'legacy'

    return cfg


# ---------------------------------------------------------------------------
# Tag-key normalisation
# ---------------------------------------------------------------------------

UNKNOWN_ARTIST = {"", "unknown artist"}
UNKNOWN_ALBUM  = {"", "unknown album"}
UNKNOWN_GENRE  = {"", "unknown"}


def _norm_artist_key(track):
    a = (getattr(track, 'artist', '') or "").strip()
    al = a.lower()
    return al if a and al not in UNKNOWN_ARTIST else None


# ---------------------------------------------------------------------------
# Deterministic scoring components (no RNG, no library aggregates)
# ---------------------------------------------------------------------------

def _norm_rating(r):
    if r is None:
        return 0.50
    try:
        r = int(r or 0)
    except Exception:
        return 0.50
    if r == 0:
        return 0.50
    if r <= 5:
        return max(0.0, min(1.0, r / 5.0))
    if r <= 100:
        return max(0.0, min(1.0, r / 100.0))
    return max(0.0, min(1.0, r / 255.0))


def _novelty_score(play_count):
    if play_count is None:
        return 0.60
    try:
        pc = int(play_count or 0)
    except Exception:
        return 0.60
    return 1.0 / (1.0 + (max(pc, 0) + 1))


def _recency_boost(last_played, now):
    if last_played is None:
        return 1.0
    try:
        lp = float(last_played)
    except Exception:
        return 1.0
    if lp <= 0:
        return 1.0
    age_days = max(0.0, (now - lp) / 86400.0)
    return 1.0 - math.exp(-age_days / 14.0)


def _newness_boost(date_added, now):
    if date_added is None:
        return 0.20
    try:
        da = float(date_added)
    except Exception:
        return 0.20
    if da <= 0:
        return 0.20
    age_days = max(0.0, (now - da) / 86400.0)
    return 1.0 / (1.0 + (age_days / 30.0))


def _skip_penalty(track):
    pc = getattr(track, 'play_count', 0) or 0
    sk = getattr(track, 'skips', 0) or 0
    if sk == 0:
        return 0.0
    return 0.4 * math.tanh(sk / (pc + 1.0))


def _love_score(loved):
    if loved is None:
        return 0.25
    return 1.0 if loved else 0.0


def legacy_preference_score(track, cfg, *, now, base=0.0,
                            artist_skip_rate=None,
                            recent_tracks=frozenset(),
                            recent_artists=frozenset()):
    """Deterministic legacy draft weight for one track (no RNG term).

    ``base`` is added first so a caller can fold in its randomness with the
    original accumulation order preserved (bit-identical legacy output).
    ``artist_skip_rate`` maps normalised artist key -> tanh skip rate.
    ``recent_tracks`` / ``recent_artists`` are the history-guard sets
    (normalised paths / normalised artist keys).
    """
    w = base
    w += cfg['rating_weight']       * _norm_rating(getattr(track, 'rating', None))
    w += cfg['novelty_weight']      * _novelty_score(getattr(track, 'play_count', None))
    w += cfg['recency_weight']      * _recency_boost(getattr(track, 'last_played', None), now)
    w += cfg['newness_weight']      * _newness_boost(getattr(track, 'date_added', None), now)
    w += cfg['loved_weight']        * _love_score(getattr(track, 'loved', None))
    w -= cfg['skip_penalty_weight'] * _skip_penalty(track)

    ak = _norm_artist_key(track)
    if ak is not None and artist_skip_rate:
        w -= cfg['artist_skip_weight'] * artist_skip_rate.get(ak, 0.0)

    if (getattr(track, 'bpm', 0) or 0) > 0:
        w += cfg['bpm_bonus']

    if recent_tracks:
        nfp = os.path.normcase(os.path.normpath(getattr(track, 'filepath', '') or ''))
        if nfp in recent_tracks:
            w -= cfg['history_track_penalty']
    if recent_artists and ak is not None and ak in recent_artists:
        w -= cfg['history_artist_penalty']

    return w


class CustomShuffleAlgorithm:
    """Custom shuffle algorithms for music playback"""

    @staticmethod
    def smart_shuffle(tracks, config=None, recent_history=None, *, rng=None, now=None):
        """
        Smart Shuffle v2 - Optimised for large libraries (15K+ tracks)

        Uses Draft + Repair algorithm:
          Phase 1: Weighted sort (O(n log n))
          Phase 2: Fix clumping (O(n))

        Parameters
        ----------
        tracks : list
            MusicTrack objects (or any objects with the expected attributes).
        config : dict, optional
            Shuffle configuration; falls back to hard-coded defaults for every key.
        recent_history : list of (norm_path, artist_key_or_None), optional
            Recently-played entries used by the history guard.  Each element is a
            2-tuple ``(normalized_filepath, artist_key_string_or_None)``.
        rng : random.Random, optional
            Random source.  When ``None`` the global :mod:`random` module is used,
            so callers that seed via :func:`random.seed` keep bit-identical
            output.  Pass an explicit ``random.Random(seed)`` for isolation from
            global state (e.g. tests, or the background shuffle thread).
        now : float, optional
            Wall-clock epoch seconds for recency/newness scoring.  Defaults to
            :func:`time.time` at call time; injectable for deterministic tests.
        """
        if not tracks:
            return []

        cfg = normalize_shuffle_config(config)

        if rng is None:
            rng = random

        # ---- Constraint windows ------------------------------------------------
        RECENT_ARTISTS = cfg['recent_artists']
        RECENT_ALBUMS  = cfg['recent_albums']
        RECENT_GENRES  = cfg['recent_genres']
        LOOKAHEAD      = cfg['lookahead']

        if now is None:
            now = time.time()

        # ---- Precompute per-track keys (Task 7: avoids repeated normalisation) --
        # Keys are stored as (artist_key, album_key, genre_key) indexed by id(t).
        _pk = {}
        for t in tracks:
            a   = (t.artist or "").strip()
            ak  = a.lower() if a and a.lower() not in UNKNOWN_ARTIST else None

            alb  = (t.album or "").strip()
            albl = alb.lower() if alb and alb.lower() not in UNKNOWN_ALBUM else None
            albk = f"{ak}||{albl}" if albl is not None else None

            g   = (getattr(t, 'genre', '') or '').strip()
            gk  = g.lower() if g and g.lower() not in UNKNOWN_GENRE else None

            _pk[id(t)] = (ak, albk, gk)

        def artist_key(t): return _pk[id(t)][0]
        def album_key(t):  return _pk[id(t)][1]
        def genre_key(t):  return _pk[id(t)][2]

        # ---- Adaptive constraint windows (Task 4) ------------------------------
        if cfg['adaptive_constraints']:
            unique_artists = len({v[0] for v in _pk.values() if v[0] is not None})
            unique_albums  = len({v[1] for v in _pk.values() if v[1] is not None})
            unique_genres  = len({v[2] for v in _pk.values() if v[2] is not None})

            adaptive_artists = min(8, max(3, round(math.sqrt(unique_artists) / 2)))
            adaptive_albums  = min(6, max(2, round(math.sqrt(unique_albums)  / 3)))
            adaptive_genres  = min(4, max(1, round(math.sqrt(unique_genres)  / 4)))

            RECENT_ARTISTS = max(RECENT_ARTISTS, adaptive_artists)
            RECENT_ALBUMS  = max(RECENT_ALBUMS,  adaptive_albums)
            RECENT_GENRES  = max(RECENT_GENRES,  adaptive_genres)

        # ---- History guard sets (Task 5) ----------------------------------------
        recent_track_set  = set()
        recent_artist_set = set()
        guard_size = cfg['history_guard_size']
        if recent_history and guard_size > 0:   # 0 explicitly disables the guard
            recent_n = recent_history[-guard_size:] if len(recent_history) > guard_size else recent_history
            # Normalise stored paths so comparisons work cross-platform (Windows
            # normcase converts forward slashes → backslashes and lowercases).
            recent_track_set  = {os.path.normcase(os.path.normpath(fp)) for fp, _ in recent_n}
            recent_artist_set = {ak for _, ak in recent_n if ak is not None}

        # ---- Artist-level skip rate aggregation --------------------------------
        artist_plays = defaultdict(int)
        artist_skips = defaultdict(int)
        for t in tracks:
            ak = artist_key(t)
            if ak is None:
                continue
            artist_plays[ak] += getattr(t, 'play_count', 0) or 0
            artist_skips[ak] += getattr(t, 'skips', 0) or 0

        artist_skip_rate = {
            ak: math.tanh((artist_skips.get(ak, 0)) / (plays + 1.0))
            for ak, plays in artist_plays.items()
        }

        # ---- PHASE 1: DRAFT (compute weight once per track, then sort) ----------
        random_min   = cfg['random_min']
        random_range = cfg['random_range']

        def _compute_weight(t):
            base = random_min + rng.random() * random_range
            w = legacy_preference_score(
                t, cfg, now=now, base=base,
                artist_skip_rate=artist_skip_rate,
                recent_tracks=recent_track_set,
                recent_artists=recent_artist_set)
            return max(w, 0.001)

        # Weighted sort  O(n log n)
        weighted_tracks = [(-_compute_weight(t), t) for t in tracks]
        weighted_tracks.sort(key=lambda x: x[0])
        draft = [t for _, t in weighted_tracks]

        # ---- PHASE 2: REPAIR (fix clumping by swapping) -------------------------
        result         = []
        recent_artists = deque(maxlen=RECENT_ARTISTS)
        recent_albums  = deque(maxlen=RECENT_ALBUMS)
        recent_genres  = deque(maxlen=RECENT_GENRES)

        i = 0
        while i < len(draft):
            track = draft[i]

            def passes_constraints(t, relax_level):
                """
                0: all constraints enforced
                1: relax genre
                2: relax album
                3: accept anything
                """
                if relax_level >= 3:
                    return True
                ak  = artist_key(t)
                al  = album_key(t)
                gk  = genre_key(t)
                if ak is not None and ak in recent_artists:
                    return False
                if relax_level < 2 and al is not None and al in recent_albums:
                    return False
                if relax_level < 1 and gk is not None and gk in recent_genres:
                    return False
                return True

            placed = False
            for relax_level in range(4):
                if passes_constraints(track, relax_level):
                    result.append(track)
                    ak = artist_key(track)
                    al = album_key(track)
                    gk = genre_key(track)
                    if ak is not None:
                        recent_artists.append(ak)
                    if al is not None:
                        recent_albums.append(al)
                    if gk is not None:
                        recent_genres.append(gk)
                    i += 1
                    placed = True
                    break

                swap_found = False
                for j in range(i + 1, min(len(draft), i + 1 + LOOKAHEAD)):
                    if passes_constraints(draft[j], relax_level):
                        draft[i], draft[j] = draft[j], draft[i]
                        track = draft[i]
                        swap_found = True
                        break

                if swap_found:
                    continue

            if not placed:
                result.append(track)
                ak = artist_key(track)
                al = album_key(track)
                gk = genre_key(track)
                if ak is not None:
                    recent_artists.append(ak)
                if al is not None:
                    recent_albums.append(al)
                if gk is not None:
                    recent_genres.append(gk)
                i += 1

        return result

    @staticmethod
    def truly_random(tracks, *, rng=None):
        """Standard random shuffle.

        ``rng`` behaves as in :meth:`smart_shuffle` - ``None`` uses the global
        :mod:`random` module for backward-compatible output.
        """
        if rng is None:
            rng = random
        shuffled = list(tracks)
        rng.shuffle(shuffled)
        return shuffled
