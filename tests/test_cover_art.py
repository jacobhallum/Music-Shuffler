"""
tests/test_cover_art.py - cover_art: shrinking the cover, the letterboxed square drawn in a skin's colours, and the
Deep Cuts fallback. Pillow only, no display.

Run with:
    python -m unittest tests.test_cover_art -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from PIL import Image  # noqa: E402

import cover_art  # noqa: E402

RED, BLUE = (255, 0, 0), (0, 0, 255)


class ShrinkTests(unittest.TestCase):
    def test_shrink_caps_and_keeps_mode(self):
        src = Image.new('RGBA', (2000, 1000), (1, 2, 3, 255))
        out = cover_art.shrink(src)
        self.assertEqual(max(out.size), 522)
        self.assertEqual(out.mode, 'RGBA')
        self.assertEqual(src.size, (2000, 1000))

    def test_shrink_leaves_small_images(self):
        src = Image.new('RGB', (300, 300))
        out = cover_art.shrink(src)
        self.assertEqual(out.size, (300, 300))
        self.assertIsNot(out, src)


class RenderTests(unittest.TestCase):
    def test_render_letterboxes_with_bg(self):
        out = cover_art.render_square(Image.new('RGB', (200, 100), RED), 243, '#102030')
        self.assertEqual(out.size, (243, 243))
        self.assertEqual(out.mode, 'RGB')
        self.assertEqual(out.getpixel((121, 121)), RED)
        self.assertEqual(out.getpixel((121, 2)), (16, 32, 48))

    def test_render_composites_alpha_on_bg(self):
        out = cover_art.render_square(Image.new('RGBA', (50, 50), (255, 0, 0, 0)), 243, '#102030')
        self.assertEqual(out.getpixel((121, 121)), (16, 32, 48))

    def test_render_modes(self):
        p = Image.new('P', (40, 40), 0)
        p.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
        p.info['transparency'] = 0
        for img in (p, Image.new('LA', (40, 40), (128, 255)), Image.new('CMYK', (40, 40), (0, 255, 255, 0)),
                    Image.new('L', (40, 40), 90), Image.new('1', (40, 40), 1)):
            with self.subTest(mode=img.mode):
                out = cover_art.render_square(img, 243, '#000000')
                self.assertEqual((out.mode, out.size), ('RGB', (243, 243)))

    def test_render_extreme_shapes(self):
        for size in ((3000, 10), (1, 1)):
            with self.subTest(size=size):
                out = cover_art.render_square(Image.new('RGB', size, RED), 243, '#000000')
                self.assertEqual(out.size, (243, 243))

    def test_render_none_is_plain_bg(self):
        out = cover_art.render_square(None, 486, '#000000')
        self.assertEqual(out.size, (486, 486))
        self.assertEqual(out.getextrema(), ((0, 0), (0, 0), (0, 0)))


class FallbackTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.assets = os.path.join(self.dir, 'assets')
        os.makedirs(self.assets)

    def png(self, data=None):
        path = os.path.join(self.assets, 'app_icon_1024.png')
        if data is None:
            Image.new('RGB', (1024, 1024), BLUE).save(path)
        else:
            with open(path, 'wb') as f:
                f.write(data)

    def ico(self):
        Image.new('RGB', (256, 256), RED).save(os.path.join(self.assets, 'app_icon.ico'), sizes=[(256, 256)])

    def test_fallback_png_first(self):
        self.png()
        self.ico()
        out = cover_art.load_fallback(self.dir)
        self.assertEqual(max(out.size), 522)
        self.assertEqual(out.convert('RGB').getpixel((10, 10)), BLUE)

    def test_fallback_ico_when_png_missing(self):
        self.ico()
        out = cover_art.load_fallback(self.dir)
        self.assertEqual(out.size, (256, 256))
        self.assertEqual(out.convert('RGB').getpixel((10, 10)), RED)

    def test_fallback_corrupt_png_uses_ico(self):
        self.png(b'nope')
        self.ico()
        out = cover_art.load_fallback(self.dir)
        self.assertEqual(out.convert('RGB').getpixel((10, 10)), RED)

    def test_fallback_none_when_nothing(self):
        self.assertIsNone(cover_art.load_fallback(self.dir))

    def test_fallback_accepts_a_path_object(self):
        from pathlib import Path
        self.png()
        self.assertIsNotNone(cover_art.load_fallback(Path(self.dir)))


if __name__ == '__main__':
    unittest.main()
