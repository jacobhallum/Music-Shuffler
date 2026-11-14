"""
tests/test_loudness_service.py - LoudnessService: cache, invalidation, background scan, pause, real leveling.

Most tests inject a fake analyzer; test_gain_brings_tones_together uses real FFmpeg (skipped without it).

Run with:
    python -m unittest tests.test_loudness_service -v
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from loudness import TrackLoudness, leveling_gain_db  # noqa: E402
from loudness_service import LoudnessService  # noqa: E402

FFMPEG = shutil.which('ffmpeg')


def _wait(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


class _Counter:
    def __init__(self, fail=()):
        self.calls = []
        self.fail = set(fail)

    def __call__(self, path):
        self.calls.append(path)
        if os.path.basename(path) in self.fail:
            raise RuntimeError('bad file')
        return TrackLoudness(-10.0, -1.0, 100.0, (-10.0,), (-10.0,))


class LoudnessServiceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.cache = os.path.join(self.dir, 'loudness_cache.json')
        self.files = []
        for name in ('a.mp3', 'b.mp3', 'c.mp3'):
            p = os.path.join(self.dir, name)
            with open(p, 'wb') as fh:
                fh.write(b'x' * 10)
            self.files.append(p)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _svc(self, analyze):
        s = LoudnessService('ffmpeg', self.cache, analyze=analyze)
        self.assertTrue(_wait(s.ready))
        return s

    def test_analyze_now_caches(self):
        f = _Counter()
        s = self._svc(f)
        self.assertIsNone(s.get(self.files[0]))
        self.assertEqual(s.analyze_now(self.files[0]).integrated_lufs, -10.0)
        s.analyze_now(self.files[0])
        self.assertEqual(len(f.calls), 1)
        self.assertEqual(s.get(self.files[0]).duration_sec, 100.0)
        s.stop()
        s2 = self._svc(f)  # persisted across instances
        self.assertIsNotNone(s2.get(self.files[0]))
        s2.stop()

    def test_cache_invalidated_by_mtime(self):
        f = _Counter()
        s = self._svc(f)
        s.analyze_now(self.files[0])
        st = os.stat(self.files[0])
        os.utime(self.files[0], (st.st_atime + 100, st.st_mtime + 100))
        self.assertIsNone(s.get(self.files[0]))
        s.analyze_now(self.files[0])
        self.assertEqual(len(f.calls), 2)
        s.stop()

    def test_failed_file_is_not_retried(self):
        f = _Counter(fail={'b.mp3'})
        s = self._svc(f)
        self.assertIsNone(s.analyze_now(self.files[1]))
        self.assertIsNone(s.analyze_now(self.files[1]))
        self.assertEqual(len(f.calls), 1)
        self.assertTrue(s.known_bad(self.files[1]))
        s.stop()

    def test_background_scan_counts_and_skips_cached(self):
        f = _Counter()
        s = self._svc(f)
        s.analyze_now(self.files[0])
        s.start_background(self.files + [os.path.join(self.dir, 'missing.mp3')])
        self.assertTrue(_wait(lambda: s.status() == (4, 4)))
        self.assertEqual(sorted(os.path.basename(p) for p in f.calls), ['a.mp3', 'b.mp3', 'c.mp3'])
        s.stop()

    def test_pause_background_stops_new_runs(self):
        f = _Counter()
        s = self._svc(f)
        s.pause_background()
        s.start_background(self.files)
        time.sleep(0.3)
        self.assertEqual(s.status(), (0, 3))
        s.resume_background()
        self.assertTrue(_wait(lambda: s.status() == (3, 3)))
        s.stop()

    def test_concurrent_analyze_of_one_path_runs_once(self):
        # The next-song prep and a Next press can ask for the same song at once: one FFmpeg, both get it.
        import threading
        gate, entered, calls = threading.Event(), threading.Event(), []

        def slow(path):
            calls.append(path)
            entered.set()
            gate.wait(5.0)
            return TrackLoudness(-10.0, -1.0, 100.0, (-10.0,), (-10.0,))

        s = self._svc(slow)
        results = []
        t1 = threading.Thread(target=lambda: results.append(s.analyze_now(self.files[0])))
        t1.start()
        self.assertTrue(entered.wait(5.0))
        t2 = threading.Thread(target=lambda: results.append(s.analyze_now(self.files[0])))
        t2.start()
        time.sleep(0.1)
        gate.set()
        t1.join(5.0)
        t2.join(5.0)
        self.assertEqual(len(calls), 1)
        self.assertEqual([r.integrated_lufs for r in results], [-10.0, -10.0])
        s.stop()

    def test_analyze_before_cache_loads_returns_none(self):
        import threading
        from unittest import mock
        import loudness_service
        release, real = threading.Event(), loudness_service.load_cache
        with mock.patch.object(loudness_service, 'load_cache', lambda p: (release.wait(5.0), real(p))[1]), \
                mock.patch.object(loudness_service, 'LOAD_WAIT_SEC', 0.05, create=True):
            s = LoudnessService('ffmpeg', self.cache, analyze=_Counter())
            try:
                self.assertIsNone(s.analyze_now(self.files[0]))
            finally:
                release.set()
                s.stop()

    def test_flushes_never_overlap(self):
        # save_cache uses one fixed .tmp name: two writers at once could corrupt or lose the cache
        import threading
        from unittest import mock
        import loudness_service
        active, seen = [0], []

        def slow_save(path, records):
            active[0] += 1
            seen.append(active[0])
            time.sleep(0.2)
            active[0] -= 1

        s = self._svc(_Counter())
        with mock.patch.object(loudness_service, 'save_cache', slow_save):
            s.analyze_now(self.files[0])
            t = threading.Thread(target=s.flush)
            t.start()
            self.assertTrue(_wait(lambda: active[0] == 1))
            s.analyze_now(self.files[1])
            s.flush()
            t.join(5.0)
            s.stop()
        self.assertEqual(max(seen), 1)

    @unittest.skipUnless(FFMPEG, 'ffmpeg not on PATH')
    def test_gain_brings_tones_together(self):
        paths = []
        for name, vol in (('quiet.wav', '-20dB'), ('loud.wav', '12dB')):
            p = os.path.join(self.dir, name)
            subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
                            f'sine=frequency=440:duration=5,volume={vol}', '-ac', '2', p], check=True)
            paths.append(p)
        s = LoudnessService(FFMPEG, self.cache)
        self.assertTrue(_wait(s.ready))
        levels = []
        for p in paths:
            t = s.analyze_now(p)
            levels.append(t.integrated_lufs + leveling_gain_db(t.integrated_lufs, t.true_peak_dbtp))
        s.stop()
        self.assertLess(abs(levels[0] - levels[1]), 1.0)


if __name__ == '__main__':
    unittest.main()
