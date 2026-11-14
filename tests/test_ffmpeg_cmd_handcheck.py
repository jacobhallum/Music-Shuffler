"""
tests/test_ffmpeg_cmd_handcheck.py - hand-worked checks for ffmpeg_cmd.py

Run with:
    python -m unittest tests/test_ffmpeg_cmd_handcheck.py -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ffmpeg_cmd import *  # noqa: F401,F403
import ffmpeg_cmd


class FfmpegCmdHandcheck(unittest.TestCase):
    def test_decode_args_unicode_path(self):
        p = "/m/My Music/Beyoncé's #1 - Halo.m4a"
        self.assertEqual(decode_args('ffmpeg', p), ['ffmpeg', '-hide_banner', '-nostdin', '-v', 'error', '-i', p, '-map', '0:a:0', '-vn',
                                                    '-f', 'f32le', '-ac', '2', '-ar', '48000', 'pipe:1'])
        a = decode_args('ffmpeg', p, start_sec=12.3456, filters='volume=-3.00dB')
        self.assertEqual(a[5:9], ['-ss', '12.346', '-i', p])
        self.assertEqual(a[a.index('-af') + 1], 'volume=-3.00dB')
        self.assertNotIn('-ss', decode_args('ffmpeg', p, start_sec=-1))

    def test_analyze_and_bytes(self):
        self.assertEqual(analyze_args('ff', 'x')[-6:], ['-vn', '-af', 'ebur128=framelog=verbose:peak=true', '-f', 'null', '-'])
        self.assertEqual((seconds_to_bytes(1.0), seconds_to_bytes(0.5)), (384000, 192000))
        self.assertEqual((bytes_to_seconds(384000), bytes_to_seconds(384007)), (1.0, 1.0))
        self.assertEqual(BYTES_PER_SEC, 384000)
        self.assertNotIn('subprocess', vars(ffmpeg_cmd))


if __name__ == '__main__':
    unittest.main()
