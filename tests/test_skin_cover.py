"""
tests/test_skin_cover.py - SkinCoverWindow: the album-art window in a skin's playlist frame (plain frame, no resize
grip, one picture). Synthetic skin; run on the hidden desktop.

Run with:
    python scripts/run_hidden.py python -m unittest tests.test_skin_cover -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    import tkinter as tk
    from tests.test_skin_ui import _root, _skin, ev
except Exception:
    _root = None

if _root is not None:
    from PIL import Image  # noqa: E402

    import skin_cover  # noqa: E402


@unittest.skipIf(_root is None, "no display")
class SkinCoverWindowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.skin = _skin(self.tmp.name)
        self.calls = []

    def cb(self, *names):
        return {n: (lambda *a, n=n: self.calls.append((n,) + a)) for n in names}

    def window(self, scale=1, callbacks=None):
        w = skin_cover.SkinCoverWindow(_root, self.skin, scale, callbacks or {})
        self.addCleanup(lambda: w.winfo_exists() and w.destroy())
        w.update_idletasks()
        return w

    def test_size_follows_scale(self):
        for s in (1, 2, 3):
            with self.subTest(scale=s):
                w = self.window(s)
                self.assertEqual((w.winfo_reqwidth(), w.winfo_reqheight()), (275 * s, 232 * s))
        self.assertEqual(skin_cover.COVER_HEIGHT, 232)

    def test_no_resize_grip(self):
        w = self.window()
        self.assertIsNone(w._hit(ev(270, 228)))
        w._on_press(ev(270, 228))
        w._on_motion(ev(270, 400))
        w._on_release(ev(270, 400))
        self.assertEqual(w.height, 232)
        w.set_height(500)
        self.assertEqual(w.height, 232)

    def test_plain_frame(self):
        names, orig = [], self.skin.sprite
        self.skin.sprite = lambda n: (names.append(n), orig(n))[1]
        self.window()
        self.assertFalse([n for n in names if n.startswith('PLAYLIST_TITLE_BAR')])
        self.assertGreaterEqual(names.count('PLAYLIST_BOTTOM_TILE'), 9)

    def test_picture_survives_focus_redraw(self):
        w = self.window()
        w.set_picture(Image.new('RGB', (243, 243), (255, 0, 0)))
        before, photo = w.art.cget('image'), w._art_photo
        self.assertTrue(before)
        w._set_focus(True)
        self.assertEqual(w.art.cget('image'), before)
        self.assertIs(w._art_photo, photo)

    def test_close_fires_close_and_label_text(self):
        w = self.window(callbacks=self.cb('close'))
        w._on_press(ev(270, 5))
        w._on_release(ev(270, 5))
        self.assertEqual(self.calls, [('close',)])
        self.assertEqual(w._hover_key('close'), ('close', 'Hide album art'))
        self.assertIsNone(w._hover_key('resize'))

    def test_right_click_on_art_fires_menu(self):
        w = self.window(callbacks=self.cb('menu'))
        w.update()
        w.art.event_generate('<Button-3>', x=5, y=5)
        w.update()
        self.assertEqual([c[0] for c in self.calls], ['menu'])
        self.assertGreaterEqual(len(self.calls[0]), 3)          # ('menu', x_root, y_root, ...)

    def test_destroy_drops_picture(self):
        w = self.window()
        w.set_picture(Image.new('RGB', (243, 243)))
        w.destroy()
        self.assertIsNone(w._art_photo)


if __name__ == '__main__':
    unittest.main()
