"""
scripts/benchmark_shuffle.py - Runtime and quality benchmark for shuffle_core

Generates synthetic track objects (no GUI dependencies) and measures, per size:
  - Median / p95 wall-clock time for smart_shuffle (interpolated percentile)
  - Adjacent same-artist and same-album repeat counts
  - Longest same-artist run
  - Repeated-session first-20 overlap and unique first-20 coverage

Fixture metadata is seeded *before* generation and independently of the
shuffle seed, so runs are reproducible and comparable.

Usage:
    python scripts/benchmark_shuffle.py
    python scripts/benchmark_shuffle.py --sizes 1000 5000 15000 --iters 10
    python scripts/benchmark_shuffle.py --skew 0.7 --sizes 1000 8000 15000 --iters 10
"""

import argparse
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from shuffle_core import CustomShuffleAlgorithm

FIXED_NOW = 1_700_000_000.0
FIXTURE_SEED = 916237  # fixed, independent of the shuffle seed


# ---------------------------------------------------------------------------
# Synthetic track factory (no MusicTrack / GUI imports)
# ---------------------------------------------------------------------------

class _FakeTrack:
    __slots__ = ('filepath', 'artist', 'album', 'genre',
                 'play_count', 'skips', 'rating', 'loved',
                 'last_played', 'date_added', 'bpm')


def _make_tracks(n, skew=0.0, fixture_seed=FIXTURE_SEED):
    """Generate *n* synthetic tracks.

    skew : float in [0, 1)
        Fraction of the library assigned to a single dominant artist.  0.0 =
        balanced (n // 8 artists).  0.7 = 70% one artist, remainder spread.
    """
    r = random.Random(fixture_seed)
    n_artists = max(1, n // 8)
    n_albums  = max(1, n // 20)
    n_genres  = max(1, min(20, n // 50))
    dominant_cut = int(n * skew)

    tracks = []
    for i in range(n):
        t = _FakeTrack()
        t.filepath = f"/synthetic/track_{i:06d}.mp3"
        if i < dominant_cut:
            t.artist = "Artist_DOMINANT"
        else:
            t.artist = f"Artist_{i % n_artists}"
        t.album       = f"Album_{i % n_albums}"
        t.genre       = f"Genre_{i % n_genres}"
        t.play_count  = r.randint(0, 50)
        t.skips       = r.randint(0, t.play_count // 2 + 1)
        t.rating      = r.choice([0, 20, 40, 60, 80, 100])
        t.loved       = r.choice([True, False, None])
        t.last_played = (FIXED_NOW - r.uniform(0, 365 * 86400)
                         if r.random() > 0.2 else None)
        t.date_added  = FIXED_NOW - r.uniform(0, 3 * 365 * 86400)
        t.bpm         = r.choice([0, 0, 0, 120, 128, 140])
        tracks.append(t)
    return tracks


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _adjacent_repeats(result, key_fn):
    return sum(
        1 for i in range(1, len(result))
        if key_fn(result[i]) == key_fn(result[i - 1]) and key_fn(result[i])
    )


def _longest_run(result, key_fn):
    longest = run = 0
    prev = object()
    for t in result:
        k = key_fn(t)
        if k == prev and k:
            run += 1
        else:
            run = 1
        prev = k
        longest = max(longest, run)
    return longest


def _theoretical_min_adjacent(counts):
    """Lower bound on adjacent same-key repeats given key multiplicities."""
    total = sum(counts)
    hi = max(counts)
    rest = total - hi
    return max(0, hi - rest - 1)


def _percentile(sorted_vals, pct):
    """Linear-interpolated percentile (numpy 'linear' method)."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = (pct / 100) * (len(sorted_vals) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return sorted_vals[int(rank)]
    frac = rank - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _session_metrics(tracks, config, n_sessions=50, head=20, shuffle_seed=1000):
    """First-`head` overlap between sessions and unique coverage across them."""
    # Each session is a full shuffle; scale down for very large libraries so a
    # 15K benchmark run does not pay 50x its per-iter cost here.
    if len(tracks) > 6000:
        n_sessions = 20
    firsts = []
    for s in range(n_sessions):
        rng = random.Random(shuffle_seed + s)
        out = CustomShuffleAlgorithm.smart_shuffle(
            list(tracks), config, rng=rng, now=FIXED_NOW)
        firsts.append({id(t) for t in out[:head]})

    overlaps = [
        len(firsts[i] & firsts[j])
        for i in range(n_sessions) for j in range(i + 1, n_sessions)
    ]
    unique = len(set().union(*firsts)) if firsts else 0
    mean_overlap = sum(overlaps) / len(overlaps) if overlaps else 0.0
    return mean_overlap, unique


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def run_benchmark(sizes, iters, skew=0.0, config=None):
    label = f"skew={skew}" if skew else "balanced"
    print(f"\n{'='*64}")
    print(f"Shuffle benchmark  |  {iters} iter/size  |  {label}")
    print(f"{'='*64}")

    for n in sizes:
        tracks = _make_tracks(n, skew=skew)
        artist_counts = {}
        for t in tracks:
            artist_counts[t.artist] = artist_counts.get(t.artist, 0) + 1
        tmin = _theoretical_min_adjacent(list(artist_counts.values()))

        print(f"\n--- {n:,} tracks | {len(artist_counts)} artists "
              f"| max artist share {max(artist_counts.values())/n:.0%} "
              f"| theoretical min adjacent {tmin} ---")

        times, art_rep, alb_rep, runs = [], [], [], []
        for it in range(iters):
            rng = random.Random(it)
            t0 = time.perf_counter()
            result = CustomShuffleAlgorithm.smart_shuffle(
                list(tracks), config, rng=rng, now=FIXED_NOW)
            times.append(time.perf_counter() - t0)
            art_rep.append(_adjacent_repeats(result, lambda t: t.artist))
            alb_rep.append(_adjacent_repeats(result, lambda t: t.album))
            runs.append(_longest_run(result, lambda t: t.artist))

        times.sort()
        art_rep.sort(); alb_rep.sort(); runs.sort()
        mid = len(times) // 2
        print(f"  time   median {_percentile(times, 50)*1000:7.1f} ms   "
              f"p95 {_percentile(times, 95)*1000:7.1f} ms")
        print(f"  artist adjacent median {art_rep[mid]:5d}   "
              f"longest run median {runs[mid]:5d}   (min {tmin})")
        print(f"  album  adjacent median {alb_rep[mid]:5d}")

        mean_ov, uniq = _session_metrics(tracks, config)
        print(f"  sessions: mean first-20 overlap {mean_ov:.2f}/20   "
              f"unique first-20 coverage {uniq}/{n}")

    print(f"\n{'='*64}\n")


def _parse_args():
    p = argparse.ArgumentParser(description="Benchmark shuffle_core.smart_shuffle")
    p.add_argument('--sizes', nargs='+', type=int, default=[1000, 5000, 15000],
                   metavar='N', help='Library sizes (default: 1000 5000 15000)')
    p.add_argument('--iters', type=int, default=5,
                   help='Iterations per size (default: 5)')
    p.add_argument('--skew', type=float, default=0.0,
                   help='Dominant-artist share in [0, 1) (default: 0.0 balanced)')
    return p.parse_args()


if __name__ == '__main__':
    args = _parse_args()
    run_benchmark(args.sizes, args.iters, skew=args.skew)
