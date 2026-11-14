"""
tests/test_skin_math_handcheck.py - hand-worked checks for skin_math.py (hidden from the worker).

Run with:
    python -m unittest tests.test_skin_math_handcheck -v
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import skin_math as sm

SCREEN = (0, 0, 1920, 1080)


class SnapHandcheck(unittest.TestCase):
    def test_snap(self):
        self.assertEqual(sm.snap((8, 300, 275, 116), [], SCREEN), (0, 300))
        self.assertEqual(sm.snap((11, 300, 275, 116), [], SCREEN), (11, 300))
        self.assertEqual(sm.snap((1640, 970, 275, 116), [], SCREEN), (1645, 964))
        main = (500, 400, 275, 116)
        # dragging the EQ under the main window: tops of EQ snap to main's bottom, lefts align
        self.assertEqual(sm.snap((506, 520, 275, 116), [main], SCREEN), (500, 516))
        # to the right of main, 7 px off; vertical overlap present
        self.assertEqual(sm.snap((782, 404, 275, 116), [main], SCREEN), (775, 400))
        # far below main: no x snap from main (no vertical overlap)
        self.assertEqual(sm.snap((506, 700, 275, 116), [main], SCREEN), (506, 700))
        # closest wins: main's right edge (775) is 3 away, screen edge none
        self.assertEqual(sm.snap((772, 300, 100, 116), [main], SCREEN), (775, 300))

    def test_docked(self):
        a = (0, 0, 275, 116)
        self.assertTrue(sm.docked(a, (0, 116, 275, 116)))
        self.assertTrue(sm.docked(a, (275, 50, 100, 20)))
        self.assertFalse(sm.docked(a, (0, 117, 275, 116)))
        self.assertFalse(sm.docked(a, (275, 116, 50, 50)))
        rects = [a, (0, 116, 275, 116), (0, 232, 275, 200), (900, 900, 10, 10)]
        self.assertEqual(sm.dock_group(0, rects), {0, 1, 2})
        self.assertEqual(sm.dock_group(3, rects), {3})


class VisHandcheck(unittest.TestCase):
    def test_spectrum(self):
        self.assertEqual(sm.spectrum([0.0] * 512, 44100), [0] * 19)
        self.assertEqual(sm.spectrum([0.5] * 20, 44100), [0] * 19)
        sine = [math.sin(2 * math.pi * 1000 * i / 44100) for i in range(600)]
        bars = sm.spectrum(sine, 44100)
        self.assertEqual(len(bars), 19)
        fmax = 16000.0
        edges = [60.0 * (fmax / 60.0) ** (k / 19) for k in range(20)]
        band = next(k for k in range(19) if edges[k] <= 1000 < edges[k + 1])
        self.assertGreaterEqual(bars[band], 14)
        self.assertLessEqual(max(bars[:2] + bars[-2:]), 6)
        self.assertEqual(len(sm.spectrum(sine, 44100, bars=10, height=8)), 10)
        self.assertLessEqual(max(sm.spectrum(sine, 44100, bars=10, height=8)), 8)

    def test_scope(self):
        self.assertEqual(sm.scope([]), [8] * 76)
        self.assertEqual(sm.scope([1.0] * 100, width=4), [0] * 4)
        self.assertEqual(sm.scope([-2.0] * 100, width=4), [15] * 4)
        self.assertEqual(sm.scope([0.0, 1.0, 0.0, -1.0], width=4, height=16), [8, 0, 8, 15])


if __name__ == '__main__':
    unittest.main()
