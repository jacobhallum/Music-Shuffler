"""
scripts/shuffle_probes.py - Reproducible measurements behind the Smart Shuffle
enhancement plan (docs/plans/2026-09-09-smart-shuffle-enhancements.md).

Every headline number in that plan comes from here.  Run:

    python scripts/shuffle_probes.py            # all probes
    python scripts/shuffle_probes.py two_artist skew_collapse

No GUI / third-party deps.  Uses the injected-rng seam so results are stable.
"""

import math
import os
import random
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from shuffle_core import CustomShuffleAlgorithm as C

NOW = 1_700_000_000.0


class T:
    __slots__ = ('filepath', 'artist', 'album', 'genre', 'rating',
                 'play_count', 'last_played', 'date_added', 'loved', 'skips', 'bpm')

    def __init__(self, fp, ar, al='Al', ge='G', rating=None, pc=0, lp=None,
                 da=None, loved=None, skips=0, bpm=0):
        self.filepath = fp; self.artist = ar; self.album = al; self.genre = ge
        self.rating = rating; self.play_count = pc; self.last_played = lp
        self.date_added = da; self.loved = loved; self.skips = skips; self.bpm = bpm


def _adjacent(seq, key=lambda t: t.artist):
    return sum(1 for a, b in zip(seq, seq[1:]) if key(a) == key(b) and key(a))


def _longest_run(seq, key=lambda t: t.artist):
    longest = run = 0
    prev = object()
    for t in seq:
        k = key(t)
        run = run + 1 if (k == prev and k) else 1
        prev = k
        longest = max(longest, run)
    return longest


# ---------------------------------------------------------------------------

def two_artist():
    """Balanced two-artist library: adaptive mode vs explicit window=1."""
    def build():
        return ([T(f'/a{i}.mp3', 'A') for i in range(10)] +
                [T(f'/b{i}.mp3', 'B') for i in range(10)])

    adaptive = sum(_adjacent(C.smart_shuffle(build(), {'adaptive_constraints': True},
                                             rng=random.Random(s), now=NOW))
                   for s in range(100))
    win1 = sum(_adjacent(C.smart_shuffle(build(),
                                         {'adaptive_constraints': False, 'recent_artists': 1,
                                          'recent_albums': 0, 'recent_genres': 0},
                                         rng=random.Random(s), now=NOW))
               for s in range(100))
    print(f"two_artist  adaptive adjacent repeats / 100 seeds : {adaptive}")
    print(f"            explicit window=1                     : {win1}")


def skew_collapse():
    """70%-dominant-artist library: spacing quality and runtime."""
    for n in (1000, 4000, 8000):
        dom = int(n * 0.7)
        lib = ([T(f'/d{i}.mp3', 'Dom') for i in range(dom)] +
               [T(f'/o{i}.mp3', f'Art{i % 30}') for i in range(n - dom)])
        t0 = time.perf_counter()
        out = C.smart_shuffle(list(lib), {}, rng=random.Random(0), now=NOW)
        dt = (time.perf_counter() - t0) * 1000
        tmin = max(0, dom - (n - dom) - 1)
        print(f"skew_collapse n={n:5d}  adjacent {_adjacent(out):5d} (min {tmin})  "
              f"longest run {_longest_run(out):5d}  {dt:7.1f} ms")


def score_composition():
    """Weight budget of each scoring component (legacy formula, no random term).

    Mirrors the weights/curves in shuffle_core._compute_weight; all numbers
    derive from the helpers below so they track the core if it changes here.
    """
    W_RATING, W_NOV, W_REC, W_NEW, W_LOVE = 0.55, 0.55, 0.45, 0.20, 0.30

    def novelty(pc): return 1.0 / (2.0 + max(pc, 0))
    def recency(days):
        return 1.0 if days is None else 1.0 - math.exp(-days / 14.0)
    newness_unknown = 0.20   # _newness_boost(None)
    love_unknown = 0.25      # _love_score(None)

    # component spans (max - min contribution)
    nov_span = W_NOV * (novelty(0) - novelty(1000))
    unplayedness = nov_span + W_REC + W_NEW
    taste = W_RATING + W_LOVE

    unrated_new = (W_RATING * 0.50 + W_NOV * novelty(0) + W_REC * recency(None)
                   + W_NEW * newness_unknown + W_LOVE * love_unknown)
    loved_fav = (W_RATING * 1.00 + W_NOV * novelty(20) + W_REC * recency(1)
                 + W_NEW * newness_unknown + W_LOVE * 1.0)

    print(f"score_composition  unplayed-ness budget (nov+rec+new) : {unplayedness:.3f}")
    print(f"                   taste budget (rating+loved)         : {taste:.3f}")
    print(f"                   unrated never-played  w (no random) : {unrated_new:.3f}")
    print(f"                   loved 5star yesterday w (no random) : {loved_fav:.3f}")


def boltzmann_sweep():
    """score**strength vs scale-free exp(beta*z): top-track P(first place)."""
    r = random.Random(0)
    scores = []
    for _ in range(2000):
        rr = 0.5 if r.random() < 0.3 else r.choice([0.2, 0.4, 0.6, 0.8, 1.0])
        nov = 1.0 / (2.0 + r.randint(0, 30))
        scores.append(0.55 * rr + 0.55 * nov + 0.45 + 0.20 * 0.20 + 0.30 * 0.25)
    m, sd = statistics.mean(scores), statistics.pstdev(scores)
    for st in (1, 2, 3):
        w = [max(s, 1e-3) ** st for s in scores]
        print(f"boltzmann_sweep  score**{st}      top P(first) {max(w)/sum(w):.4f}  "
              f"({max(w)/sum(w)*len(w):.1f}x uniform)")
    for beta in (0.5, 1.0, 2.0):
        w = [math.exp(beta * (s - m) / sd) for s in scores]
        print(f"boltzmann_sweep  exp(beta={beta})  top P(first) {max(w)/sum(w):.4f}  "
              f"({max(w)/sum(w)*len(w):.1f}x uniform)")


def session_coverage():
    """Unique first-20 coverage and inter-session overlap over 50 sessions."""
    r = random.Random(0)
    lib = [T(f'/m/{i}.mp3', f'Art{i % 60}', f'Alb{i % 120}', f'G{i % 8}',
             r.choice([None, 20, 40, 60, 80, 100]), r.randint(0, 30))
           for i in range(500)]
    firsts = [[id(t) for t in C.smart_shuffle(list(lib), {}, rng=random.Random(1000 + s),
                                              now=NOW)[:20]]
              for s in range(50)]
    ov = statistics.mean(len(set(firsts[i]) & set(firsts[j]))
                         for i in range(50) for j in range(i + 1, 50))
    uniq = len({x for f in firsts for x in f})
    print(f"session_coverage  mean first-20 overlap {ov:.2f}/20   "
          f"unique first-20 {uniq}/500")


PROBES = {
    'two_artist': two_artist,
    'skew_collapse': skew_collapse,
    'score_composition': score_composition,
    'boltzmann_sweep': boltzmann_sweep,
    'session_coverage': session_coverage,
}

if __name__ == '__main__':
    wanted = sys.argv[1:] or list(PROBES)
    for name in wanted:
        if name not in PROBES:
            print(f"unknown probe: {name}  (have: {', '.join(PROBES)})")
            continue
        PROBES[name]()
        print()
