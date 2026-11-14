"""
tests/test_loudness.py - stdlib-only unit tests for loudness.py

Run with:
    python -m unittest tests/test_loudness.py -v
"""

import json
import os
import sys
import tempfile
import unittest

# Allow running from project root or tests/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import loudness
from loudness import (TrackLoudness, parse_ebur128, leveling_gain_db,
                      album_gain_db, cache_key, to_record, from_record,
                      load_cache, save_cache)


def _frame(t, m):
    return (f"[Parsed_ebur128_0 @ 00000225668b1600] t: {t}  TARGET:-23 LUFS"
            f"    M:   {m} S:-120.7     I: -70.0 LUFS       LRA:   0.0 LU"
            f"  FTPK:  -4.0  -4.5 dBFS  TPK:  -4.0  -4.5 dBFS")


_SUMMARY = """[Parsed_ebur128_0 @ 000001de3a0a15c0] Summary:

  Integrated loudness:
    I:         -13.6 LUFS
    Threshold: -23.6 LUFS

  Loudness range:
    LRA:         5.3 LU
    Threshold: -33.6 LUFS
    LRA low:   -18.0 LUFS
    LRA high:  -12.7 LUFS

  True peak:
    Peak:       -1.9 dBFS
"""


class FrameParsingTests(unittest.TestCase):
    """Frame-line parsing: M clamping, duration, head/tail edge windows."""

    def test_m_values_clamped_and_rounded(self):
        lines = [
            _frame('0.0999773', '-120.7'),   # below floor -> -70.0
            _frame('0.1999773', 'nan'),      # nan -> -70.0
            _frame('0.2999773', '-inf'),     # -inf -> -70.0
            _frame('0.3999773', 'abc'),      # unparseable -> -70.0
            _frame('0.4999773', '-18.84'),   # rounded to -18.8
        ]
        text = '\n'.join(lines) + '\n' + _SUMMARY
        tl = parse_ebur128(text)
        self.assertEqual(tl.head, (-70.0, -70.0, -70.0, -70.0, -18.8))
        self.assertEqual(tl.tail, tl.head)
        self.assertEqual(tl.duration_sec, 0.4999773)

    def test_head_and_tail_lengths_on_long_track(self):
        n = 700
        lines = [_frame(f'{i * 0.1:.7f}', str(-20.0 - (i % 40) * 0.1))
                 for i in range(n)]
        text = '\n'.join(lines) + '\n' + _SUMMARY
        tl = parse_ebur128(text)
        self.assertEqual(len(tl.head), loudness.EDGE_FRAMES)
        self.assertEqual(len(tl.tail), loudness.EDGE_FRAMES)
        first_all = tuple(_m_value_shim(l) for l in lines)
        self.assertEqual(tl.head, first_all[:300])
        self.assertEqual(tl.tail, first_all[-300:])
        self.assertAlmostEqual(tl.duration_sec, (n - 1) * 0.1, places=5)

    def test_fewer_than_300_frames_head_and_tail_are_all_frames(self):
        lines = [_frame(f'{i * 0.1:.7f}', '-15.0') for i in range(5)]
        text = '\n'.join(lines) + '\n' + _SUMMARY
        tl = parse_ebur128(text)
        self.assertEqual(tl.head, (-15.0,) * 5)
        self.assertEqual(tl.tail, (-15.0,) * 5)
        self.assertEqual(tl.duration_sec, 0.4)

    def test_no_frame_lines_duration_zero(self):
        tl = parse_ebur128(_SUMMARY)
        self.assertEqual(tl.duration_sec, 0.0)
        self.assertEqual(tl.head, ())
        self.assertEqual(tl.tail, ())


def _m_value_shim(line):
    """Recompute the expected M value the way loudness.py does."""
    import re
    match = re.search(r'\bt:\s*([0-9.]+)\s.*?\bM:\s*(\S+)', line)
    return loudness._m_value(match.group(2))


class SummaryParsingTests(unittest.TestCase):
    """Summary block: I / Peak extraction, fallbacks, error cases."""

    def test_summary_values(self):
        tl = parse_ebur128(_frame('1.0', '-15.0') + '\n' + _SUMMARY)
        self.assertEqual(tl.integrated_lufs, -13.6)
        self.assertEqual(tl.true_peak_dbtp, -1.9)

    def test_inf_integrated_becomes_silence(self):
        text = _frame('1.0', '-15.0') + '\n' + \
            _SUMMARY.replace('I:         -13.6 LUFS', 'I:         -inf LUFS')
        tl = parse_ebur128(text)
        self.assertEqual(tl.integrated_lufs, loudness.SILENCE_LUFS)

    def test_inf_peak_becomes_minus_120(self):
        text = _frame('1.0', '-15.0') + '\n' + \
            _SUMMARY.replace('Peak:       -1.9 dBFS', 'Peak:       -inf dBFS')
        tl = parse_ebur128(text)
        self.assertEqual(tl.true_peak_dbtp, -120.0)

    def test_missing_peak_becomes_minus_120(self):
        text = _frame('1.0', '-15.0') + '\n' + \
            _SUMMARY.replace('    Peak:       -1.9 dBFS\n', '')
        tl = parse_ebur128(text)
        self.assertEqual(tl.true_peak_dbtp, -120.0)

    def test_missing_summary_raises(self):
        with self.assertRaises(ValueError):
            parse_ebur128(_frame('1.0', '-15.0'))

    def test_summary_without_i_raises(self):
        text = _frame('1.0', '-15.0') + '\n' + \
            _SUMMARY.replace('    I:         -13.6 LUFS\n', '')
        with self.assertRaises(ValueError):
            parse_ebur128(text)


class LevelingGainTests(unittest.TestCase):

    def test_peak_limited_boost(self):
        # -14 - (-24) = +10 dB wanted, but ceiling -1 - (-3) = +2 dB allowed.
        self.assertEqual(leveling_gain_db(-24.0, -3.0), 2.0)

    def test_plain_boost_when_peaks_are_low(self):
        self.assertEqual(leveling_gain_db(-20.0, -8.0), 6.0)

    def test_cut(self):
        # -14 - (-10) = -4 dB, peak limit would allow +0 -> cut wins.
        self.assertEqual(leveling_gain_db(-10.0, -1.0), -4.0)

    def test_silent_track_gets_no_gain(self):
        self.assertEqual(leveling_gain_db(-70.0, -120.0), 0.0)
        self.assertEqual(leveling_gain_db(-70.5, -120.0), 0.0)

    def test_custom_target_and_ceiling(self):
        self.assertEqual(leveling_gain_db(-20.0, -10.0,
                                          target=-18.0, ceiling=-2.0), 2.0)


def _song(lufs, peak, duration=100.0):
    return TrackLoudness(integrated_lufs=lufs, true_peak_dbtp=peak,
                         duration_sec=duration, head=(), tail=())


class AlbumGainTests(unittest.TestCase):
    """album_gain_db: duration-weighted energy mean over non-silent songs."""

    def test_equal_songs_match_single_song_gain(self):
        songs = [_song(-20.0, -8.0), _song(-20.0, -8.0)]
        self.assertEqual(album_gain_db(songs),
                         leveling_gain_db(-20.0, -8.0))

    def test_two_song_energy_mean(self):
        # L = 10*log10((100*10^-1 + 100*10^-2)/200) ~= -12.596
        songs = [_song(-10.0, -8.0), _song(-20.0, -8.0)]
        self.assertEqual(album_gain_db(songs), -1.4)

    def test_duration_weighting_changes_result(self):
        even = album_gain_db([_song(-10.0, -8.0), _song(-20.0, -8.0)])
        weighted = album_gain_db([_song(-10.0, -8.0, duration=300.0),
                                  _song(-20.0, -8.0, duration=100.0)])
        self.assertNotEqual(even, weighted)
        self.assertEqual(weighted, -2.9)

    def test_loud_peak_caps_the_gain(self):
        songs = [_song(-20.0, -2.0), _song(-20.0, -2.0)]
        self.assertEqual(album_gain_db(songs),
                         leveling_gain_db(-20.0, -2.0))
        self.assertEqual(album_gain_db(songs), 1.0)

    def test_silent_songs_left_out(self):
        songs = [_song(-70.0, -120.0), _song(-20.0, -8.0)]
        self.assertEqual(album_gain_db(songs), leveling_gain_db(-20.0, -8.0))

    def test_empty_input_returns_zero(self):
        self.assertEqual(album_gain_db([]), 0.0)

    def test_all_silent_returns_zero(self):
        songs = [_song(-70.0, -120.0), _song(-75.0, -120.0)]
        self.assertEqual(album_gain_db(songs), 0.0)

    def test_zero_duration_songs_left_out(self):
        songs = [_song(-10.0, -8.0, duration=0.0), _song(-20.0, -8.0)]
        self.assertEqual(album_gain_db(songs), leveling_gain_db(-20.0, -8.0))


class RecordRoundTripTests(unittest.TestCase):

    def _track(self):
        return TrackLoudness(
            integrated_lufs=-13.6, true_peak_dbtp=-1.9,
            duration_sec=117.3, head=(-20.0, -13.5), tail=(-70.0, -12.3))

    def test_record_shape(self):
        rec = to_record(self._track())
        self.assertEqual(rec['i'], -136)
        self.assertEqual(rec['p'], -19)
        self.assertEqual(rec['d'], 117.3)
        self.assertEqual(rec['h'], [-200, -135])
        self.assertEqual(rec['t'], [-700, -123])
        self.assertIsInstance(rec['h'], list)
        self.assertIsInstance(rec['t'], list)
        self.assertIsInstance(rec['i'], int)
        self.assertIsInstance(rec['p'], int)

    def test_round_trip(self):
        tl = self._track()
        back = from_record(to_record(tl))
        self.assertEqual(back, tl)

    def test_malformed_records_return_none(self):
        good = to_record(self._track())
        self.assertIsNone(from_record('not a dict'))
        self.assertIsNone(from_record(None))
        self.assertIsNone(from_record({k: v for k, v in good.items()
                                       if k != 'h'}))
        for key, bad in (('i', -13.6), ('i', True), ('p', -1.9), ('p', True),
                         ('d', '117.3'), ('d', True),
                         ('h', (-200, -135)), ('h', [-200, -13.5]),
                         ('h', [-200, True]), ('t', 'x'), ('t', [1.5])):
            rec = dict(good)
            rec[key] = bad
            self.assertIsNone(from_record(rec), f'{key}={bad!r}')


class CacheTests(unittest.TestCase):

    def _key(self, name, size, mtime):
        return cache_key(os.path.join(self.tmp, name), size, mtime)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.cache_path = os.path.join(self.tmp, 'loudness_cache.json')

    def test_cache_key_format(self):
        p = os.path.join(self.tmp, 'song.mp3')
        k = cache_key(p, 1234, 987.65)
        self.assertEqual(k, os.path.normcase(os.path.normpath(p))
                         + '|1234|987')

    def test_load_missing_file(self):
        self.assertEqual(load_cache(self.cache_path), {})

    def test_load_corrupt_json(self):
        with open(self.cache_path, 'w', encoding='utf-8') as f:
            f.write('{not json')
        self.assertEqual(load_cache(self.cache_path), {})

    def test_load_non_dict_and_wrong_version(self):
        with open(self.cache_path, 'w', encoding='utf-8') as f:
            f.write('[]')
        self.assertEqual(load_cache(self.cache_path), {})
        with open(self.cache_path, 'w', encoding='utf-8') as f:
            json.dump({'version': 2, 'tracks': {}}, f)
        self.assertEqual(load_cache(self.cache_path), {})
        with open(self.cache_path, 'w', encoding='utf-8') as f:
            json.dump({'version': 1, 'tracks': []}, f)
        self.assertEqual(load_cache(self.cache_path), {})

    def test_save_load_round_trip_and_no_tmp_left(self):
        rec = to_record(TrackLoudness(-13.6, -1.9, 117.3,
                                      (-20.0, -13.5), (-70.0, -12.3)))
        key = self._key('song.mp3', 4321, 123.9)
        save_cache(self.cache_path, {key: rec})
        loaded = load_cache(self.cache_path)
        self.assertEqual(loaded, {key: rec})
        self.assertEqual(from_record(loaded[key]),
                         from_record(rec))
        self.assertEqual(os.listdir(self.tmp), ['loudness_cache.json'])


if __name__ == '__main__':
    unittest.main()
