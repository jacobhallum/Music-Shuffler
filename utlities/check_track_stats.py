"""
Track Statistics Diagnostic (Task 9)

Why does a track rank where it does in Smart Shuffle?  This reads the SAME
player metadata and shuffle config the app uses, and the SAME scoring code
(shuffle_core.explain_preference_score) - it never copies the formulas, so it
can't drift.

    python utlities/check_track_stats.py "<path or filename fragment>"
"""

import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import plistlib
from shuffle_core import explain_preference_score, normalize_shuffle_config


def _cache_dir():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "music_shuffler_cache")


def _load_config():
    path = os.path.join(_cache_dir(), "shuffle_config.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return normalize_shuffle_config(json.load(f))
    except (OSError, ValueError):
        return normalize_shuffle_config(None)


def _load_player_metadata():
    path = os.path.join(_cache_dir(), "music_player_metadata.xml")
    try:
        with open(path, "rb") as f:
            data = plistlib.load(f)
        return data.get("tracks", {}), int(data.get("schema_version", 1))
    except (OSError, ValueError):
        return {}, 0


class _Track:
    """Minimal track-like object for the scorer."""
    def __init__(self, key, md):
        self.filepath = key
        self.artist = md.get("artist", "")
        self.album = md.get("album", "")
        self.genre = md.get("genre", "")
        self.rating = md.get("rating", 0) or 0
        self.play_count = md.get("play_count", 0) or 0
        self.skips = md.get("skip_count")
        self.loved = md.get("loved")
        self.last_played = md.get("last_played")
        self.date_added = md.get("date_added")
        self.bpm = md.get("bpm", 0) or 0
        self.qualified_play_count = md.get("qualified_play_count")
        self.manual_skip_count = md.get("manual_skip_count")
        self.last_skipped_at = md.get("last_skipped_at")


def _fmt_ts(v):
    if not v:
        return "never"
    try:
        return datetime.fromtimestamp(float(v)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return str(v)


def report(fragment):
    tracks, schema = _load_player_metadata()
    cfg = _load_config()
    now = time.time()

    frag = fragment.lower()
    matches = [(k, v) for k, v in tracks.items() if frag in k.lower()]
    if not matches:
        print(f"No track in the player metadata matched {fragment!r}.")
        print(f"  (metadata file: {os.path.join(_cache_dir(), 'music_player_metadata.xml')})")
        return
    if len(matches) > 8:
        print(f"{len(matches)} matches - narrow the fragment. First few:")
        for k, _ in matches[:8]:
            print(f"  {k}")
        return

    for key, md in matches:
        t = _Track(key, md)
        info = explain_preference_score(t, cfg, now=now)

        print("=" * 72)
        print(key)
        print("=" * 72)
        print(f"  play_count {t.play_count}   legacy_skips {t.skips}   "
              f"rating {t.rating}   loved {t.loved} ({md.get('loved_source')})")
        print(f"  last_played {_fmt_ts(t.last_played)}   date_added {_fmt_ts(t.date_added)}")
        fb = info["feedback"]
        print(f"  qualified_plays {fb['qualified_play_count']}   "
              f"manual_skips {fb['manual_skip_count']}   "
              f"last_skipped {_fmt_ts(fb['last_skipped_at'])}")
        print()
        print(f"  scoring mode: {info['mode']}   config schema v{schema}")
        print(f"  deterministic preference score: {info['score']:.4f}")
        print("  component contributions:")
        for label, val in info["components"]:
            print(f"    {label:<24} {val:+.4f}")
        print()
        for note in info["notes"]:
            print(f"  note: {note}")
        print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    report(sys.argv[1])
