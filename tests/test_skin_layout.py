"""
tests/test_skin_layout.py - stdlib-only unit tests for skin_layout.py

Run with:
    python -m unittest tests/test_skin_layout.py -v
"""

import os
import sys
import unittest

# Allow running from project root or tests/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import skin_layout
from skin_layout import (
    MAIN_LAYOUT, EQ_LAYOUT, TIME_DIGIT_X, MINUS_SIGN, MARQUEE_SEPARATOR,
    hit_test, slider_value, slider_thumb_x,
    volume_frame, balance_frame, eq_frame, eq_thumb_y,
    time_digits, text_cells, marquee_text, marquee_slice,
)
from skin_sprites import FONT_LOOKUP


class HitTestTests(unittest.TestCase):
    def test_transport_buttons(self):
        cases = {
            'previous': (20, 95), 'play': (45, 95), 'pause': (70, 95),
            'stop': (95, 95), 'next': (115, 95), 'eject': (145, 95),
        }
        for name, (x, y) in cases.items():
            self.assertEqual(hit_test(MAIN_LAYOUT, x, y), name, (name, x, y))

    def test_close_inside_titlebar(self):
        # close rect (264, 3, 9, 9) precedes titlebar in MAIN_LAYOUT order
        self.assertEqual(hit_test(MAIN_LAYOUT, 268, 7), 'close')

    def test_titlebar_point(self):
        self.assertEqual(hit_test(MAIN_LAYOUT, 130, 5), 'titlebar')

    def test_scale_2(self):
        # play at (39, 88, 23, 18): scaled pixel (90, 190) -> logical (45, 95)
        self.assertEqual(hit_test(MAIN_LAYOUT, 90, 190, scale=2), 'play')
        # scaled position must map by integer division, not raw coordinates
        self.assertEqual(hit_test(MAIN_LAYOUT, 46, 96, scale=2), None)

    def test_outside_window_none(self):
        self.assertIsNone(hit_test(MAIN_LAYOUT, 300, 130))
        self.assertIsNone(hit_test(MAIN_LAYOUT, -1, -1))
        self.assertIsNone(hit_test(EQ_LAYOUT, 300, 130))

    def test_eq_band4_and_preamp(self):
        self.assertEqual(hit_test(EQ_LAYOUT, 78 + 18 * 4 + 5, 60), 'band4')
        self.assertEqual(hit_test(EQ_LAYOUT, 25, 70), 'preamp')

    def test_layout_entries_and_order(self):
        names = list(MAIN_LAYOUT)
        self.assertEqual(
            names[:6],
            ['menu', 'minimize', 'shade', 'close', 'clutter_o', 'clutter_a'])
        self.assertEqual(names[-2:], ['about', 'titlebar'])
        self.assertEqual(names[-1], 'titlebar')
        eq_names = list(EQ_LAYOUT)
        self.assertEqual(eq_names[0], 'close')
        self.assertEqual(eq_names[-1], 'titlebar')
        self.assertIn('band9', eq_names)
        self.assertEqual(EQ_LAYOUT['band9'], (78 + 18 * 9, 38, 14, 63))

    def test_time_digit_constants(self):
        self.assertEqual(TIME_DIGIT_X, (48, 60, 78, 90))
        self.assertEqual(MINUS_SIGN, (38, 32, 5, 1))


class SliderTests(unittest.TestCase):
    TRACK_X, TRACK_W, THUMB_W = 16, 248, 28

    def test_round_trip_ends_and_middle(self):
        lo, hi = 0, 255
        for value in (lo, (lo + hi) / 2, hi):
            x = slider_thumb_x(value, self.TRACK_X, self.TRACK_W,
                               self.THUMB_W, lo, hi)
            got = slider_value(x + self.THUMB_W // 2, self.TRACK_X,
                               self.TRACK_W, self.THUMB_W, lo, hi)
            self.assertAlmostEqual(got, value, delta=1.0, msg=(value, x, got))

    def test_slider_value_clamps_outside_track(self):
        lo, hi = 0, 100
        self.assertEqual(slider_value(-50, self.TRACK_X, self.TRACK_W,
                                      self.THUMB_W, lo, hi), lo)
        self.assertEqual(slider_value(9999, self.TRACK_X, self.TRACK_W,
                                      self.THUMB_W, lo, hi), hi)

    def test_slider_thumb_x_clamps_value(self):
        lo, hi = 0, 100
        self.assertEqual(slider_thumb_x(-5, self.TRACK_X, self.TRACK_W,
                                        self.THUMB_W, lo, hi), self.TRACK_X)
        self.assertEqual(slider_thumb_x(105, self.TRACK_X, self.TRACK_W,
                                        self.THUMB_W, lo, hi),
                         self.TRACK_X + self.TRACK_W - self.THUMB_W)
        self.assertIsInstance(slider_thumb_x(50, self.TRACK_X, self.TRACK_W,
                                             self.THUMB_W, lo, hi), int)

    def test_slider_value_degenerate_span(self):
        self.assertEqual(slider_value(50, 0, 28, 28, 10, 90), 10)


class FrameTests(unittest.TestCase):
    def test_volume_frame(self):
        self.assertEqual(volume_frame(0), 0)
        self.assertEqual(volume_frame(50), 14)
        self.assertEqual(volume_frame(100), 27)

    def test_volume_frame_clamps(self):
        self.assertEqual(volume_frame(-5), 0)
        self.assertEqual(volume_frame(150), 27)

    def test_balance_frame(self):
        self.assertEqual(balance_frame(-100), 27)
        self.assertEqual(balance_frame(0), 0)
        self.assertEqual(balance_frame(50), 14)

    def test_eq_frame(self):
        self.assertEqual(eq_frame(-12), 0)
        self.assertEqual(eq_frame(0), 14)
        self.assertEqual(eq_frame(12), 27)

    def test_eq_thumb_y(self):
        self.assertEqual(eq_thumb_y(12), 0)
        self.assertEqual(eq_thumb_y(0), 26)  # round(51/2) == round(25.5) == 26
        self.assertEqual(eq_thumb_y(-12), 51)


class TimeDigitsTests(unittest.TestCase):
    def test_values(self):
        self.assertEqual(time_digits(0), (0, 0, 0, 0))
        self.assertEqual(time_digits(61), (0, 1, 0, 1))
        self.assertEqual(time_digits(3599), (5, 9, 5, 9))
        self.assertEqual(time_digits(6000), (9, 9, 5, 9))
        self.assertEqual(time_digits(None), (0, 0, 0, 0))
        self.assertEqual(time_digits(-5), (0, 0, 0, 0))


class TextCellsTests(unittest.TestCase):
    def test_lower_and_upper_equal(self):
        for ch in 'abcxyz':
            self.assertEqual(text_cells(ch.upper()), text_cells(ch))

    def test_digit(self):
        self.assertEqual(text_cells('5'), [FONT_LOOKUP['5']])

    def test_unknown_char_is_space_cell(self):
        self.assertEqual(text_cells('~'), [FONT_LOOKUP[' ']])
        # 'Å' is directly in the lookup; '~' falls back to space
        self.assertEqual(text_cells('a~'), [FONT_LOOKUP['a'], FONT_LOOKUP[' ']])

    def test_empty(self):
        self.assertEqual(text_cells(''), [])


class MarqueeTests(unittest.TestCase):
    def test_with_artist_and_duration(self):
        self.assertEqual(marquee_text('Artist', 'Title', 61),
                         'Artist - Title (1:01)')

    def test_without_artist(self):
        self.assertEqual(marquee_text(None, 'Title', None), 'Title')
        self.assertEqual(marquee_text('', '  Title  ', None), 'Title')

    def test_without_duration(self):
        self.assertEqual(marquee_text('Artist', 'Title', None),
                         'Artist - Title')
        self.assertEqual(marquee_text('Artist', 'Title', 0), 'Artist - Title')

    def test_strips_whitespace(self):
        self.assertEqual(marquee_text('  Artist  ', '  Title  ', 65),
                         'Artist - Title (1:05)')

    def test_slice_short_text_padded(self):
        self.assertEqual(marquee_slice('ab', 3, 5), 'ab   ')

    def test_slice_long_text_wraps_past_separator(self):
        text = '0123456789'
        loop = text + MARQUEE_SEPARATOR
        n = len(loop)
        # offset near the end of the loop wraps into the duplicated copy
        self.assertEqual(marquee_slice(text, n - 2, 4), (loop + loop)[n - 2:n + 2])
        self.assertEqual(marquee_slice(text, n - 1, 4), (loop + loop)[n - 1:n + 3])
        self.assertEqual(marquee_slice(text, n, 4), loop[0:4])
        # offset wraps modulo loop length
        self.assertEqual(marquee_slice(text, 0, 4), marquee_slice(text, n, 4))
        self.assertEqual(marquee_slice(text, 5, 4), loop[5:9])


if __name__ == '__main__':
    unittest.main()
