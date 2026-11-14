"""
tests/test_audio_backend.py - FfmpegDecoder and analyze_file against real FFmpeg on generated tones.

Skipped when FFmpeg is not on PATH. No music library files are used.

Run with:
    python -m unittest tests.test_audio_backend -v
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

FFMPEG = shutil.which('ffmpeg')
BPS = 384000


def _gen(args):
    subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-y'] + args, check=True,
                   stdin=subprocess.DEVNULL)


def _drain(dec, timeout=10.0):
    total, deadline = 0, time.monotonic() + timeout
    while time.monotonic() < deadline:
        c = dec.read_chunk()
        if c is None:
            return total, True
        if c == b'':
            time.sleep(0.005)
            continue
        assert len(c) % 8 == 0, 'chunk not a whole number of frames'
        total += len(c)
    return total, False


@unittest.skipUnless(FFMPEG, 'ffmpeg not on PATH')
class AudioBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.wav = os.path.join(cls.dir, 'tone.wav')
        _gen(['-f', 'lavfi', '-i', 'sine=frequency=440:duration=3', '-ac', '2', '-ar', '44100', cls.wav])
        cls.long_wav = os.path.join(cls.dir, 'long.wav')
        _gen(['-f', 'lavfi', '-i', 'sine=frequency=440:duration=60', '-ac', '2', '-ar', '44100', cls.long_wav])
        cls.m4a = os.path.join(cls.dir, 'tone.m4a')
        _gen(['-i', cls.wav, '-c:a', 'aac', '-b:a', '128k', cls.m4a])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_decoder_reads_whole_file(self):
        from audio_backend import FfmpegDecoder
        d = FfmpegDecoder(FFMPEG, self.wav, 0.0, '', chunk_sec=1.0)
        total, ended = _drain(d)
        d.close()
        self.assertTrue(ended)
        self.assertLessEqual(abs(total - 3 * BPS), 8 * 48)  # within 1 ms
        self.assertIsNone(d.failed())

    def test_decoder_start_offset(self):
        from audio_backend import FfmpegDecoder
        d = FfmpegDecoder(FFMPEG, self.wav, 1.0, 'volume=-3.00dB', chunk_sec=0.5)
        total, ended = _drain(d)
        d.close()
        self.assertTrue(ended)
        self.assertLessEqual(abs(total - 2 * BPS), 8 * 480)  # within 10 ms

    def test_decoder_reads_non_ascii_path(self):
        from audio_backend import FfmpegDecoder
        odd_dir = os.path.join(self.dir, "Beyoncé's #1 dir")
        os.makedirs(odd_dir, exist_ok=True)
        odd = os.path.join(odd_dir, 'tone ü.wav')
        shutil.copy(self.wav, odd)
        d = FfmpegDecoder(FFMPEG, odd, 0.0, '')
        total, ended = _drain(d)
        d.close()
        self.assertTrue(ended)
        self.assertLessEqual(abs(total - 3 * BPS), 8 * 48)

    def test_truncated_file_reports_failure(self):
        from audio_backend import FfmpegDecoder
        bad = os.path.join(self.dir, 'cut.m4a')
        with open(self.m4a, 'rb') as src, open(bad, 'wb') as dst:
            data = src.read()
            dst.write(data[:len(data) * 3 // 10])
        start = time.monotonic()
        d = FfmpegDecoder(FFMPEG, bad, 0.0, '')
        total, ended = _drain(d, timeout=5.0)
        d.close()
        self.assertTrue(ended)
        self.assertLess(time.monotonic() - start, 5.0)
        self.assertLess(total, 3 * BPS)

    def test_close_kills_process(self):
        from audio_backend import FfmpegDecoder
        d = FfmpegDecoder(FFMPEG, self.long_wav, 0.0, '', chunk_sec=0.5, read_ahead_sec=0.5)
        time.sleep(0.2)
        d.close()
        deadline = time.monotonic() + 1.0
        while d.process_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(d.process_alive())
        self.assertIsNone(d.read_chunk())

    def test_read_ahead_bounds_buffer(self):
        from audio_backend import FfmpegDecoder
        d = FfmpegDecoder(FFMPEG, self.long_wav, 0.0, '', chunk_sec=1.0, read_ahead_sec=3.0)
        time.sleep(0.5)
        self.assertLessEqual(d.buffered_seconds(), 3.0 + 1.0)
        d.close()

    def test_analyze_file(self):
        from audio_backend import analyze_file
        t = analyze_file(FFMPEG, self.wav)
        self.assertAlmostEqual(t.duration_sec, 3.0, delta=0.15)
        self.assertTrue(-40.0 < t.integrated_lufs < 0.0)

    def test_analyze_file_bad_input_raises(self):
        from audio_backend import analyze_file
        with self.assertRaises(RuntimeError):
            analyze_file(FFMPEG, os.path.join(self.dir, 'missing.wav'))


if __name__ == '__main__':
    unittest.main()
