"""
tests/test_skin_layout_handcheck.py - hand-worked checks for skin_layout.py (hidden from the worker).

Run with:
    python -m unittest tests.test_skin_layout_handcheck -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import skin_layout as sl


class LayoutHandcheck(unittest.TestCase):
    def test_tables(self):
        self.assertEqual(list(sl.MAIN_LAYOUT)[0], 'menu')
        self.assertEqual(list(sl.MAIN_LAYOUT)[-1], 'titlebar')
        self.assertEqual(sl.MAIN_LAYOUT['eject'], (136, 89, 22, 16))
        self.assertEqual(len(sl.EQ_LAYOUT), 18)
        self.assertEqual(sl.EQ_LAYOUT['band9'], (240, 38, 14, 63))
        self.assertEqual(list(sl.EQ_LAYOUT)[-1], 'titlebar')

    def test_hit_test(self):
        h = sl.hit_test
        M = sl.MAIN_LAYOUT
        self.assertEqual(h(M, 16, 88), 'previous')
        self.assertEqual(h(M, 38, 105), 'previous')
        self.assertEqual(h(M, 39, 88), 'play')
        self.assertEqual(h(M, 129, 90), 'next')
        self.assertEqual(h(M, 130, 90), None)
        self.assertEqual(h(M, 268, 6), 'close')
        self.assertEqual(h(M, 150, 6), 'titlebar')
        self.assertEqual(h(M, 2 * 268 + 1, 2 * 6, scale=2), 'close')
        self.assertEqual(h(M, 300, 50), None)
        self.assertEqual(h(sl.EQ_LAYOUT, 150, 60), 'band4')
        self.assertEqual(h(sl.EQ_LAYOUT, 25, 99), 'preamp')

    def test_sliders(self):
        self.assertEqual(sl.slider_value(16, 16, 248, 29, 0, 1), 0)
        self.assertAlmostEqual(sl.slider_value(16 + 14.5 + 219 / 2, 16, 248, 29, 0, 1), 0.5)
        self.assertEqual(sl.slider_value(999, 16, 248, 29, 0, 1), 1)
        self.assertEqual(sl.slider_value(5, 107, 68, 14, 0, 100), 0)
        self.assertEqual(sl.slider_thumb_x(0, 107, 68, 14, 0, 100), 107)
        self.assertEqual(sl.slider_thumb_x(100, 107, 68, 14, 0, 100), 161)
        self.assertEqual(sl.slider_thumb_x(50, 107, 68, 14, 0, 100), 134)
        self.assertEqual(sl.slider_thumb_x(150, 107, 68, 14, 0, 100), 161)
        self.assertEqual(sl.slider_thumb_x(0, 177, 38, 14, -100, 100), 189)

    def test_frames(self):
        self.assertEqual([sl.volume_frame(p) for p in (0, 50, 100, 130)], [0, 14, 27, 27])
        self.assertEqual([sl.balance_frame(b) for b in (-100, 0, 50)], [27, 0, 14])
        self.assertEqual([sl.eq_frame(d) for d in (-12, 0, 12, 20)], [0, 14, 27, 27])
        self.assertEqual([sl.eq_thumb_y(d) for d in (12, 0, -12)], [0, 26, 51])

    def test_time_and_text(self):
        self.assertEqual(sl.time_digits(0), (0, 0, 0, 0))
        self.assertEqual(sl.time_digits(61.9), (0, 1, 0, 1))
        self.assertEqual(sl.time_digits(3599), (5, 9, 5, 9))
        self.assertEqual(sl.time_digits(6000), (9, 9, 5, 9))
        self.assertEqual(sl.time_digits(None), (0, 0, 0, 0))
        self.assertEqual(sl.text_cells('aA1~'), [(0, 0), (0, 0), (1, 1), (0, 30)])
        self.assertEqual(sl.text_cells('å'), [(2, 0)])

    def test_marquee(self):
        self.assertEqual(sl.marquee_text(' Art ', 'Song', 225), 'Art - Song (3:45)')
        self.assertEqual(sl.marquee_text(None, 'Song', 0), 'Song')
        self.assertEqual(sl.marquee_text('', 'Song', 5), 'Song (0:05)')
        self.assertEqual(sl.marquee_slice('abc', 7, 5), 'abc  ')
        self.assertEqual(sl.marquee_slice('abcdef', 0, 4), 'abcd')
        self.assertEqual(sl.marquee_slice('abcdef', 5, 4), 'f  *')
        self.assertEqual(sl.marquee_slice('abcdef', 13, 4), 'abcd')
        self.assertEqual(sl.marquee_slice('abcdef', 10, 4), '*  a')


if __name__ == '__main__':
    unittest.main()
