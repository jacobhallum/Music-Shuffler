"""
tests/test_ffmpeg_cmd.py - stdlib-only unit tests for ffmpeg_cmd.py

Run with:
    python -m unittest tests/test_ffmpeg_cmd.py -v
"""

import os
import sys
import unittest

# Allow running from project root or tests/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import ffmpeg_cmd
from ffmpeg_cmd import (
    analyze_args,
    bytes_to_seconds,
    decode_args,
    seconds_to_bytes,
)


class DecodeArgsTests(unittest.TestCase):
    """decode_args builds the ffmpeg command line for PCM decoding."""

    def test_plain_decode_full_list(self):
        self.assertEqual(
            decode_args('ffmpeg', 'song.mp3'),
            [
                'ffmpeg', '-hide_banner', '-nostdin', '-v', 'error',
                '-i', 'song.mp3', '-map', '0:a:0', '-vn',
                '-f', 'f32le', '-ac', '2', '-ar', '48000', 'pipe:1',
            ],
        )

    def test_start_sec_adds_ss_before_input(self):
        args = decode_args('ffmpeg', 'song.mp3', start_sec=1.5)
        self.assertEqual(args[5:7], ['-ss', '1.500'])
        self.assertEqual(args[7:9], ['-i', 'song.mp3'])

    def test_start_sec_three_decimals(self):
        args = decode_args('ffmpeg', 'song.mp3', start_sec=12.345678)
        self.assertIn('12.346', args)

    def test_start_sec_zero_and_negative_add_nothing(self):
        for start in (0.0, -1.0):
            with self.subTest(start=start):
                self.assertNotIn('-ss', decode_args('ffmpeg', 'p.mp3', start_sec=start))

    def test_filters_after_vn(self):
        args = decode_args('ffmpeg', 'song.mp3', filters='volume=0.5')
        self.assertEqual(args[10:12], ['-af', 'volume=0.5'])

    def test_empty_filters_add_nothing(self):
        self.assertNotIn('-af', decode_args('ffmpeg', 'song.mp3', filters=''))

    def test_start_and_filters_together(self):
        args = decode_args('ffmpeg', 'song.mp3', start_sec=2.0, filters='dynaudnorm')
        self.assertEqual(
            args,
            [
                'ffmpeg', '-hide_banner', '-nostdin', '-v', 'error',
                '-ss', '2.000',
                '-i', 'song.mp3', '-map', '0:a:0', '-vn', '-af', 'dynaudnorm',
                '-f', 'f32le', '-ac', '2', '-ar', '48000', 'pipe:1',
            ],
        )

    def test_path_with_spaces_and_non_ascii_stays_one_element(self):
        path = 'my music/año café #1.flac'
        args = decode_args('ffmpeg', path)
        self.assertIn(path, args)
        self.assertEqual(args[6], path)


class AnalyzeArgsTests(unittest.TestCase):
    """analyze_args builds the ebur128 loudness analysis command line."""

    def test_full_list(self):
        self.assertEqual(
            analyze_args('ffmpeg', 'track.wav'),
            [
                'ffmpeg', '-hide_banner', '-nostdin', '-nostats', '-v', 'verbose',
                '-i', 'track.wav', '-map', '0:a:0', '-vn',
                '-af', 'ebur128=framelog=verbose:peak=true',
                '-f', 'null', '-',
            ],
        )

    def test_path_with_spaces_stays_one_element(self):
        path = 'my music/track #1.wav'
        args = analyze_args('ffmpeg', path)
        self.assertIn(path, args)
        self.assertEqual(args[7], path)


class ConversionTests(unittest.TestCase):
    """seconds_to_bytes / bytes_to_seconds round trips for PCM framing."""

    def test_seconds_to_bytes(self):
        self.assertEqual(seconds_to_bytes(1.0), 384000)
        self.assertEqual(seconds_to_bytes(0.5), 192000)
        self.assertEqual(seconds_to_bytes(0), 0)

    def test_bytes_to_seconds(self):
        self.assertEqual(bytes_to_seconds(384000), 1.0)
        self.assertEqual(bytes_to_seconds(384007), 1.0)
        self.assertEqual(bytes_to_seconds(0), 0.0)

    def test_no_forbidden_imports(self):
        with open(ffmpeg_cmd.__file__, encoding='utf-8') as fh:
            src = fh.read()
        for name in ('subprocess', 'os', 'shlex'):
            self.assertNotIn(f'import {name}', src)
            self.assertNotIn(f'import {name},', src)


if __name__ == '__main__':
    unittest.main()
