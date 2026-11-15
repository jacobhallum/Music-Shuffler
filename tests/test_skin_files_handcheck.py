"""
tests/test_skin_files_handcheck.py - hand-worked checks for skin_files.py (hidden from the worker).

Run with:
    python -m unittest tests.test_skin_files_handcheck -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import skin_files as sf

MB = 1024 * 1024


class SkinFilesHandcheck(unittest.TestCase):
    def test_member_map(self):
        names = ['Skin/', 'Skin/MAIN.BMP', 'Skin/Eqmain.BMP', 'main.bmp', 'Skin\\sub\\PlEdit.txt', 'x/']
        self.assertEqual(sf.member_map(names), {'main.bmp': 'Skin/MAIN.BMP', 'eqmain.bmp': 'Skin/Eqmain.BMP',
                                                'pledit.txt': 'Skin\\sub\\PlEdit.txt'})

    def test_size_limits(self):
        infos = [('a', 3 * MB), ('huge', 5 * MB), ('b', 4 * MB), ('c', 10 * MB), ('d', 4 * MB), ('e', 4 * MB),
                 ('f', 4 * MB), ('g', 1 * MB), ('h', 1)]
        # a3 b4 d4 e4 f4 = 19 ... c is 10 MB > 4 MB limit -> skipped; g 1 -> 20; h -> fine
        self.assertEqual(sf.allowed_members(infos), {'a', 'b', 'd', 'e', 'f', 'g', 'h'})
        infos = [('m%d' % i, 4 * MB) for i in range(9)]   # 7 fit (28 MB), the 8th would make 32
        self.assertEqual(sf.allowed_members(infos), {'m%d' % i for i in range(7)})
        infos = [('a', 4 * MB)] * 7 + [('b', 4 * MB), ('c', 1)]
        self.assertNotIn('c', sf.allowed_members(infos))   # once the cut happens, later ones are out too

    def test_pledit(self):
        text = ("[Text]\r\nNormal=#00FF00\r\nCurrent = FFFFFF ; white\r\nNormalBG=#12345\r\n"
                "SelectedBG=#0000Ff // blue\r\nFont=Tahoma\r\nmbFG=#ffffff\r\n")
        self.assertEqual(sf.parse_pledit(text), {'normal': '#00ff00', 'current': '#ffffff', 'normalbg': '#000000',
                                                 'selectedbg': '#0000ff', 'font': 'Tahoma'})
        self.assertEqual(sf.parse_pledit(''), sf.DEFAULT_PLEDIT)
        self.assertIsNot(sf.parse_pledit(''), sf.DEFAULT_PLEDIT)

    def test_viscolor(self):
        self.assertEqual(len(sf.DEFAULT_VISCOLORS), 24)
        text = "0,0,0, // bg\n junk\n300, 2 ,3 // clamp\n"
        got = sf.parse_viscolor(text)
        self.assertEqual(len(got), 24)
        self.assertEqual(got[:2], [(0, 0, 0), (255, 2, 3)])
        self.assertEqual(got[2:], sf.DEFAULT_VISCOLORS[2:])
        full = '\n'.join(f'{i},{i},{i}' for i in range(30))
        self.assertEqual(sf.parse_viscolor(full), [(i, i, i) for i in range(24)])

    def test_region(self):
        text = ("; shape\n[Normal]\nNumPoints = 4, 3\nPointList=0,0, 275,0 275,116  0,116, 1,1 2,1 2,2\n"
                "[Equalizer]\nNumPoints=4\nPointList=0,0,1,1,2,2\n[Other]\nNumPoints=3\nPointList=0,0,1,0,1,1\n"
                "[WindowShade]\nNumPoints=2\nPointList=0,0,1,1\n")
        self.assertEqual(sf.parse_region(text), {'normal': [[(0, 0), (275, 0), (275, 116), (0, 116)],
                                                            [(1, 1), (2, 1), (2, 2)]]})
        self.assertEqual(sf.parse_region(''), {})


if __name__ == '__main__':
    unittest.main()
