"""
tests/test_transitions.py - stdlib-only unit tests for transitions.py

Run with:
    python -m unittest tests/test_transitions.py -v
"""

import math
import os
import sys
import unittest

# Allow running from project root or tests/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import transitions
from transitions import (
    TransitionPlan,
    plan_transition,
    track_number,
    is_consecutive_album_track,
    equal_power_gains,
)
from loudness import TrackLoudness


def _loud(integrated=-10.0, duration=200.0, head=None, tail=None):
    """TrackLoudness with a solid envelope unless overridden."""
    return TrackLoudness(
        integrated_lufs=integrated,
        true_peak_dbtp=-1.0,
        duration_sec=duration,
        head=tuple(head if head is not None else [-10.0] * 300),
        tail=tuple(tail if tail is not None else [-10.0] * 300),
    )


def _assert_plan(test, plan, kind, cue_out, overlap, incoming_start, outgoing_end):
    test.assertEqual(plan.kind, kind)
    test.assertAlmostEqual(plan.cue_out_sec, cue_out)
    test.assertAlmostEqual(plan.overlap_sec, overlap)
    test.assertAlmostEqual(plan.incoming_start_sec, incoming_start)
    test.assertAlmostEqual(plan.outgoing_end_sec, outgoing_end)


class GaplessTests(unittest.TestCase):
    """plan_transition falls back to a gapless plan for off mode, album
    continuity, unknown modes and too-short songs."""

    def test_off_mode_is_gapless(self):
        plan = plan_transition('off', 8, 200.0, 200.0)
        self.assertIsInstance(plan, TransitionPlan)
        _assert_plan(self, plan, 'gapless', 200.0, 0.0, 0.0, 200.0)

    def test_consecutive_album_overrides_crossfade(self):
        plan = plan_transition('crossfade', 8, 200.0, 200.0,
                               consecutive_album=True,
                               out_loudness=_loud(), in_loudness=_loud())
        _assert_plan(self, plan, 'gapless', 200.0, 0.0, 0.0, 200.0)

    def test_unknown_mode_is_gapless(self):
        plan = plan_transition('shrink', 8, 200.0, 200.0,
                               out_loudness=_loud(), in_loudness=_loud())
        _assert_plan(self, plan, 'gapless', 200.0, 0.0, 0.0, 200.0)

    def test_zero_out_duration_is_gapless(self):
        # cap = 0.25 * 0 = 0 < MIN_OVERLAP_SEC
        plan = plan_transition('crossfade', 8, 0.0, 200.0)
        _assert_plan(self, plan, 'gapless', 0.0, 0.0, 0.0, 0.0)


class CrossfadeTests(unittest.TestCase):
    """Fixed crossfade length and the 25% caps on either song."""

    def test_fixed_crossfade_length(self):
        plan = plan_transition('crossfade', 6, 200.0, 200.0)
        _assert_plan(self, plan, 'overlap', 194.0, 6.0, 0.0, 200.0)

    def test_cap_at_25pct_of_short_outgoing(self):
        # min(30, 0.25*8, 0.25*200) = 2.0
        plan = plan_transition('crossfade', 30, 8.0, 200.0)
        _assert_plan(self, plan, 'overlap', 6.0, 2.0, 0.0, 8.0)

    def test_cap_at_25pct_of_short_incoming(self):
        # min(30, 0.25*200, 0.25*6) = 1.5
        plan = plan_transition('crossfade', 30, 200.0, 6.0)
        _assert_plan(self, plan, 'overlap', 198.5, 1.5, 0.0, 200.0)

    def test_tiny_song_falls_back_to_gapless(self):
        # min(10, 0.25*1, 50) = 0.25 < MIN_OVERLAP_SEC
        plan = plan_transition('crossfade', 10, 1.0, 200.0)
        _assert_plan(self, plan, 'gapless', 1.0, 0.0, 0.0, 1.0)

    def test_smart_without_loudness_falls_back_to_crossfade(self):
        expected = plan_transition('crossfade', 6, 200.0, 200.0)
        plan = plan_transition('smart', 6, 200.0, 200.0)
        _assert_plan(self, plan, expected.kind, expected.cue_out_sec,
                     expected.overlap_sec, expected.incoming_start_sec,
                     expected.outgoing_end_sec)

    def test_smart_with_all_silent_tail_falls_back_to_crossfade(self):
        silent_out = _loud(tail=[-70.0] * 300)
        plan = plan_transition('smart', 6, 200.0, 200.0,
                               out_loudness=silent_out, in_loudness=_loud())
        _assert_plan(self, plan, 'overlap', 194.0, 6.0, 0.0, 200.0)


class SmartTests(unittest.TestCase):
    """Envelope-driven smart plans."""

    def test_smart_trims_quiet_tail_before_trailing_silence(self):
        # tail len 100, j=0..99: 40 loud frames, 40 frames at -20 dB
        # (below integrated-6 = -16), then 20 silent frames.
        # Last loud frame j=79 at t=198.0; quiet run j=40..79, earliest
        # j=40 at t=194.1; tail_len=3.9 -> overlap=3.9.
        out = _loud(tail=[-10.0] * 40 + [-20.0] * 40 + [-70.0] * 20)
        plan = plan_transition('smart', 6, 200.0, 200.0,
                               out_loudness=out, in_loudness=_loud())
        _assert_plan(self, plan, 'overlap', 194.1, 3.9, 0.0, 198.0)

    def test_smart_abrupt_ending_uses_fixed_short_overlap(self):
        # tail = 79 loud frames + 21 silent frames: last loud frame j=78
        # at t=197.9 and it is not a drop, so tail_len=0 -> overlap 2.0.
        out = _loud(tail=[-10.0] * 79 + [-70.0] * 21)
        plan = plan_transition('smart', 6, 200.0, 200.0,
                               out_loudness=out, in_loudness=_loud())
        _assert_plan(self, plan, 'overlap', 195.9, 2.0, 0.0, 197.9)

    def test_smart_skips_leading_silence_on_incoming(self):
        # Outgoing ends abruptly at t=200.0; incoming is silent for its
        # first 2.0 s, so start at t_k - 0.4 = 1.7.
        out = _loud(tail=[-10.0] * 100)
        in_loud = _loud(head=[-70.0] * 20 + [-10.0] * 80)
        plan = plan_transition('smart', 6, 200.0, 200.0,
                               out_loudness=out, in_loudness=in_loud)
        _assert_plan(self, plan, 'overlap', 198.0, 2.0, 1.7, 200.0)

    def test_smart_all_silent_head_starts_incoming_at_zero(self):
        out = _loud(tail=[-10.0] * 79 + [-70.0] * 21)
        in_loud = _loud(head=[-70.0] * 100)
        plan = plan_transition('smart', 6, 200.0, 200.0,
                               out_loudness=out, in_loudness=in_loud)
        _assert_plan(self, plan, 'overlap', 195.9, 2.0, 0.0, 197.9)

    def test_smart_incoming_start_clamped_to_fraction_of_incoming(self):
        # in_duration=4.0 -> cap=1.0; first loud head frame k=14 at t=1.5,
        # raw start 1.1 clamped to min(len(head)*0.1, 0.25*4.0) = 1.0.
        out = _loud(tail=[-10.0] * 40 + [-20.0] * 40 + [-70.0] * 20)
        in_loud = _loud(duration=4.0, head=[-70.0] * 14 + [-10.0] * 6)
        plan = plan_transition('smart', 6, 200.0, 4.0,
                               out_loudness=out, in_loudness=in_loud)
        _assert_plan(self, plan, 'overlap', 197.0, 1.0, 1.0, 198.0)


class TrackNumberTests(unittest.TestCase):

    def test_rejects_bools(self):
        self.assertIsNone(track_number(True))
        self.assertIsNone(track_number(False))

    def test_positive_and_nonpositive_ints(self):
        self.assertEqual(track_number(3), 3)
        self.assertIsNone(track_number(0))
        self.assertIsNone(track_number(-2))

    def test_strings(self):
        self.assertEqual(track_number('5'), 5)
        self.assertEqual(track_number('5/12'), 5)
        self.assertEqual(track_number(' 5 / 12 '), 5)

    def test_invalid_strings_and_none(self):
        for raw in (None, '', 'abc', '0', '0/12'):
            self.assertIsNone(track_number(raw))


class ConsecutiveAlbumTrackTests(unittest.TestCase):

    def test_same_dir_next_track_is_consecutive(self):
        self.assertTrue(is_consecutive_album_track(
            '/music/album/03 - a.mp3', '3/12',
            '/music/album/04 - b.mp3', '4/12'))

    def test_same_dir_int_track_numbers(self):
        self.assertTrue(is_consecutive_album_track(
            '/music/album/07 - a.mp3', 7,
            '/music/album/08 - b.mp3', 8))

    def test_gap_in_track_numbers_is_not_consecutive(self):
        self.assertFalse(is_consecutive_album_track(
            '/music/album/03 - a.mp3', 3,
            '/music/album/05 - b.mp3', 5))

    def test_different_directories_are_not_consecutive(self):
        self.assertFalse(is_consecutive_album_track(
            '/music/album1/03 - a.mp3', 3,
            '/music/album2/04 - b.mp3', 4))

    def test_unparsable_track_number_is_not_consecutive(self):
        self.assertFalse(is_consecutive_album_track(
            '/music/album/03 - a.mp3', None,
            '/music/album/04 - b.mp3', 4))


class EqualPowerGainsTests(unittest.TestCase):

    def test_at_zero_outgoing_is_full(self):
        out_gain, in_gain = equal_power_gains(0.0)
        self.assertAlmostEqual(out_gain, 1.0)
        self.assertAlmostEqual(in_gain, 0.0)

    def test_at_half_is_equal_power(self):
        out_gain, in_gain = equal_power_gains(0.5)
        self.assertAlmostEqual(out_gain, math.cos(math.pi / 4))
        self.assertAlmostEqual(in_gain, math.sin(math.pi / 4))

    def test_at_one_incoming_is_full(self):
        out_gain, in_gain = equal_power_gains(1.0)
        self.assertAlmostEqual(out_gain, 0.0)
        self.assertAlmostEqual(in_gain, 1.0)

    def test_out_of_range_progress_is_clamped(self):
        self.assertEqual(equal_power_gains(-0.25), equal_power_gains(0.0))
        self.assertEqual(equal_power_gains(2.0), equal_power_gains(1.0))


if __name__ == '__main__':
    unittest.main()
