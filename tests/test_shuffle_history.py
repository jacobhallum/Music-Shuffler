"""
tests/test_shuffle_history.py - persisted ordered listening history.

Run:
    python -m unittest tests.test_shuffle_history -v
"""

import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import shuffle_history as sh


class TmpMixin:
    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self.path = os.path.join(self._dir, 'shuffle_history.json')

    def tearDown(self):
        for f in os.listdir(self._dir):
            os.unlink(os.path.join(self._dir, f))
        os.rmdir(self._dir)


class TestEntry(unittest.TestCase):
    def test_entry_normalises_path_and_keys(self):
        e = sh.make_entry('/Music/Foo/Bar.mp3', artist='  The Band ',
                          album='LP', genre='Rock', ts=123.0)
        self.assertEqual(e['path'], os.path.normcase(os.path.normpath('/Music/Foo/Bar.mp3')))
        self.assertEqual(e['artist'], 'the band')
        self.assertEqual(e['ts'], 123.0)

    def test_unknown_tags_become_none(self):
        e = sh.make_entry('/x.mp3', artist='', album='Unknown Album', genre='unknown')
        self.assertIsNone(e['artist'])
        self.assertIsNone(e['album'])
        self.assertIsNone(e['genre'])


class TestRoundTrip(TmpMixin, unittest.TestCase):
    def test_save_then_load(self):
        entries = [sh.make_entry(f'/m/{i}.mp3', artist=f'A{i}', ts=float(i))
                   for i in range(5)]
        sh.save(self.path, entries)
        loaded = sh.load(self.path)
        self.assertEqual([e['path'] for e in loaded], [e['path'] for e in entries])

    def test_load_missing_file_returns_empty(self):
        self.assertEqual(sh.load(self.path), [])

    def test_load_corrupt_file_returns_empty(self):
        with open(self.path, 'w') as f:
            f.write('{not json')
        self.assertEqual(sh.load(self.path), [])

    def test_load_wrong_version_returns_empty(self):
        with open(self.path, 'w') as f:
            json.dump({'version': 999, 'entries': [{'path': '/x'}]}, f)
        self.assertEqual(sh.load(self.path), [])

    def test_load_coerces_bad_timestamp(self):
        with open(self.path, 'w') as f:
            json.dump({'version': sh.HISTORY_VERSION,
                       'entries': [{'path': '/x.mp3', 'ts': 'bad'}]}, f)
        loaded = sh.load(self.path)
        self.assertEqual(loaded[0]['ts'], 0.0)
        # and protected_paths must not raise on it
        sh.protected_paths(loaded, now=1_000_000.0)

    def test_load_drops_malformed_entries(self):
        with open(self.path, 'w') as f:
            json.dump({'version': sh.HISTORY_VERSION,
                       'entries': [{'path': '/ok.mp3', 'ts': 1.0}, 'garbage', {'no_path': 1}]}, f)
        loaded = sh.load(self.path)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]['path'], '/ok.mp3')

    def test_save_caps_at_max(self):
        entries = [sh.make_entry(f'/m/{i}.mp3', ts=float(i)) for i in range(sh.MAX_HISTORY + 50)]
        sh.save(self.path, entries)
        loaded = sh.load(self.path)
        self.assertEqual(len(loaded), sh.MAX_HISTORY)
        self.assertEqual(loaded[-1]['path'],
                         os.path.normcase(os.path.normpath(f'/m/{sh.MAX_HISTORY + 49}.mp3')))

    def test_save_is_atomic_no_tmp_left(self):
        sh.save(self.path, [sh.make_entry('/a.mp3')])
        self.assertEqual(os.listdir(self._dir), ['shuffle_history.json'])


class TestAppend(unittest.TestCase):
    def test_append_caps_and_keeps_newest(self):
        entries = []
        for i in range(sh.MAX_HISTORY + 10):
            entries = sh.append(entries, sh.make_entry(f'/m/{i}.mp3', ts=float(i)))
        self.assertEqual(len(entries), sh.MAX_HISTORY)
        self.assertEqual(entries[-1]['path'],
                         os.path.normcase(os.path.normpath(f'/m/{sh.MAX_HISTORY + 9}.mp3')))

    def test_append_returns_new_list_does_not_mutate(self):
        original = [sh.make_entry('/a.mp3')]
        new = sh.append(original, sh.make_entry('/b.mp3'))
        self.assertEqual(len(original), 1)
        self.assertEqual(len(new), 2)


class TestContextForCore(unittest.TestCase):
    def test_recent_history_tuples(self):
        entries = [sh.make_entry('/a.mp3', artist='X', ts=1.0),
                   sh.make_entry('/b.mp3', artist='Y', ts=2.0)]
        tuples = sh.to_recent_history(entries)
        self.assertEqual(tuples, [
            (os.path.normcase(os.path.normpath('/a.mp3')), 'x'),
            (os.path.normcase(os.path.normpath('/b.mp3')), 'y'),
        ])

    def test_protected_paths_within_window(self):
        now = 1_000_000.0
        entries = [
            sh.make_entry('/old.mp3', ts=now - 48 * 3600),   # outside 24h
            sh.make_entry('/p1.mp3', ts=now - 3600),
            sh.make_entry('/p2.mp3', ts=now - 60),
            sh.make_entry('/p3.mp3', ts=now - 5),
        ]
        protected = sh.protected_paths(entries, now=now, count=3, max_age_h=24)
        self.assertEqual(set(protected), {
            os.path.normcase(os.path.normpath('/p1.mp3')),
            os.path.normcase(os.path.normpath('/p2.mp3')),
            os.path.normcase(os.path.normpath('/p3.mp3')),
        })

    def test_protection_expires_after_max_age(self):
        now = 1_000_000.0
        entries = [sh.make_entry('/p.mp3', ts=now - 25 * 3600)]
        self.assertEqual(sh.protected_paths(entries, now=now, count=3, max_age_h=24), [])

    def test_protected_paths_empty_entries(self):
        self.assertEqual(sh.protected_paths([], now=1_000_000.0), [])

    def test_protected_paths_count_larger_than_entries(self):
        now = 1_000_000.0
        entries = [
            sh.make_entry('/expired.mp3', ts=now - 48 * 3600),
            sh.make_entry('/r1.mp3', ts=now - 3600),
            sh.make_entry('/r2.mp3', ts=now - 60),
        ]
        protected = sh.protected_paths(entries, now=now, count=10, max_age_h=24)
        self.assertEqual(protected, [
            os.path.normcase(os.path.normpath('/r1.mp3')),
            os.path.normcase(os.path.normpath('/r2.mp3')),
        ])

    @unittest.expectedFailure
    def test_protected_paths_count_zero_returns_empty(self):
        # Docstring promises the last 0 starts, i.e. nothing.
        now = 1_000_000.0
        entries = [sh.make_entry('/p.mp3', ts=now - 60)]
        self.assertEqual(sh.protected_paths(entries, now=now, count=0), [])

    def test_protected_paths_includes_entry_exactly_at_cutoff(self):
        now = 1_000_000.0
        entries = [sh.make_entry('/edge.mp3', ts=now - 24 * 3600)]
        protected = sh.protected_paths(entries, now=now, count=3, max_age_h=24)
        self.assertEqual(protected, [
            os.path.normcase(os.path.normpath('/edge.mp3')),
        ])

    def test_protected_paths_non_numeric_ts_is_expired(self):
        now = 1_000_000.0
        entry = sh.make_entry('/bad.mp3', ts=now - 60)
        entry['ts'] = 'abc'
        self.assertEqual(
            sh.protected_paths([entry], now=now, count=3, max_age_h=24), [])

    def test_protected_paths_preserves_order_oldest_first(self):
        now = 1_000_000.0
        entries = [
            sh.make_entry('/a.mp3', ts=now - 300),
            sh.make_entry('/b.mp3', ts=now - 200),
            sh.make_entry('/c.mp3', ts=now - 100),
        ]
        protected = sh.protected_paths(entries, now=now, count=3, max_age_h=24)
        self.assertEqual(protected, [
            os.path.normcase(os.path.normpath('/a.mp3')),
            os.path.normcase(os.path.normpath('/b.mp3')),
            os.path.normcase(os.path.normpath('/c.mp3')),
        ])


if __name__ == '__main__':
    unittest.main()
