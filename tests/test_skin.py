"""
tests/test_skin.py - loading .wsz skins with Pillow (synthetic in-memory skins; the real ones only when present).

Run with:
    python -m unittest tests.test_skin -v
"""

import glob
import io
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from PIL import Image  # noqa: E402

import skin  # noqa: E402

REPO = os.path.join(os.path.dirname(__file__), '..')


def bmp(size, color):
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, 'BMP')
    return buf.getvalue()


class SkinLoadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def wsz(self, members, name='t.wsz'):
        path = os.path.join(self.tmp.name, name)
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
            for member, data in members.items():
                z.writestr(member, data)
        return path

    def test_minimal_skin_loads(self):
        cb = Image.new('RGB', (136, 36), (0, 0, 255))
        cb.paste((0, 255, 0), (23, 0, 46, 18))           # the play button
        buf = io.BytesIO()
        cb.save(buf, 'BMP')
        s = skin.load_skin(self.wsz({'MAIN.BMP': bmp((275, 116), (200, 0, 0)), 'cbuttons.bmp': buf.getvalue()}))
        self.assertEqual(s.name, 't.wsz')
        main = s.sprite('MAIN_WINDOW_BACKGROUND')
        self.assertEqual(main.size, (275, 116))
        self.assertEqual(main.getpixel((10, 10)), (200, 0, 0, 255))
        self.assertEqual(s.sprite('MAIN_PLAY_BUTTON').getpixel((5, 5)), (0, 255, 0, 255))
        self.assertEqual(s.pledit['normal'], '#00ff00')
        self.assertEqual(len(s.viscolors), 24)
        self.assertEqual(s.regions, {})
        self.assertEqual(s.scaled(main, 2).size, (550, 232))
        self.assertIs(s.sprite('MAIN_PLAY_BUTTON'), s.sprite('MAIN_PLAY_BUTTON'))   # cached

    def test_short_and_missing_sheets_fall_back_per_piece(self):
        s = skin.load_skin(self.wsz({'main.bmp': bmp((275, 116), (1, 2, 3)),
                                     'balance.bmp': bmp((47, 13), (9, 9, 9))}))
        bg = s.sprite('MAIN_BALANCE_BACKGROUND')                # (9, 0, 38, 420) from a 47x13 sheet
        self.assertEqual(bg.size, (38, 420))
        self.assertEqual(bg.getpixel((0, 0)), (9, 9, 9, 255))
        self.assertEqual(bg.getpixel((0, 20)), skin.FALLBACK_RGBA)
        self.assertFalse(s.has('posbar.bmp'))
        self.assertEqual(s.sprite('MAIN_POSITION_SLIDER_BACKGROUND').getpixel((3, 3)), skin.FALLBACK_RGBA)
        self.assertEqual(s.sheet_size('balance.bmp'), (47, 13))
        self.assertIsNone(s.sheet_size('posbar.bmp'))

    def test_not_a_zip_and_no_main(self):
        bad = os.path.join(self.tmp.name, 'bad.wsz')
        with open(bad, 'wb') as f:
            f.write(b'not a zip')
        with self.assertRaises(skin.SkinError):
            skin.load_skin(bad)
        with self.assertRaises(skin.SkinError):
            skin.load_skin(self.wsz({'cbuttons.bmp': bmp((136, 36), (0, 0, 0))}))
        with self.assertRaises(skin.SkinError):
            skin.load_skin(self.wsz({'main.bmp': b'BM not really a bitmap'}))
        with self.assertRaises(skin.SkinError):
            skin.load_skin(os.path.join(self.tmp.name, 'missing.wsz'))

    def test_member_names_any_case_and_folder(self):
        s = skin.load_skin(self.wsz({'MySkin/Main.Bmp': bmp((275, 116), (5, 5, 5)),
                                     'MySkin/PLEDIT.TXT': '[Text]\r\nNormal=#112233\r\n'}))
        self.assertEqual(s.sprite('MAIN_WINDOW_BACKGROUND').getpixel((0, 0)), (5, 5, 5, 255))
        self.assertEqual(s.pledit['normal'], '#112233')

    def test_oversized_text_member_is_ignored(self):
        s = skin.load_skin(self.wsz({'main.bmp': bmp((275, 116), (5, 5, 5)),
                                     'pledit.txt': 'Normal=#112233\n' + ' ' * (5 * 1024 * 1024)}))
        self.assertEqual(s.pledit['normal'], '#00ff00')

    def test_list_skins(self):
        for n in ('b.WSZ', 'a.wsz', 'notes.txt'):
            open(os.path.join(self.tmp.name, n), 'w').close()
        self.assertEqual(skin.list_skins(self.tmp.name), ['a.wsz', 'b.WSZ'])
        self.assertEqual(skin.list_skins(os.path.join(self.tmp.name, 'nope')), [])

    def test_real_skins_load_when_present(self):
        paths = glob.glob(os.path.join(REPO, 'skins', '*.wsz'))
        if not paths:
            self.skipTest('no skins in skins/')
        for p in paths:
            s = skin.load_skin(p)
            self.assertEqual(s.sprite('MAIN_WINDOW_BACKGROUND').size, (275, 116))
            self.assertEqual(len(s.viscolors), 24)


if __name__ == '__main__':
    unittest.main()
