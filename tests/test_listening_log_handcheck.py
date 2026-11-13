"""
tests/test_listening_log_handcheck.py - hand-worked checks for listening_log.py

Run with:
    python -m unittest tests/test_listening_log_handcheck.py -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from listening_log import *  # noqa: F401,F403


class ListeningLogHandcheck(unittest.TestCase):
    def rec(self, **kw):
        base = dict(started_at=1000.9, ended_at=1200.2, path='/m/A/B/x.mp3', title='Halo', artist='Beyoncé', album='I Am...',
                    genre=None, duration_sec=200.04, audible_sec=180.06, reason='natural_end', qualified=True, skip=False,
                    transition='smart')
        base.update(kw)
        return make_record(**base)

    def test_record(self):
        r = self.rec()
        self.assertEqual((r['v'], r['start'], r['end'], r['genre'], r['dur'], r['heard'], r['q'], r['skip'], r['tr']),
                         (1, 1000, 1200, '', 200.0, 180.1, True, False, 'smart'))
        self.assertEqual(r['path'], os.path.normcase(os.path.normpath('/m/A/B/x.mp3')))
        with self.assertRaises(ValueError):
            self.rec(reason='skipped')
        with self.assertRaises(ValueError):
            self.rec(transition='automix')

    def test_torn_line_and_non_finite(self):
        r = self.rec(duration_sec=float('nan'), audible_sec=float('inf'))
        self.assertEqual((r['dur'], r['heard']), (0.0, 0.0))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'listening_log.jsonl')
            append(p, self.rec())
            with open(p, 'a', encoding='utf-8') as fh:
                fh.write('{"v":1,"start":5')  # last write torn by a crash
            append(p, self.rec(reason='next', skip=True, transition='cut'))
            self.assertEqual([g['reason'] for g in read(p)], ['natural_end', 'next'])

    def test_append_read(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'listening_log.jsonl')
            self.assertEqual(read(p), [])
            append(p, self.rec())
            append(p, self.rec(reason='next', skip=True, transition='cut'))
            with open(p, 'a', encoding='utf-8') as fh:
                fh.write('{broken\n\n{"v":2}\n[1]\n')
            got = read(p)
            self.assertEqual([g['reason'] for g in got], ['natural_end', 'next'])
            with open(p, encoding='utf-8') as fh:
                self.assertIn('Beyoncé', fh.read())


if __name__ == '__main__':
    unittest.main()
