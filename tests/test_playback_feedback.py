"""
tests/test_playback_feedback.py - audible-time accounting + play/skip outcomes.

Run:
    python -m unittest tests.test_playback_feedback -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from playback_feedback import (PlaybackTracker, QUALIFY_SECONDS,
                               skip_signal, decayed_skip_aversion)


class TestAudibleAccounting(unittest.TestCase):

    def test_accumulates_only_while_running(self):
        t = PlaybackTracker('i1', duration=300, now=0.0)
        t.pause(now=30.0)          # 30s heard
        t.resume(now=100.0)        # 70s gap not counted
        self.assertAlmostEqual(t.audible(now=140.0), 70.0)  # 30 + 40

    def test_seek_does_not_add_the_jump(self):
        t = PlaybackTracker('i1', duration=300, now=0.0)
        # heard 20s, then user drags forward to 4:00 and keeps playing 10s
        t.seek(now=20.0)
        self.assertAlmostEqual(t.audible(now=30.0), 30.0)  # 20 + 10, not +240

    def test_repeated_seeking_never_inflates(self):
        t = PlaybackTracker('i1', duration=600, now=0.0)
        for k in range(1, 11):
            t.seek(now=float(k))
        self.assertAlmostEqual(t.audible(now=10.0), 10.0)


class TestOutcomes(unittest.TestCase):

    def _played(self, duration, audible, reason):
        t = PlaybackTracker('i1', duration=duration, now=0.0)
        return t.finalize(now=float(audible), reason=reason)

    def test_quick_skip_is_a_skip_not_a_play(self):
        o = self._played(duration=200, audible=5, reason='next')
        self.assertTrue(o.manual_skip)
        self.assertFalse(o.qualified_play)

    def test_listened_past_80pct_then_next_is_not_a_skip(self):
        o = self._played(duration=200, audible=170, reason='next')  # 85%
        self.assertFalse(o.manual_skip)
        self.assertTrue(o.qualified_play)

    def test_long_track_qualifies_at_240s_even_below_80pct(self):
        o = self._played(duration=1000, audible=250, reason='natural_end')
        self.assertTrue(o.qualified_play)

    def test_long_track_next_after_qualifying_can_still_be_a_skip(self):
        # heard 250s of a 1000s track: qualified play, but Next before 80% (800s)
        o = self._played(duration=1000, audible=250, reason='next')
        self.assertTrue(o.qualified_play)
        self.assertTrue(o.manual_skip)

    def test_natural_end_unknown_duration_qualifies(self):
        o = self._played(duration=None, audible=12, reason='natural_end')
        self.assertTrue(o.qualified_play)

    def test_unknown_duration_next_below_240_is_a_skip(self):
        o = self._played(duration=None, audible=30, reason='next')
        self.assertTrue(o.manual_skip)

    def test_stop_previous_select_failure_are_not_dislike(self):
        for reason in ('stop', 'previous', 'select', 'failure'):
            o = self._played(duration=200, audible=3, reason=reason)
            self.assertFalse(o.manual_skip, reason)

    def test_finalize_is_idempotent(self):
        t = PlaybackTracker('i1', duration=200, now=0.0)
        first = t.finalize(now=100.0, reason='next')
        self.assertIsNotNone(first)
        self.assertIsNone(t.finalize(now=101.0, reason='stop'))

    def test_pause_resume_before_finalize(self):
        t = PlaybackTracker('i1', duration=300, now=0.0)
        t.pause(now=100.0)
        t.resume(now=1000.0)
        o = t.finalize(now=1150.0, reason='natural_end')  # 100 + 150 = 250 audible
        self.assertTrue(o.qualified_play)


class TestScoringHelpers(unittest.TestCase):

    def test_skip_signal_bounded_and_monotone(self):
        self.assertEqual(skip_signal(0, 0), 0.0)
        s1 = skip_signal(3, 0)
        s2 = skip_signal(6, 0)
        self.assertGreater(s2, s1)
        self.assertLess(s2, 1.0)

    def test_qualified_plays_dilute_the_skip_signal(self):
        self.assertGreater(skip_signal(3, 0), skip_signal(3, 10))

    def test_decay_fades_over_time(self):
        now = 1_000_000.0
        fresh = decayed_skip_aversion(now - 60, now)
        old = decayed_skip_aversion(now - 30 * 86400, now)
        self.assertGreater(fresh, old)
        self.assertEqual(decayed_skip_aversion(None, now), 0.0)


if __name__ == '__main__':
    unittest.main()
