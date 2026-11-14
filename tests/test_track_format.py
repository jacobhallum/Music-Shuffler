"""
tests/test_track_format.py - tags and duration come from a file's content, not its extension.

Some library files are MP4 audio saved with a .flac name. mutagen weighs the extension above the
header, so they used to load with duration 0 and no tags. Uses FFmpeg to make the files (skipped
without it).

Run with:
    python -m unittest tests.test_track_format -v
"""

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import music_player  # noqa: E402
from music_player import MusicTrack  # noqa: E402

FFMPEG = shutil.which('ffmpeg')


def _make(path, codec_args, fmt):
    subprocess.run([FFMPEG, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3',
                    *codec_args, '-metadata', 'title=Tone', '-metadata', 'artist=Tester',
                    '-metadata', 'track=3', '-f', fmt, path], check=True, timeout=60)


def _box(typ, payload):
    return struct.pack('>I', 8 + len(payload)) + typ + payload


def _mvhd_v0(timescale):
    return _box(b'mvhd', b'\x00\x00\x00\x00' + struct.pack('>IIII', 0, 0, timescale, 0) + b'\x00' * 80)


def _inject_mehd(path, seconds):
    """Add an mvex/mehd box (as the library's DASH files have) to an FFmpeg fragmented MP4."""
    with open(path, 'rb') as fh:
        data = fh.read()
    i = data.index(b'moov') - 4
    moov_size = struct.unpack('>I', data[i:i + 4])[0]
    j = data.index(b'mvex', i) - 4
    mvex_size = struct.unpack('>I', data[j:j + 4])[0]
    k = data.index(b'mvhd', i) + 4
    ts = struct.unpack('>I', data[k + 12:k + 16])[0] if data[k] == 0 else struct.unpack('>I', data[k + 20:k + 24])[0]
    mehd = _box(b'mehd', b'\x00\x00\x00\x00' + struct.pack('>I', seconds * ts))
    out = bytearray(data[:j + 8] + mehd + data[j + 8:])
    struct.pack_into('>I', out, j, mvex_size + len(mehd))
    struct.pack_into('>I', out, i, moov_size + len(mehd))
    with open(path, 'wb') as fh:
        fh.write(bytes(out))


class FragmentedDurationTests(unittest.TestCase):
    def _write(self, data):
        fd, path = tempfile.mkstemp(suffix='.flac')
        with os.fdopen(fd, 'wb') as fh:
            fh.write(data)
        self.addCleanup(os.remove, path)
        return path

    def test_reads_mehd_v0_and_v1(self):
        f = getattr(music_player, 'fragmented_mp4_duration', None)
        self.assertIsNotNone(f)
        ftyp = _box(b'ftyp', b'iso8\x00\x00\x00\x00mp41dash')
        v0 = _box(b'mvex', _box(b'mehd', b'\x00\x00\x00\x00' + struct.pack('>I', 185480)))
        v1 = _box(b'mvex', _box(b'mehd', b'\x01\x00\x00\x00' + struct.pack('>Q', 185480)))
        self.assertAlmostEqual(f(self._write(ftyp + _box(b'moov', _mvhd_v0(1000) + v0) + _box(b'mdat', b'x'))), 185.48)
        self.assertAlmostEqual(f(self._write(ftyp + _box(b'moov', _mvhd_v0(1000) + v1))), 185.48)

    def test_none_when_there_is_nothing_to_read(self):
        f = getattr(music_player, 'fragmented_mp4_duration', None)
        self.assertIsNotNone(f)
        ftyp = _box(b'ftyp', b'iso8\x00\x00\x00\x00mp41dash')
        self.assertIsNone(f(self._write(ftyp + _box(b'moov', _mvhd_v0(1000)))))   # no mvex/mehd
        self.assertIsNone(f(self._write(b'fLaC' + b'\x00' * 40)))                 # not MP4
        self.assertIsNone(f(self._write(ftyp + b'\x00\x00\x00\x02moov')))          # torn box size
        self.assertIsNone(f(os.path.join(tempfile.gettempdir(), 'no-such-file.flac')))


@unittest.skipUnless(FFMPEG, 'ffmpeg not on PATH')
class TrackFormatTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_mp4_named_flac_gets_duration_and_tags(self):
        path = os.path.join(self.dir, 'tone.flac')
        _make(path, ['-c:a', 'aac', '-b:a', '96k'], 'mp4')
        t = MusicTrack(path)
        self.assertGreaterEqual(t.duration, 2)
        self.assertEqual((t.title, t.artist), ('Tone', 'Tester'))
        self.assertEqual(str(t.track_number).split('/')[0], '3')

    def test_real_flac_still_reads(self):
        path = os.path.join(self.dir, 'tone.flac')
        _make(path, ['-c:a', 'flac'], 'flac')
        t = MusicTrack(path)
        self.assertGreaterEqual(t.duration, 2)
        self.assertEqual(t.title, 'Tone')

    def test_fragmented_mp4_named_flac_gets_its_duration(self):
        # The real files are fragmented (DASH) MP4 holding FLAC: mutagen's length is 0, mvex/mehd has it.
        path = os.path.join(self.dir, 'frag.flac')
        subprocess.run([FFMPEG, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3',
                        '-c:a', 'flac', '-strict', '-2', '-movflags', 'frag_keyframe+empty_moov+default_base_moof',
                        '-f', 'mp4', path], check=True, timeout=60)
        _inject_mehd(path, seconds=3)
        self.assertEqual(MusicTrack(path).duration, 3)

    def test_cached_track_without_a_duration_is_read_again(self):
        # Library cache entries saved while the bug was live have duration 0: they must be re-read.
        needs = getattr(music_player.MusicPlayerGUI, '_cached_track_needs_rescan', None)
        self.assertIsNotNone(needs)
        path = os.path.join(self.dir, 'tone.flac')
        _make(path, ['-c:a', 'aac', '-b:a', '96k'], 'mp4')
        t = MusicTrack(path)
        self.assertFalse(needs(t))
        t.duration = 0
        self.assertTrue(needs(t))
        t.duration = 120
        t.filepath = os.path.join(self.dir, 'gone.flac')
        self.assertTrue(needs(t))


if __name__ == '__main__':
    unittest.main()
