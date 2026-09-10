"""
shuffle_core.py - Custom shuffle algorithms (standalone, no GUI dependencies)

Provides CustomShuffleAlgorithm with:
  - smart_shuffle: bucketed stride-interleave (O(n log k) in unique artists).
    Tracks are bucketed by artist and each bucket is ordered by an exponential
    race on its preference weight; buckets are then interleaved by a
    random-phase stride schedule with a hard per-artist cooldown, a hard
    consecutive-run cap, and a soft album/genre tie-break.
  - truly_random: standard random shuffle

Scoring modes (config['scoring_mode']):
  - 'legacy'   : the historical additive preference score (default)
  - 'weighted' : scale-free Boltzmann weights, strength via config['beta']

Both modes share the interleave; they differ only in the per-track weight.
"""

import heapq
import math
import os
import random
import time
from collections import defaultdict, deque

from playback_feedback import decayed_skip_aversion as _decayed_skip_aversion
from playback_feedback import skip_signal as _skip_signal

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

    # Listening feedback (Task 8): confidence-adjusted skip signal + a
    # temporary post-skip aversion that decays over ~a week.
    'feedback_skip_weight':  0.40,
    'feedback_decay_weight': 0.20,
}

_WINDOW_KEYS  = ('recent_artists', 'recent_albums', 'recent_genres')
_WEIGHT_KEYS  = ('random_min', 'random_range', 'rating_weight', 'novelty_weight',
                 'recency_weight', 'newness_weight', 'loved_weight',
                 'skip_penalty_weight', 'artist_skip_weight', 'bpm_bonus',
                 'history_track_penalty', 'history_artist_penalty', 'beta',
                 'feedback_skip_weight', 'feedback_decay_weight')
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
    # None / unknown -> neutral (Task 7).  A track you have never had an
    # opinion about should not be scored the same as one you actively unfaved.
    if loved is None:
        return 0.35
    return 1.0 if loved else 0.0


# Weighted mode: favorite is the taste axis, numeric rating drops out (Task 7b).
WEIGHTED_LOVED_WEIGHT = 0.85


def weighted_score_cfg(cfg):
    """The config `smart_shuffle` scores against in 'weighted' mode - one place
    so the diagnostic and the shuffle can't disagree."""
    return {**cfg, 'rating_weight': 0.0, 'loved_weight': WEIGHTED_LOVED_WEIGHT}


# The feedback penalty alone can never exceed this, so a disliked track is
# demoted, never parked-forever (see the relative race-weight floor below).
_FEEDBACK_PENALTY_CAP = 0.50


def _feedback_penalty(track, cfg, now):
    """Learned dislike from actual listening outcomes (Task 8).

    Uses versioned counters when present; contributes nothing for a track with
    no new observations (backs off to the rest of the score).
    """
    qp = getattr(track, 'qualified_play_count', None)
    ms = getattr(track, 'manual_skip_count', None)
    if not qp and not ms:
        return 0.0
    pen = cfg['feedback_skip_weight'] * _skip_signal(int(ms or 0), int(qp or 0))
    last = getattr(track, 'last_skipped_at', None)
    if last is not None:
        try:
            pen += cfg['feedback_decay_weight'] * _decayed_skip_aversion(float(last), now)
        except (TypeError, ValueError):
            pass
    return min(pen, _FEEDBACK_PENALTY_CAP)


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
    w -= _feedback_penalty(track, cfg, now)

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


def explain_preference_score(track, config=None, *, now=None, scoring_mode=None):
    """Human-readable breakdown of a track's deterministic preference score.

    Returns a dict: {'mode', 'score', 'components': [(label, value), ...],
    'feedback', 'notes': [...]}.  Used by the diagnostic tool so it can never
    drift from the real scorer (Task 9).
    """
    cfg = normalize_shuffle_config(config)
    if now is None:
        now = time.time()
    mode = scoring_mode or cfg['scoring_mode']
    wcfg = weighted_score_cfg(cfg) if mode == 'weighted' else cfg

    bpm_on = (getattr(track, 'bpm', 0) or 0) > 0
    comps = [
        ('rating',   wcfg['rating_weight']  * _norm_rating(getattr(track, 'rating', None))),
        ('novelty',  wcfg['novelty_weight'] * _novelty_score(getattr(track, 'play_count', None))),
        ('recency',  wcfg['recency_weight'] * _recency_boost(getattr(track, 'last_played', None), now)),
        ('newness',  wcfg['newness_weight'] * _newness_boost(getattr(track, 'date_added', None), now)),
        ('favorite', wcfg['loved_weight']   * _love_score(getattr(track, 'loved', None))),
        ('bpm_bonus', wcfg['bpm_bonus'] if bpm_on else 0.0),
        ('skip_penalty(legacy)', -wcfg['skip_penalty_weight'] * _skip_penalty(track)),
        ('feedback_penalty',     -_feedback_penalty(track, wcfg, now)),
    ]
    score = legacy_preference_score(track, wcfg, now=now)
    notes = []
    if mode == 'weighted':
        notes.append(f"weighted mode: numeric rating is ignored; "
                     f"favorite weight = {WEIGHTED_LOVED_WEIGHT}")
    else:
        notes.append("legacy mode: numeric rating still contributes")
    notes.append("artist-skip-rate and the recent-history guard also affect the "
                 "score but need the whole library, so they are not shown here")
    notes.append("final playlist position also depends on artist stride + a "
                 "per-seed random race, so rank varies run to run")
    return {
        'mode': mode,
        'score': score,
        'components': comps,
        'feedback': {
            'qualified_play_count': getattr(track, 'qualified_play_count', None),
            'manual_skip_count': getattr(track, 'manual_skip_count', None),
            'last_skipped_at': getattr(track, 'last_skipped_at', None),
        },
        'notes': notes,
    }


def _boltzmann_weights(scores, beta):
    """Scale-free race weights for ``weighted`` mode: w = exp(beta * clamp(z)).

    ``scores`` is a list (one entry per track, by index); returns a list the
    same length.  Floored at 2% of the median so nothing is effectively
    excluded.  ``beta = 0`` -> flat weights (uniform within the stride schedule).
    """
    mean = sum(scores) / len(scores)
    std = math.sqrt(sum((s - mean) ** 2 for s in scores) / len(scores)) or 1.0
    raw = [math.exp(beta * max(-3.0, min(3.0, (s - mean) / std))) for s in scores]
    floor = 0.02 * sorted(raw)[len(raw) // 2]
    return [max(w, floor) for w in raw]


class CustomShuffleAlgorithm:
    """Custom shuffle algorithms for music playback"""

    @staticmethod
    def smart_shuffle(tracks, config=None, recent_history=None, *,
                      rng=None, now=None, context=None):
        """
        Bucketed stride-interleave shuffle - O(n log k) in unique artists,
        well under a second on 15K+ track libraries at any artist skew.

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
        context : dict, optional
            Extra playback context.  Recognised keys:
              ``protected_paths``    - iterable of normalised filepaths to keep
                                       out of the first ``protect_positions``
                                       result slots (degrades gracefully when
                                       the pool is too small).
              ``protect_positions``  - int, default 3.
        """
        if not tracks:
            return []

        cfg = normalize_shuffle_config(config)
        if rng is None:
            rng = random
        if now is None:
            now = time.time()

        n = len(tracks)
        scoring_mode = cfg['scoring_mode']

        # ---- Per-track (artist, album, genre) keys, indexed by position -------
        pk = []
        for t in tracks:
            a   = (getattr(t, 'artist', '') or "").strip().lower()
            ak  = a if a and a not in UNKNOWN_ARTIST else None

            alb  = (getattr(t, 'album', '') or "").strip().lower()
            albk = f"{ak}||{alb}" if alb and alb not in UNKNOWN_ALBUM else None

            g   = (getattr(t, 'genre', '') or "").strip().lower()
            gk  = g if g and g not in UNKNOWN_GENRE else None

            pk.append((ak, albk, gk))

        # ---- History guard sets (Task 5) -------------------------------------
        recent_track_set  = set()
        recent_artist_set = set()
        guard_size = cfg['history_guard_size']
        if recent_history and guard_size > 0:   # 0 explicitly disables the guard
            recent_n = recent_history[-guard_size:]
            recent_track_set  = {os.path.normcase(os.path.normpath(fp)) for fp, _ in recent_n}
            recent_artist_set = {ak for _, ak in recent_n if ak is not None}

        # ---- Artist-level skip-rate aggregation ------------------------------
        artist_plays = defaultdict(int)
        artist_skips = defaultdict(int)
        for i, t in enumerate(tracks):
            ak = pk[i][0]
            if ak is None:
                continue
            artist_plays[ak] += getattr(t, 'play_count', 0) or 0
            artist_skips[ak] += getattr(t, 'skips', 0) or 0
        artist_skip_rate = {
            ak: math.tanh(artist_skips.get(ak, 0) / (plays + 1.0))
            for ak, plays in artist_plays.items()
        }

        # ---- Deterministic preference score per track (by index) -------------
        # In 'weighted' mode the taste axis is the favorite, not numeric rating
        # (Task 7b): rating drops out, favorite weight goes up.
        score_cfg = weighted_score_cfg(cfg) if scoring_mode == 'weighted' else cfg
        scores = [
            legacy_preference_score(
                t, score_cfg, now=now,
                artist_skip_rate=artist_skip_rate,
                recent_tracks=recent_track_set,
                recent_artists=recent_artist_set)
            for t in tracks
        ]

        if scoring_mode == 'weighted':
            race_weight = _boltzmann_weights(scores, cfg['beta'])
        else:
            # Relative floor (like weighted mode) so a heavily-penalised track
            # is demoted, never effectively excluded - otherwise a
            # consistently-skipped track parks at the end of every shuffle and
            # can never earn the qualified plays that would rehabilitate it.
            _pos = [s for s in scores if s > 0]
            _floor = 0.02 * (sorted(_pos)[len(_pos) // 2] if _pos else 1.0)
            race_weight = [max(s, _floor) for s in scores]

        # Cross-bucket bias: a fraction of a bucket's stride, bounded by tanh so
        # phase randomness (spread == stride) can always overcome it - this is
        # what stops the old rigid preference bands.
        smean = sum(scores) / n
        sstd = math.sqrt(sum((s - smean) ** 2 for s in scores) / n) or 1.0
        if scoring_mode == 'weighted':
            pref_bias = 0.40 * min(1.0, cfg['beta'] / 2.0)   # beta 0 -> no bias
        else:
            pref_bias = 0.40

        def _pref_shift(idx):
            z = max(-3.0, min(3.0, (scores[idx] - smean) / sstd))
            return pref_bias * math.tanh(z)

        # ---- Bucket track indices by artist; unknown -> own singleton bucket -
        buckets = {}
        for i in range(n):
            ak = pk[i][0]
            buckets.setdefault(ak if ak is not None else f"\x00u\x00{i}", []).append(i)

        # Order each bucket by an exponential race (lower key = earlier).
        for key, idxs in buckets.items():
            idxs.sort(key=lambda i: -math.log(1.0 - rng.random()) / race_weight[i])
            buckets[key] = deque(idxs)

        cd_ceiling = cfg['recent_artists']
        if cfg['adaptive_constraints']:
            cd_ceiling = max(cd_ceiling, 3)
        max_run = max(2, cd_ceiling)

        meta = {}
        for key, dq in buckets.items():
            total = len(dq)
            meta[key] = {
                'stride':     n / total,
                'pass':       (n / total) * rng.random(),   # random phase
                'cd':         min(cd_ceiling, n // total - 1),
                'cool_until': -1,
            }

        # Light cooldown seed from the recent-history tail (Task 3/4 replaces
        # this with real fixed-prefix seeding).
        if recent_history and cd_ceiling:
            for back, entry in enumerate(reversed(recent_history[-cd_ceiling:])):
                m = meta.get(entry[1])
                if m is not None:
                    m['cool_until'] = max(m['cool_until'], m['cd'] - (back + 1))

        # Soft album/genre spacing: a small nudge applied at selection time
        # (never a hard gate - a hard gate reintroduces the O(n*window) cliff).
        recent_alb = deque(maxlen=max(1, cfg['recent_albums']))
        recent_gen = deque(maxlen=max(1, cfg['recent_genres']))

        def _avail_entry(key):
            head = buckets[key][0]
            priority = meta[key]['pass'] - meta[key]['stride'] * _pref_shift(head)
            return (priority, -scores[head], -len(buckets[key]), key)

        def _soft_cost(key):
            _, albk, gk = pk[buckets[key][0]]
            return ((albk is not None and albk in recent_alb)
                    + (gk is not None and gk in recent_gen))

        avail, cooling = [], []
        for key, m in meta.items():
            if m['cool_until'] >= 0:
                heapq.heappush(cooling, (m['cool_until'], key))
            else:
                heapq.heappush(avail, _avail_entry(key))

        result = []
        run_key, run_len = None, 0
        for pos in range(n):
            while cooling and cooling[0][0] <= pos:
                _, k = heapq.heappop(cooling)
                if buckets[k]:
                    heapq.heappush(avail, _avail_entry(k))

            if avail:
                # Pop up to 4 near-best candidates; among them prefer the one
                # whose album/genre was least recently emitted (soft tie-break).
                cand = [heapq.heappop(avail) for _ in range(min(4, len(avail)))]
                pick = min(range(len(cand)),
                           key=lambda j: cand[j][0] + _soft_cost(cand[j][3]))
                key = cand[pick][3]
                for j, entry in enumerate(cand):
                    if j != pick:
                        heapq.heappush(avail, entry)
            else:                                   # fallback: never stall
                key = heapq.heappop(cooling)[1]

            # Hard run cap: never extend a same-artist run past max_run while
            # any alternative bucket still has tracks.
            if key == run_key and run_len >= max_run and (avail or cooling):
                deferred = key
                if avail:
                    key = heapq.heappop(avail)[3]
                else:
                    # Force-release from cooldown: an intentional cooldown
                    # bypass, justified because the run cap outranks spacing.
                    key = heapq.heappop(cooling)[1]
                heapq.heappush(avail, _avail_entry(deferred))

            idx = buckets[key].popleft()
            result.append(tracks[idx])
            run_len = run_len + 1 if key == run_key else 1
            run_key = key
            if pk[idx][1] is not None:
                recent_alb.append(pk[idx][1])
            if pk[idx][2] is not None:
                recent_gen.append(pk[idx][2])

            m = meta[key]
            m['pass'] += m['stride']
            if buckets[key]:
                if m['cd'] > 0:
                    m['cool_until'] = pos + 1 + m['cd']
                    heapq.heappush(cooling, (m['cool_until'], key))
                else:
                    heapq.heappush(avail, _avail_entry(key))

        # ---- Start protection (Task 3) --------------------------------------
        if context:
            protected = {os.path.normcase(os.path.normpath(p))
                         for p in context.get('protected_paths') or ()}
            k = min(int(context.get('protect_positions', 3)), n)
            if protected:
                swap_from = k
                for i in range(k):
                    fp = os.path.normcase(os.path.normpath(
                        getattr(result[i], 'filepath', '') or ''))
                    if fp not in protected:
                        continue
                    # find the earliest later slot holding a non-protected track
                    while swap_from < n:
                        cand = os.path.normcase(os.path.normpath(
                            getattr(result[swap_from], 'filepath', '') or ''))
                        if cand not in protected:
                            break
                        swap_from += 1
                    if swap_from >= n:
                        break   # whole pool is protected - degrade gracefully
                    result[i], result[swap_from] = result[swap_from], result[i]
                    swap_from += 1

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
