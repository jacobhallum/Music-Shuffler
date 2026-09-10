"""
tests/test_preferences.py - durable favorite store (Task 7c).

Run:
    python -m unittest tests.test_preferences -v
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import preferences as pf


class TmpMixin:
    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self.path = os.path.join(self._dir, 'shuffler_preferences.json')

    def tearDown(self):
        for f in os.listdir(self._dir):
            os.unlink(os.path.join(self._dir, f))
        os.rmdir(self._dir)


class TestRoundTrip(TmpMixin, unittest.TestCase):
    def test_set_get_save_load(self):
        favs = {}
        favs = pf.set_favorite(favs, '/M/A.mp3', True, source='user', ts=1.0)
        favs = pf.set_favorite(favs, '/M/B.mp3', False, source='user', ts=2.0)
        pf.save(self.path, favs)

        loaded = pf.load(self.path)
        self.assertEqual(pf.get(loaded, '/m/a.mp3'), (True, 'user'))
        self.assertEqual(pf.get(loaded, '/M/B.MP3'), (False, 'user'))
        self.assertEqual(pf.get(loaded, '/M/missing.mp3'), (None, None))

    def test_missing_file_is_empty(self):
        self.assertEqual(pf.load(self.path), {})

    def test_corrupt_file_is_empty(self):
        with open(self.path, 'w') as f:
            f.write('{{bad')
        self.assertEqual(pf.load(self.path), {})

    def test_wrong_version_is_empty(self):
        with open(self.path, 'w') as f:
            json.dump({'version': 99, 'favorites': {'/x': {'loved': True}}}, f)
        self.assertEqual(pf.load(self.path), {})

    def test_save_atomic_no_tmp(self):
        pf.save(self.path, pf.set_favorite({}, '/a.mp3', True))
        self.assertEqual(os.listdir(self._dir), ['shuffler_preferences.json'])


class TestMerge(unittest.TestCase):
    def test_merge_into_metadata_prefers_preferences(self):
        favs = pf.set_favorite({}, '/m/a.mp3', True, source='user')
        meta = {
            os.path.normcase(os.path.normpath('/m/a.mp3')): {'loved': None, 'loved_source': None},
            os.path.normcase(os.path.normpath('/m/b.mp3')): {'loved': False, 'loved_source': 'user'},
        }
        pf.merge_into_metadata(favs, meta)
        a = meta[os.path.normcase(os.path.normpath('/m/a.mp3'))]
        self.assertEqual((a['loved'], a['loved_source']), (True, 'user'))
        # b untouched (not in prefs)
        b = meta[os.path.normcase(os.path.normpath('/m/b.mp3'))]
        self.assertEqual(b['loved'], False)

    def test_merge_creates_metadata_entry_when_absent(self):
        favs = pf.set_favorite({}, '/m/new.mp3', True)
        meta = {}
        pf.merge_into_metadata(favs, meta)
        self.assertIn(os.path.normcase(os.path.normpath('/m/new.mp3')), meta)


if __name__ == '__main__':
    unittest.main()
