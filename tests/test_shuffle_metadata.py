"""
tests/test_shuffle_metadata.py - tri-state metadata round-trip (Task 7a).

Verifies that `loved` / `skips` / `last_played` / `date_added` keep their
None ("never set") state through a save -> load cycle instead of collapsing
to False / 0, and that a v1 file's bare `loved: False` is reclassified as
unknown on migration.

Run:
    python -m unittest tests.test_shuffle_metadata -v
"""

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import plistlib
from music_player import MusicPlayerGUI


class _FakeMeta:
    _load_player_metadata = MusicPlayerGUI._load_player_metadata
    _save_player_metadata = MusicPlayerGUI._save_player_metadata

    def __init__(self, path):
        self.player_metadata_file = Path(path)
        self.player_metadata = {}
        self._metadata_lock = threading.Lock()

    def _norm_path(self, p):
        return os.path.normcase(os.path.normpath(p or ""))


class TmpMixin:
    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self.path = os.path.join(self._dir, 'music_player_metadata.xml')

    def tearDown(self):
        for f in os.listdir(self._dir):
            os.unlink(os.path.join(self._dir, f))
        os.rmdir(self._dir)


class TestTriStateRoundTrip(TmpMixin, unittest.TestCase):

    def test_none_survives_save_load(self):
        w = _FakeMeta(self.path)
        w.player_metadata['/m/a.mp3'] = {
            'play_count': 3, 'rating': 0,
            'skip_count': None, 'last_played': None, 'date_added': None,
            'loved': None, 'loved_source': None,
        }
        w._save_player_metadata()

        r = _FakeMeta(self.path)
        r._load_player_metadata()
        e = r.player_metadata[r._norm_path('/m/a.mp3')]
        self.assertIsNone(e['loved'])
        self.assertIsNone(e['skip_count'])
        self.assertIsNone(e['last_played'])
        self.assertIsNone(e['date_added'])
        self.assertEqual(e['play_count'], 3)

    def test_explicit_false_and_true_survive_with_provenance(self):
        w = _FakeMeta(self.path)
        w.player_metadata['/m/hi.mp3'] = {
            'play_count': 1, 'rating': 0, 'skip_count': 2,
            'last_played': 111, 'date_added': 222,
            'loved': True, 'loved_source': 'user',
        }
        w.player_metadata['/m/lo.mp3'] = {
            'play_count': 1, 'rating': 0, 'skip_count': None,
            'last_played': None, 'date_added': None,
            'loved': False, 'loved_source': 'user',
        }
        w._save_player_metadata()

        r = _FakeMeta(self.path)
        r._load_player_metadata()
        hi = r.player_metadata[r._norm_path('/m/hi.mp3')]
        lo = r.player_metadata[r._norm_path('/m/lo.mp3')]
        self.assertEqual((hi['loved'], hi['loved_source']), (True, 'user'))
        self.assertEqual((lo['loved'], lo['loved_source']), (False, 'user'))
        self.assertEqual(hi['skip_count'], 2)


class TestV1Migration(TmpMixin, unittest.TestCase):

    def _write_v1(self, tracks):
        with open(self.path, 'wb') as f:
            plistlib.dump({'tracks': tracks}, f)   # no schema_version

    def test_bare_false_becomes_unknown(self):
        self._write_v1({
            '/m/played.mp3': {'play_count': 5, 'loved': False},
            '/m/hearted.mp3': {'play_count': 1, 'loved': True},
            '/m/nometa.mp3': {'play_count': 0},
        })
        r = _FakeMeta(self.path)
        r._load_player_metadata()
        played = r.player_metadata[r._norm_path('/m/played.mp3')]
        hearted = r.player_metadata[r._norm_path('/m/hearted.mp3')]
        self.assertIsNone(played['loved'], "v1 bare False must not be read as dislike")
        self.assertIsNone(played['loved_source'])
        self.assertEqual((hearted['loved'], hearted['loved_source']), (True, 'user'))

    def test_v1_backup_written(self):
        self._write_v1({'/m/x.mp3': {'play_count': 1, 'loved': True}})
        r = _FakeMeta(self.path)
        r._load_player_metadata()
        self.assertTrue(Path(self.path).with_suffix('.xml.v1backup').exists())

    def test_v1_zero_fields_become_none(self):
        self._write_v1({'/m/x.mp3': {'play_count': 2, 'skip_count': 0,
                                     'last_played': 0, 'date_added': 0}})
        r = _FakeMeta(self.path)
        r._load_player_metadata()
        e = r.player_metadata[r._norm_path('/m/x.mp3')]
        self.assertIsNone(e['skip_count'])
        self.assertIsNone(e['last_played'])
        self.assertIsNone(e['date_added'])


if __name__ == '__main__':
    unittest.main()
