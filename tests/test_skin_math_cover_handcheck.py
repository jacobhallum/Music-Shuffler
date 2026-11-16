"""
tests/test_skin_math_cover_handcheck.py - hand-checked values for skin_math's album-art helpers: area_for, cover_spot,
fit_box. Standard library only.

Run with:
    python -m unittest tests.test_skin_math_cover_handcheck -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from skin_math import area_for, cover_spot, fit_box  # noqa: E402

A = (0, 0, 1920, 1040)


class AreaForTests(unittest.TestCase):
    def test_no_areas(self):
        self.assertIsNone(area_for((0, 0, 10, 10), []))

    def test_biggest_overlap_wins(self):
        left, right = (0, 0, 1000, 1000), (1000, 0, 1000, 1000)
        self.assertEqual(area_for((900, 10, 300, 10), [left, right]), right)
        self.assertEqual(area_for((800, 10, 300, 10), [left, right]), left)

    def test_off_every_area_takes_the_nearest(self):
        near, far = (0, 0, 2560, 1400), (-1920, 0, 1920, 1040)
        self.assertEqual(area_for((5000, 100, 10, 10), [far, near]), near)
        self.assertEqual(area_for((-3000, 100, 10, 10), [near, far]), far)


class CoverSpotTests(unittest.TestCase):
    def test_right_when_it_fits(self):
        self.assertEqual(cover_spot((100, 100, 550, 232), 550, [A]), ('right', 0))

    def test_left_when_only_left_fits(self):
        self.assertEqual(cover_spot((1400, 100, 550, 232), 550, [A]), ('left', 0))

    def test_slide_when_neither_side_fits(self):
        self.assertEqual(cover_spot((500, 100, 825, 348), 825, [A]), ('right', -230))

    def test_no_slide_when_the_pair_is_wider_than_the_area(self):
        self.assertEqual(cover_spot((500, 100, 825, 348), 825, [(0, 0, 1280, 1000)]), ('right', 0))

    def test_uses_the_monitor_holding_main(self):
        areas = [(-1920, 0, 1920, 1040), (0, 0, 2560, 1400)]
        self.assertEqual(cover_spot((-700, 100, 550, 232), 550, areas), ('left', 0))

    def test_main_off_every_monitor_slides_onto_the_nearest(self):
        self.assertEqual(cover_spot((5000, 100, 550, 232), 550, [(0, 0, 2560, 1400)]), ('right', -3540))

    def test_no_areas(self):
        self.assertEqual(cover_spot((100, 100, 550, 232), 550, []), ('right', 0))

    def test_returns_ints(self):
        side, dx = cover_spot((500, 100, 825, 348), 825, [A])
        self.assertIsInstance(dx, int)


class FitBoxTests(unittest.TestCase):
    def test_square(self):
        self.assertEqual(fit_box(500, 500, 243), (0, 0, 243, 243))

    def test_wide(self):
        self.assertEqual(fit_box(1000, 500, 486), (0, 121, 486, 243))

    def test_very_wide_keeps_one_pixel(self):
        self.assertEqual(fit_box(3000, 10, 243), (0, 121, 243, 1))

    def test_very_tall_keeps_one_pixel(self):
        self.assertEqual(fit_box(10, 3000, 243), (121, 0, 1, 243))

    def test_tiny_grows(self):
        self.assertEqual(fit_box(1, 1, 729), (0, 0, 729, 729))


if __name__ == '__main__':
    unittest.main()
