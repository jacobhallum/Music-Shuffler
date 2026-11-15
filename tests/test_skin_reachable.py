"""
tests/test_skin_reachable.py - skin_math.reachable: a skinned window's title bar always stays where the mouse can grab it.

Run with:
    python -m unittest tests.test_skin_reachable -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from skin_math import reachable  # noqa: E402

ONE = [(0, 0, 2560, 1392)]                               # one monitor, taskbar at the bottom
TWO = [(0, 0, 1920, 1040), (1920, 0, 2560, 1400)]        # two monitors of different sizes


class ReachableTests(unittest.TestCase):
    def test_a_window_on_screen_stays_put(self):
        self.assertEqual(reachable((200, 200, 275, 116), ONE), (200, 200))
        self.assertEqual(reachable((2560 - 100, 1392 - 14, 275, 116), ONE), (2460, 1378))   # just reachable

    def test_off_the_left_or_right_edge_comes_back_to_a_grabbable_strip(self):
        self.assertEqual(reachable((-250, 300, 275, 116), ONE), (-175, 300))
        self.assertEqual(reachable((2540, 300, 275, 116), ONE), (2460, 300))

    def test_under_the_taskbar_or_above_the_top(self):
        self.assertEqual(reachable((100, 1420, 275, 116), ONE), (100, 1378))
        self.assertEqual(reachable((100, -50, 275, 116), ONE), (100, 0))

    def test_title_bar_height_follows_double_size(self):
        self.assertEqual(reachable((100, 1420, 550, 232), ONE, grip=28), (100, 1364))

    def test_a_narrow_window_must_show_all_of_its_width(self):
        self.assertEqual(reachable((-30, 300, 60, 40), ONE), (0, 300))

    def test_two_monitors_each_counts_and_the_nearest_wins(self):
        self.assertEqual(reachable((2000, 1200, 275, 116), TWO), (2000, 1200))    # low on the taller monitor
        self.assertEqual(reachable((100, 1200, 275, 116), TWO), (100, 1026))      # below the shorter one
        self.assertEqual(reachable((1900, 1100, 275, 116), TWO), (1900, 1100))    # straddles, on the tall one

    def test_no_areas_leaves_it_alone(self):
        self.assertEqual(reachable((-5000, -5000, 275, 116), []), (-5000, -5000))


if __name__ == '__main__':
    unittest.main()
