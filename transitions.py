"""
transitions.py - pure planning of song-to-song transitions (times only).

A music player can move from one song to the next in three ways when a
song ends on its own: "off" (gapless, back to back), "crossfade"
(fixed-length overlap at the end) and "smart" (looks at each song's
loudness envelope: trims silence and fades where the outgoing song tails
off).  This module only PLANS the transition: it returns times.  It plays
nothing.

plan_transition is pure and stdlib-only, so it can be unit tested in
isolation (see tests/test_transitions.py).
"""

import math
import os
from dataclasses import dataclass

from loudness import TrackLoudness

MAX_FRACTION = 0.25
MIN_OVERLAP_SEC = 0.5
SILENCE_FLOOR_LUFS = -50.0
TAIL_DROP_DB = 6.0
ABRUPT_OVERLAP_SEC = 2.0
M_WINDOW_SEC = 0.4
FRAME_SEC = 0.1


@dataclass(frozen=True)
class TransitionPlan:
    kind: str                  # 'gapless' or 'overlap'
    cue_out_sec: float         # time in the outgoing song when the incoming song starts
    overlap_sec: float         # 0.0 for gapless
    incoming_start_sec: float  # where to start decoding the incoming song
    outgoing_end_sec: float    # time in the outgoing song where it stops being heard


def _gapless(out_duration: float) -> TransitionPlan:
    """Back-to-back plan: the outgoing song is heard to its full length."""
    return TransitionPlan('gapless', out_duration, 0.0, 0.0, out_duration)


def _crossfade(seconds: int, out_duration: float, in_duration: float) -> TransitionPlan:
    """Fixed-length overlap plan, capped by MAX_FRACTION of either song."""
    cap = min(seconds, MAX_FRACTION * out_duration, MAX_FRACTION * in_duration)
    if cap < MIN_OVERLAP_SEC:
        return _gapless(out_duration)
    return TransitionPlan('overlap', out_duration - cap, cap, 0.0, out_duration)


def plan_transition(mode: str, seconds: int, out_duration: float, in_duration: float, *,
                    consecutive_album: bool = False,
                    out_loudness: 'TrackLoudness | None' = None,
                    in_loudness: 'TrackLoudness | None' = None) -> TransitionPlan:
    """Decide how the outgoing song hands over to the incoming one.

    out_duration should be out_loudness.duration_sec when mode is 'smart'.
    """
    if not (math.isfinite(out_duration) and math.isfinite(in_duration)):
        return _gapless(out_duration if math.isfinite(out_duration) else 0.0)
    if mode == 'off' or consecutive_album:
        return _gapless(out_duration)

    cap = min(seconds, MAX_FRACTION * out_duration, MAX_FRACTION * in_duration)
    if not cap >= MIN_OVERLAP_SEC:  # also catches NaN
        return _gapless(out_duration)

    if mode == 'crossfade':
        return TransitionPlan('overlap', out_duration - cap, cap, 0.0, out_duration)
    if mode != 'smart':
        return _gapless(out_duration)
    if out_loudness is None or in_loudness is None:
        return TransitionPlan('overlap', out_duration - cap, cap, 0.0, out_duration)
    return _smart_plan(seconds, out_duration, in_duration, cap,
                       out_loudness, in_loudness)


def _smart_plan(seconds: int, out_duration: float, in_duration: float, cap: float,
                out_loudness: TrackLoudness, in_loudness: TrackLoudness) -> TransitionPlan:
    """Envelope-driven plan: trim trailing silence on the way out, skip
    leading silence on the way in."""
    tail = out_loudness.tail
    loud = [j for j, v in enumerate(tail) if v > SILENCE_FLOOR_LUFS]
    if not loud:
        return TransitionPlan('overlap', out_duration - cap, cap, 0.0, out_duration)
    last_j = loud[-1]
    end_of_music = out_duration - (len(tail) - 1 - last_j) * FRAME_SEC

    threshold = out_loudness.integrated_lufs - TAIL_DROP_DB
    run_start = last_j
    while run_start >= 0 and tail[run_start] < threshold:
        run_start -= 1
    if run_start == last_j:  # last loud frame is not a drop: nothing to fade
        fade_start = end_of_music
    else:
        fade_start = out_duration - (len(tail) - 1 - (run_start + 1)) * FRAME_SEC

    # The envelope can disagree with out_duration (tail longer than the song): keep every time inside the song.
    end_of_music = min(max(0.0, end_of_music), out_duration)
    fade_start = min(max(0.0, fade_start), end_of_music)
    tail_len = end_of_music - fade_start
    if tail_len < 1.0:
        overlap = min(ABRUPT_OVERLAP_SEC, cap)
    else:
        overlap = min(max(tail_len, 1.0), cap)
    overlap = min(overlap, end_of_music)
    if overlap < MIN_OVERLAP_SEC:
        return _gapless(out_duration)
    cue_out = max(0.0, end_of_music - overlap)

    head = in_loudness.head
    first_loud_k = next(
        (k for k, v in enumerate(head) if v > SILENCE_FLOOR_LUFS), None)
    if first_loud_k is None:
        incoming_start = 0.0
    else:
        t_k = (first_loud_k + 1) * FRAME_SEC
        bound = min(len(head) * FRAME_SEC, MAX_FRACTION * in_duration)
        incoming_start = min(max(0.0, t_k - M_WINDOW_SEC), bound)

    return TransitionPlan('overlap', cue_out, overlap, incoming_start, end_of_music)


def track_number(raw) -> 'int | None':
    """Parse a track number from an int, a 'n/total' string or similar.

    bools are rejected; anything unparsable or non-positive gives None.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    if isinstance(raw, str):
        before_slash = raw.split('/', 1)[0].strip()
        if before_slash.isascii() and before_slash.isdigit() and int(before_slash) > 0:
            return int(before_slash)
    return None


def is_consecutive_album_track(out_path, out_track_no, in_path, in_track_no) -> bool:
    """True when both files sit in the same directory and the incoming
    track number is exactly one more than the outgoing one."""
    out_no = track_number(out_track_no)
    in_no = track_number(in_track_no)
    if out_no is None or in_no is None:
        return False
    same_dir = (
        os.path.normcase(os.path.normpath(os.path.dirname(out_path)))
        == os.path.normcase(os.path.normpath(os.path.dirname(in_path)))
    )
    return same_dir and in_no == out_no + 1


def equal_power_gains(progress: float) -> 'tuple[float, float]':
    """Equal-power crossfade gains at *progress* (0 = full outgoing,
    1 = full incoming); progress is clamped to [0, 1]."""
    p = min(1.0, max(0.0, progress))
    return (math.cos(p * math.pi / 2), math.sin(p * math.pi / 2))
