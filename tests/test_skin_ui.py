"""
tests/test_skin_ui.py - the skinned main, EQ and playlist windows (real Tk, hidden root, synthetic skin).

Run with:
    python -m unittest tests.test_skin_ui -v
"""

import io
import os
import struct
import sys
import tempfile
import unittest
import zipfile
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.withdraw()
except Exception:
    _root = None

from PIL import Image  # noqa: E402

import skin  # noqa: E402

if _root is not None:
    import skin_playlist  # noqa: E402
    import skin_ui  # noqa: E402


def _bmp(size, color):
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, 'BMP')
    return buf.getvalue()


def _skin(tmp):
    path = os.path.join(tmp, 'syn.wsz')
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('main.bmp', _bmp((275, 116), (10, 10, 10)))
        z.writestr('eqmain.bmp', _bmp((275, 315), (20, 20, 20)))
        z.writestr('text.bmp', _bmp((155, 18), (200, 200, 200)))
        z.writestr('pledit.bmp', _bmp((280, 186), (30, 30, 30)))
    return skin.load_skin(path)


def ev(x, y, **kw):
    return SimpleNamespace(x=x, y=y, x_root=x + 100, y_root=y + 100, **kw)


def rle8_bmp():
    """A 4x2 BI_RLE8 bitmap: bottom row 'run of 2 red', then a top row from an absolute run [1, 0, 1]."""
    palette = bytes([0, 0, 0, 0, 0, 0, 255, 0])                     # index 0 black, 1 red (BGRA)
    rle = bytes([2, 1, 0, 0, 0, 3, 1, 0, 1, 0, 0, 1])            # run, EOL, absolute(3)+pad, EOB
    off = 14 + 40 + len(palette)
    header = b'BM' + struct.pack('<IHHI', off + len(rle), 0, 0, off)
    info = struct.pack('<IiiHHIIiiII', 40, 4, 2, 1, 8, 1, len(rle), 0, 0, 2, 0)
    return header + info + palette + rle


class RleTests(unittest.TestCase):
    def test_rle8_bitmap_is_decoded_by_hand(self):
        img = skin._decode_rle8(rle8_bmp())
        self.assertEqual(img.size, (4, 2))
        red, black = (255, 0, 0, 255), (0, 0, 0, 255)
        self.assertEqual([img.getpixel((x, 1)) for x in range(4)], [red, red, black, black])
        self.assertEqual([img.getpixel((x, 0)) for x in range(4)], [red, black, red, black])
        self.assertIsNone(skin._decode_rle8(b'not a bitmap'))


@unittest.skipIf(_root is None, "no display")
class SkinWindowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.skin = _skin(self.tmp.name)
        self.calls = []

    def cb(self, *names):
        return {n: (lambda *a, n=n: self.calls.append((n,) + a)) for n in names}

    def test_main_window_draws_and_buttons_call_back(self):
        w = skin_ui.SkinMainWindow(_root, self.skin, 1, self.cb('play', 'next', 'eq', 'close', 'heart', 'menu'))
        self.addCleanup(w.destroy)
        w.withdraw()
        w.update_state(play_state='playing', elapsed=10, duration=100, title='A - B (1:40)')
        for name, (x, y) in (('play', (45, 95)), ('next', (115, 95)), ('eq', (225, 62)), ('close', (268, 6)),
                             ('heart', (103, 31))):
            w._on_press(ev(x, y))
            w._on_release(ev(x, y))
        self.assertEqual([c[0] for c in self.calls], ['play', 'next', 'eq', 'close', 'heart'])
        self.calls.clear()
        w._on_press(ev(45, 95))
        w._on_release(ev(150, 95))     # released somewhere else: no click
        self.assertEqual(self.calls, [])

    def test_seek_and_volume_drag(self):
        w = skin_ui.SkinMainWindow(_root, self.skin, 2, self.cb('seek', 'volume'))
        self.addCleanup(w.destroy)
        w.withdraw()
        w.update_state(play_state='playing', elapsed=0, duration=100)
        w._on_press(ev(2 * 140, 2 * 76))
        w._on_motion(ev(2 * 140, 2 * 76))
        w._on_release(ev(2 * 140, 2 * 76))
        seek = [c for c in self.calls if c[0] == 'seek']
        self.assertEqual(len(seek), 1)
        self.assertAlmostEqual(seek[0][1], 0.5, places=2)
        w._on_press(ev(2 * 174, 2 * 62))    # far right of the volume bar
        w._on_release(ev(2 * 174, 2 * 62))
        vols = [c[1] for c in self.calls if c[0] == 'volume']
        self.assertTrue(vols and vols[-1] == 100)

    def test_marquee_status_then_title(self):
        w = skin_ui.SkinMainWindow(_root, self.skin, 1, {})
        self.addCleanup(w.destroy)
        w.withdraw()
        w.update_state(title='Song')
        w.flash('VOLUME: 50%', ms=10)
        self.assertEqual(w.flash_text, 'VOLUME: 50%')
        _root.after(50, _root.quit)
        _root.mainloop()
        self.assertIsNone(w.flash_text)

    def test_eq_sliders_emit_settings(self):
        w = skin_ui.SkinEqWindow(_root, self.skin, 1, self.cb('change', 'presets'))
        self.addCleanup(w.destroy)
        w.withdraw()
        w._on_press(ev(150 + 7, 38 + 5))       # top of band4 -> about +12 dB
        w._on_release(ev(150 + 7, 38 + 5))
        bands, preamp, enabled = self.calls[-1][1:]
        self.assertGreater(bands[4], 10)
        self.assertEqual((preamp, enabled), (0.0, True))
        w._on_press(ev(20, 22))                # ON toggles
        w._on_release(ev(20, 22))
        self.assertFalse(self.calls[-1][3])
        w._on_press(ev(230, 22))
        w._on_release(ev(230, 22))
        self.assertEqual(self.calls[-1][0], 'presets')

    def test_playlist_rows_and_double_click(self):
        w = skin_playlist.SkinPlaylistWindow(_root, self.skin, 1, self.cb('play', 'search'))
        self.addCleanup(w.destroy)
        w.withdraw()
        w.set_rows(['1. A', '2. B', '3. C'], current=1)
        self.assertEqual(w.listbox.size(), 3)
        self.assertEqual(w.listbox.itemcget(1, 'fg'), self.skin.pledit['current'])
        w.listbox.selection_set(2)
        w._on_double(None)
        self.assertEqual(self.calls[-1], ('play', 2))
        w.search_var.set('b')
        self.assertEqual(self.calls[-1], ('search', 'b'))
        w.set_height(200)
        self.assertEqual(w.height, 116 + 3 * 29)

    def test_double_size_scales_canvas(self):
        w = skin_ui.SkinMainWindow(_root, self.skin, 1, {})
        self.addCleanup(w.destroy)
        w.withdraw()
        w.set_skin(self.skin, 2)
        self.assertEqual((int(w.canvas.cget('width')), int(w.canvas.cget('height'))), (550, 232))

    def test_triple_size_scales_canvas(self):
        w = skin_ui.SkinMainWindow(_root, self.skin, 3, {})
        self.addCleanup(w.destroy)
        w.withdraw()
        self.assertEqual((int(w.canvas.cget('width')), int(w.canvas.cget('height'))), (825, 348))


if __name__ == '__main__':
    unittest.main()
