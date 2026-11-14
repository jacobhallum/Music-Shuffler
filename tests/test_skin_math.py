"""
tests/test_skin_math.py - stdlib-only unit tests for skin_math.py

Run with:
    python -m unittest tests/test_skin_math.py -v
"""

import math
import os
import sys
import unittest

# Allow running from project root or tests/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import skin_math
from skin_math import SNAP_PX, snap, docked, dock_group, spectrum, scope


class SnapTests(unittest.TestCase):
    def test_snaps_to_left_screen_edge_within_10px(self):
        # 4 px from the left edge of the screen -> snaps to sx = 0
        self.assertEqual(snap((4, 50, 20, 10), [], (0, 0, 500, 500)), (0, 50))

    def test_snaps_to_top_screen_edge_within_10px(self):
        self.assertEqual(snap((50, 3, 20, 10), [], (0, 0, 500, 500)), (50, 0))

    def test_no_snap_at_11px(self):
        self.assertEqual(snap((11, 50, 20, 10), [], (0, 0, 500, 500)), (11, 50))
        self.assertEqual(snap((50, 11, 20, 10), [], (0, 0, 500, 500)), (50, 11))

    def test_snaps_to_right_side_of_another_window(self):
        other = (100, 0, 50, 200)
        # 6 px to the right of other's right edge: candidate ox + ow = 150
        self.assertEqual(snap((156, 20, 30, 20), [other], (0, 0, 500, 500)),
                         (150, 20))

    def test_aligned_tops(self):
        other = (100, 40, 50, 200)
        self.assertEqual(snap((155, 45, 30, 20), [other], (0, 0, 500, 500)),
                         (150, 40))   # 5 px from its right edge: tops align, sides touch

    def test_no_snap_to_window_far_away_vertically(self):
        other = (100, 0, 50, 20)
        # x would snap to 150 (ox + ow) but the windows are far apart
        # vertically (y = 200, other spans 0..20), so no x snap applies.
        self.assertEqual(snap((155, 200, 30, 20), [other], (0, 0, 500, 500)),
                         (155, 200))

    def test_closest_candidate_wins(self):
        # Candidates from screen: sx = 0, sx + sw - w = 480; from other: 150 etc.
        # x = 4 is 4 away from screen left, but far from others' candidates.
        self.assertEqual(snap((4, 100, 20, 10), [(100, 0, 50, 20)],
                              (0, 0, 500, 500)), (0, 100))

    def test_first_candidate_wins_tie(self):
        # x = 5: candidates sx = 0 (d=5) and ox + ow = 10 (d=5)? Make a tie.
        # other = (-5, 0, 10, 20) with overlapping y -> candidates: ox - w = -25,
        # ox + ow = 5, ox = -5, ox + ow - w = -15. x = 5: tie with ox + ow = 5
        # is exact; instead craft tie between screen and other:
        # screen sx = 0, other right edge also at 0 -> other = (-40, 0, 40, 60).
        # x = 5: candidates 0 (screen, d=5) and -40, 0, -40, -45... tie between
        # two 0s -> first wins, still 0.
        self.assertEqual(snap((5, 10, 20, 10), [(-40, 0, 40, 60)],
                              (0, 0, 500, 500)), (0, 0))


class DockedTests(unittest.TestCase):
    def test_touching_below(self):
        a = (0, 0, 100, 50)
        b = (10, 50, 80, 50)
        self.assertTrue(docked(a, b))

    def test_touching_right(self):
        a = (0, 0, 100, 50)
        b = (100, 10, 60, 30)
        self.assertTrue(docked(a, b))

    def test_gap_of_one_px(self):
        a = (0, 0, 100, 50)
        b = (10, 51, 80, 50)
        self.assertFalse(docked(a, b))

    def test_corner_only_touch_is_false(self):
        # b starts exactly at a's bottom-right corner: x ranges don't overlap
        a = (0, 0, 100, 100)
        b = (100, 100, 50, 50)
        self.assertFalse(docked(a, b))

    def test_overlapping_without_touch(self):
        self.assertFalse(docked((0, 0, 100, 100), (50, 50, 100, 100)))


class DockGroupTests(unittest.TestCase):
    def test_chain_of_three(self):
        rects = [
            (0, 0, 10, 10),     # 0
            (10, 0, 10, 10),    # 1: docked with 0 (right edge)
            (20, 0, 10, 10),    # 2: docked with 1
            (500, 500, 10, 10), # 3: apart
        ]
        self.assertEqual(dock_group(0, rects), {0, 1, 2})
        self.assertEqual(dock_group(1, rects), {0, 1, 2})
        self.assertEqual(dock_group(3, rects), {3})

    def test_one_apart(self):
        rects = [
            (0, 0, 10, 10),
            (11, 0, 10, 10),  # 1 px gap -> not docked
        ]
        self.assertEqual(dock_group(0, rects), {0})
        self.assertEqual(dock_group(1, rects), {1})


class SpectrumTests(unittest.TestCase):
    def test_silence_all_zeros(self):
        result = spectrum([0.0] * 256, 44100)
        self.assertEqual(result, [0] * 19)

    def test_full_scale_sine_peaks_near_top(self):
        rate = 44100
        n = 512
        samples = [math.sin(2 * math.pi * 1000.0 * i / rate) for i in range(n)]
        result = spectrum(samples, rate)
        self.assertEqual(len(result), 19)
        # The 1000 Hz band should be near the top.
        fmax = min(16000.0, rate / 2)
        k1000 = None
        for k in range(19):
            lo = 60.0 * (fmax / 60.0) ** (k / 19)
            hi = 60.0 * (fmax / 60.0) ** ((k + 1) / 19)
            if lo <= 1000.0 < hi:
                k1000 = k
        self.assertIsNotNone(k1000)
        self.assertGreaterEqual(result[k1000], 13)
        # Far bands (other side of the spectrum) should be low.
        far = [v for k, v in enumerate(result)
               if abs(k - k1000) > 4]
        self.assertLess(max(far), 8)

    def test_fewer_than_32_samples(self):
        self.assertEqual(spectrum([1.0] * 31, 44100), [0] * 19)

    def test_output_length_is_bars(self):
        samples = [math.sin(2 * math.pi * 440.0 * i / 44100) for i in range(256)]
        self.assertEqual(len(spectrum(samples, 44100, bars=19)), 19)
        self.assertEqual(len(spectrum(samples, 44100, bars=7, height=8)), 7)


class ScopeTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(scope([], width=76, height=16),
                         [round(15 / 2)] * 76)

    def test_full_scale_plus_one_is_top(self):
        result = scope([1.0] * 76)
        self.assertEqual(result, [0] * 76)

    def test_full_scale_minus_one_is_bottom(self):
        result = scope([-1.0] * 76, width=76, height=16)
        self.assertEqual(result, [15] * 76)

    def test_length_is_width(self):
        samples = [math.sin(2 * math.pi * 8 * i / 76) for i in range(304)]
        self.assertEqual(len(scope(samples, width=76, height=16)), 76)

    def test_values_clamped_and_in_range(self):
        result = scope([2.0, -2.0] * 40, width=76, height=16)
        self.assertTrue(all(0 <= v <= 15 for v in result))


if __name__ == '__main__':
    unittest.main()
