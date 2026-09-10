"""
playback_feedback.py - honest listening outcomes (standalone, no GUI).

Tracks *audible* elapsed time for one playback instance, separate from the
seek cursor, so:
  - seeking never counts as listening
  - a qualified play means the listener actually heard enough of the track
  - a manual Next before ~80% is a skip; stop / previous / direct selection /
    decode failure are NOT dislike signals

The player owns one PlaybackTracker per playback and finalizes it exactly
once on every exit path.
"""

from collections import namedtuple

QUALIFY_SECONDS = 240.0
QUALIFY_FRACTION = 0.80

# reasons a playback instance can end
NATURAL_END = 'natural_end'
NEXT = 'next'
PREVIOUS = 'previous'
SELECT = 'select'        # user picked another track directly
STOP = 'stop'            # stop / app close
FAILURE = 'failure'      # decode / conversion failure

Outcome = namedtuple('Outcome',
                     'instance_id reason audible qualified_play manual_skip')


class PlaybackTracker:
    def __init__(self, instance_id, *, duration=None, now=0.0):
        self.instance_id = instance_id
        self.duration = duration if (duration and duration > 0) else None
        self._audible = 0.0
        self._running_since = now      # None => not accumulating
        self._finalized = False

    # -- lifecycle -------------------------------------------------------
    def _settle(self, now):
        if self._running_since is not None:
            self._audible += max(0.0, now - self._running_since)
            self._running_since = now

    def pause(self, now):
        self._settle(now)
        self._running_since = None

    def resume(self, now):
        if self._running_since is None and not self._finalized:
            self._running_since = now

    def seek(self, now):
        """A seek settles audible time up to now and keeps playing - the jumped
        interval is never added."""
        self._settle(now)

    def audible(self, now):
        a = self._audible
        if self._running_since is not None:
            a += max(0.0, now - self._running_since)
        return a

    def _qualify_threshold(self):
        if self.duration is not None:
            return min(QUALIFY_SECONDS, QUALIFY_FRACTION * self.duration)
        return QUALIFY_SECONDS

    def finalize(self, now, reason):
        """Return the Outcome once; subsequent calls return None."""
        if self._finalized:
            return None
        self._finalized = True
        aud = self.audible(now)

        qualified = aud >= self._qualify_threshold()
        if reason == NATURAL_END and self.duration is None:
            qualified = True   # heard it to the end; we just don't know how long

        manual_skip = False
        if reason == NEXT:
            if self.duration is not None:
                manual_skip = aud < QUALIFY_FRACTION * self.duration
            else:
                manual_skip = aud < QUALIFY_SECONDS

        return Outcome(self.instance_id, reason, aud, qualified, manual_skip)


# -- scoring helpers ---------------------------------------------------

def skip_signal(manual_skips, qualified_plays, prior=5):
    """Confidence-adjusted dislike in [0, 1): 0 with no observations, rising
    with skips, diluted by qualified plays and the prior."""
    denom = manual_skips + qualified_plays + prior
    return manual_skips / denom if denom else 0.0


def decayed_skip_aversion(last_skipped_at, now, half_life_days=7.0):
    """Temporary aversion after a recent skip; halves every half_life_days."""
    if not last_skipped_at:
        return 0.0
    age_days = max(0.0, (now - last_skipped_at) / 86400.0)
    return 0.5 ** (age_days / half_life_days)
