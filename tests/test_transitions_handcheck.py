"""
tests/test_transitions_handcheck.py - hand-worked checks for transitions.py

Run with:
    python -m unittest tests/test_transitions_handcheck.py -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from transitions import *  # noqa: F401,F403
from loudness import TrackLoudness


def tl(i, head, tail, d):
    return TrackLoudness(i, -1.0, d, tuple(head), tuple(tail))


class TransitionsHandcheck(unittest.TestCase):
    def test_off_and_album_gapless(self):
        self.assertEqual(plan_transition('off', 12, 200.0, 180.0), TransitionPlan('gapless', 200.0, 0.0, 0.0, 200.0))
        self.assertEqual(plan_transition('crossfade', 6, 200.0, 180.0, consecutive_album=True).kind, 'gapless')

    def test_crossfade_and_cap(self):
        p = plan_transition('crossfade', 6, 200.0, 180.0)
        self.assertEqual((p.kind, p.overlap_sec, p.cue_out_sec, p.outgoing_end_sec), ('overlap', 6, 194.0, 200.0))
        p = plan_transition('crossfade', 12, 30.0, 180.0)
        self.assertEqual((p.overlap_sec, p.cue_out_sec), (7.5, 22.5))

    def test_tiny_song_is_gapless(self):
        self.assertEqual(plan_transition('crossfade', 12, 1.5, 180.0).kind, 'gapless')

    def test_smart_tail_and_lead_in(self):
        out = tl(-10.0, [-10.0] * 300, [-10.0] * 240 + [-20.0] * 40 + [-70.0] * 20, 200.0)
        inc = tl(-12.0, [-70.0] * 20 + [-12.0] * 280, [-12.0] * 300, 180.0)
        p = plan_transition('smart', 12, 200.0, 180.0, out_loudness=out, in_loudness=inc)
        for got, want in [(p.overlap_sec, 3.9), (p.cue_out_sec, 194.1), (p.outgoing_end_sec, 198.0), (p.incoming_start_sec, 1.7)]:
            self.assertAlmostEqual(got, want, places=6)

    def test_smart_abrupt_and_fallback(self):
        out = tl(-10.0, [-10.0] * 300, [-10.0] * 295 + [-70.0] * 5, 200.0)
        inc = tl(-12.0, [-12.0] * 300, [-12.0] * 300, 180.0)
        p = plan_transition('smart', 12, 200.0, 180.0, out_loudness=out, in_loudness=inc)
        self.assertAlmostEqual(p.overlap_sec, 2.0)
        self.assertAlmostEqual(p.cue_out_sec, 197.5)
        self.assertEqual(plan_transition('smart', 5, 200.0, 180.0).overlap_sec, 5)

    def test_track_number_and_album(self):
        self.assertEqual([track_number(x) for x in ('3/12', '03', 7, ' 4 / 10 ', None, '', 'abc', '0', True)],
                         [3, 3, 7, 4, None, None, None, None, None])
        a, b = '/m/Artist/Album/01 a.mp3', '/m/Artist/Album/02 b.mp3'
        self.assertTrue(is_consecutive_album_track(a, '1', b, '2/10'))
        self.assertFalse(is_consecutive_album_track(a, '1', '/m/Artist/Other/02 b.mp3', '2'))
        self.assertFalse(is_consecutive_album_track(a, '2', b, '2'))
        self.assertFalse(is_consecutive_album_track(a, None, b, '2'))

    def test_hostile_inputs_never_crash_or_nan(self):
        self.assertIsNone(track_number('²'))
        self.assertIsNone(track_number('①'))
        nan, inf = float('nan'), float('inf')
        for secs, out_d, in_d in [(10, nan, 200.0), (nan, 200.0, 200.0), (10, inf, 200.0), (10, 200.0, nan)]:
            self.assertEqual(plan_transition('crossfade', secs, out_d, in_d).kind, 'gapless')
        out = tl(-10.0, [-10.0] * 100, [-10.0] * 100, 3.0)   # 10 s of tail frames on a 3 s song
        inc = tl(-12.0, [-12.0] * 100, [-12.0] * 100, 180.0)
        p = plan_transition('smart', 12, 3.0, 180.0, out_loudness=out, in_loudness=inc)
        self.assertTrue(0.0 <= p.cue_out_sec <= p.outgoing_end_sec <= 3.0)
        self.assertLessEqual(p.cue_out_sec + p.overlap_sec, p.outgoing_end_sec + 1e-9)

    def test_gains(self):
        for p, (o, i) in [(0, (1.0, 0.0)), (1, (0.0, 1.0)), (0.5, (0.70710678, 0.70710678)), (-1, (1.0, 0.0)), (2, (0.0, 1.0))]:
            go, gi = equal_power_gains(p)
            self.assertAlmostEqual(go, o, 6)
            self.assertAlmostEqual(gi, i, 6)


if __name__ == '__main__':
    unittest.main()
