"""
tests/test_listening_log.py - stdlib-only unit tests for listening_log.py

Run with:
    python -m unittest tests/test_listening_log.py -v
"""

import json
import os
import sys
import tempfile
import unittest

# Allow running from project root or tests/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import listening_log
from listening_log import make_record, append, read


def _base_record(**overrides):
    kwargs = dict(
        started_at=1000.9,
        ended_at=1240.4,
        path=os.path.join('music', 'Song.mp3'),
        title='Song',
        artist='Artist',
        album='Album',
        genre='Rock',
        duration_sec=240.0,
        audible_sec=123.45,
        reason='natural_end',
        qualified=True,
        skip=False,
        transition='gapless',
    )
    kwargs.update(overrides)
    return kwargs


class MakeRecordTests(unittest.TestCase):
    """make_record builds the fixed key set with normalised values."""

    def test_field_values_and_rounding(self):
        rec = make_record(**_base_record())
        expected_path = os.path.normcase(os.path.normpath(
            os.path.join('music', 'Song.mp3')))
        self.assertEqual(set(rec.keys()), set(
            ['v', 'start', 'end', 'path', 'title', 'artist', 'album',
             'genre', 'dur', 'heard', 'reason', 'q', 'skip', 'tr']))
        self.assertEqual(rec['v'], listening_log.VERSION)
        self.assertEqual(rec['start'], 1000)   # truncated toward zero
        self.assertEqual(rec['end'], 1240)
        self.assertEqual(rec['path'], expected_path)
        self.assertEqual(rec['title'], 'Song')
        self.assertEqual(rec['artist'], 'Artist')
        self.assertEqual(rec['album'], 'Album')
        self.assertEqual(rec['genre'], 'Rock')
        self.assertEqual(rec['dur'], 240.0)
        self.assertEqual(rec['heard'], 123.5)  # round(..., 1)
        self.assertEqual(rec['reason'], 'natural_end')
        self.assertTrue(rec['q'])
        self.assertFalse(rec['skip'])
        self.assertEqual(rec['tr'], 'gapless')

    def test_none_tags_become_empty_strings(self):
        rec = make_record(**_base_record(genre=None))
        self.assertEqual(rec['genre'], '')

    def test_bad_reason_raises(self):
        with self.assertRaises(ValueError):
            make_record(**_base_record(reason='shuffle'))

    def test_bad_transition_raises(self):
        with self.assertRaises(ValueError):
            make_record(**_base_record(transition='fade'))


class ContextFieldTests(unittest.TestCase):
    """make_record stores an optional 'ctx' key after 'tr'."""

    def test_no_ctx_means_no_ctx_key(self):
        rec = make_record(**_base_record())
        self.assertNotIn('ctx', rec)

    def test_album_ctx_stored_after_tr(self):
        rec = make_record(**_base_record(ctx='album'))
        self.assertEqual(rec['ctx'], 'album')
        keys = list(rec.keys())
        self.assertEqual(keys.index('ctx'), keys.index('tr') + 1)

    def test_artist_ctx_stored(self):
        rec = make_record(**_base_record(ctx='artist'))
        self.assertEqual(rec['ctx'], 'artist')

    def test_unknown_ctx_raises(self):
        with self.assertRaises(ValueError):
            make_record(**_base_record(ctx='shuffle'))
        with self.assertRaises(ValueError):
            make_record(**_base_record(ctx=''))

    def test_ctx_record_survives_append_and_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, 'listening.jsonl')
            rec = make_record(**_base_record(ctx='album'))
            append(log_path, rec)
            records = read(log_path)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]['ctx'], 'album')


class AppendReadTests(unittest.TestCase):
    """append writes one JSON line per record; read returns them in order."""

    def test_append_twice_then_read_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, 'listening.jsonl')
            rec1 = make_record(**_base_record(title='First'))
            rec2 = make_record(**_base_record(title='Second',
                                              reason='next', skip=True))
            append(log_path, rec1)
            append(log_path, rec2)
            records = read(log_path)
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]['title'], 'First')
            self.assertEqual(records[1]['title'], 'Second')

    def test_read_skips_bad_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, 'listening.jsonl')
            good = make_record(**_base_record(title='Good'))
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write('\n')                                        # blank
                f.write('{not json}\n')                              # broken
                f.write('[1, 2, 3]\n')                               # non-dict
                f.write('{"v": 2, "x": 1}\n')                        # wrong v
                f.write(json.dumps(good, ensure_ascii=False,
                                   separators=(',', ':')) + '\n')    # good
            records = read(log_path)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]['title'], 'Good')

    def test_read_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, 'missing.jsonl')
            self.assertEqual(read(log_path), [])


class UnicodeTests(unittest.TestCase):
    """Non-ASCII text is written as-is and reads back equal."""

    def test_non_ascii_not_escaped(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, 'listening.jsonl')
            rec = make_record(**_base_record(title='Beyoncé',
                                             artist='Björk'))
            append(log_path, rec)
            with open(log_path, 'r', encoding='utf-8') as f:
                line = f.readline()
            self.assertIn('Beyoncé', line)
            self.assertNotIn('\\u', line)
            records = read(log_path)
            self.assertEqual(records[0]['title'], 'Beyoncé')
            self.assertEqual(records[0]['artist'], 'Björk')


if __name__ == '__main__':
    unittest.main()
