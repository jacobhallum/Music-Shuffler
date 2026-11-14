"""
tests/test_eq.py - stdlib-only unit tests for eq.py

Run with:
    python -m unittest tests/test_eq.py -v
"""

import os
import sys
import unittest

# Allow running from project root or tests/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import eq
from eq import (
    BAND_FREQS_HZ, EQF_HEADER, EQF_NAME_LEN, EQF_FLAT_VALUE,
    BUILTIN_PRESETS, eqf_value_to_db, db_to_eqf_value,
    parse_eqf, build_eqf, filter_chain,
)


def _record(name, values):
    """Build one .eqf record (name + 11 value bytes) from Winamp values."""
    out = name.encode('latin-1') + b'\x00' * (EQF_NAME_LEN - len(name.encode('latin-1')))
    out += bytes(64 - v for v in values)
    return out


class ValueMappingTests(unittest.TestCase):
    """eqf_value_to_db / db_to_eqf_value conversion and clamping."""

    def test_flat_is_zero(self):
        self.assertEqual(eqf_value_to_db(33), 0.0)

    def test_one_step_up(self):
        self.assertEqual(eqf_value_to_db(34), round(12 / 31, 1))

    def test_one_step_down(self):
        self.assertEqual(eqf_value_to_db(32), round(-12 / 31, 1))

    def test_top_is_plus_twelve(self):
        self.assertEqual(eqf_value_to_db(64), 12.0)

    def test_bottom_is_minus_twelve(self):
        self.assertEqual(eqf_value_to_db(1), -12.0)

    def test_clamped_below_range(self):
        self.assertEqual(eqf_value_to_db(0), -12.0)
        self.assertEqual(eqf_value_to_db(-50), -12.0)

    def test_clamped_above_range(self):
        self.assertEqual(eqf_value_to_db(65), 12.0)
        self.assertEqual(eqf_value_to_db(1000), 12.0)

    def test_db_to_value_flat(self):
        self.assertEqual(db_to_eqf_value(0.0), 33)

    def test_db_to_value_top(self):
        self.assertEqual(db_to_eqf_value(12.0), 64)

    def test_db_to_value_bottom(self):
        self.assertEqual(db_to_eqf_value(-12.0), 2)  # 33 - 31; value 1 is -12.39 dB, clamped

    def test_db_to_value_clamped(self):
        self.assertEqual(db_to_eqf_value(99.0), 64)
        self.assertEqual(db_to_eqf_value(-99.0), 1)

    def test_roundtrip(self):
        for value in range(2, 65):  # value 1 clamps to -12 dB, which maps back to 2
            self.assertEqual(db_to_eqf_value(eqf_value_to_db(value)), value)


class BuiltinPresetsTests(unittest.TestCase):

    def test_count_and_order(self):
        names = list(BUILTIN_PRESETS)
        self.assertEqual(len(names), 18)
        self.assertEqual(names[0], 'Flat')
        self.assertEqual(names[1], 'Classical')
        self.assertEqual(names[-1], 'Techno')

    def test_flat(self):
        bands, preamp = BUILTIN_PRESETS['Flat']
        self.assertEqual(bands, [0.0] * 10)
        self.assertEqual(preamp, 0.0)

    def test_rock_exact(self):
        bands, preamp = BUILTIN_PRESETS['Rock']
        expected = [eqf_value_to_db(v) for v in (45, 40, 23, 19, 26, 39, 47, 50, 50, 50)]
        self.assertEqual(bands, expected)
        self.assertEqual(preamp, 0.0)

    def test_full_treble_exact(self):
        bands, preamp = BUILTIN_PRESETS['Full Treble']
        expected = [eqf_value_to_db(v) for v in (16, 16, 16, 25, 37, 50, 58, 58, 58, 60)]
        self.assertEqual(bands, expected)
        self.assertEqual(preamp, 0.0)

    def test_all_have_ten_bands(self):
        for name, (bands, preamp) in BUILTIN_PRESETS.items():
            self.assertEqual(len(bands), 10, name)
            self.assertIsInstance(preamp, float, name)


class EqfRoundTripTests(unittest.TestCase):

    def test_build_parse_roundtrip(self):
        # values on the .eqf grid (each is eqf_value_to_db of a Winamp value) so they survive quantization
        presets = [('My EQ', [eqf_value_to_db(v) for v in (37, 28, 33, 41, 30, 33, 48, 18, 64, 2)], eqf_value_to_db(40))]
        data = build_eqf(presets)
        self.assertTrue(data.startswith(EQF_HEADER))
        self.assertEqual(len(data), len(EQF_HEADER) + EQF_NAME_LEN + 11)
        parsed = parse_eqf(data)
        self.assertEqual(parsed, presets)

    def test_two_record_file(self):
        presets = [
            ('First', [0.0] * 10, 0.0),
            ('Second', [eqf_value_to_db(v) for v in (33, 44, 22, 33, 33, 55, 33, 33, 33, 61)], eqf_value_to_db(26)),
        ]
        data = build_eqf(presets)
        parsed = parse_eqf(data)
        self.assertEqual(parsed, presets)
        self.assertEqual(len(data), len(EQF_HEADER) + 2 * (EQF_NAME_LEN + 11))

    def test_byte_encoding(self):
        data = build_eqf([('X', [12.0, 0.0, -12.0] + [0.0] * 7, 0.0)])
        values = data[len(EQF_HEADER) + EQF_NAME_LEN:]
        self.assertEqual(values[0], 0)    # +12 dB -> value 64 -> byte 0
        self.assertEqual(values[1], 31)   # 0 dB -> value 33 -> byte 31
        self.assertEqual(values[2], 62)   # -12 dB -> value 2 -> byte 62

    def test_name_truncated_at_first_nul(self):
        data = EQF_HEADER + _record('Short name', [33] * 11)
        parsed = parse_eqf(data)
        self.assertEqual(parsed[0][0], 'Short name')

    def test_bad_header(self):
        with self.assertRaises(ValueError):
            parse_eqf(b'not the header at all' + b'\x00' * 300)

    def test_empty_data(self):
        with self.assertRaises(ValueError):
            parse_eqf(b'')

    def test_header_only(self):
        with self.assertRaises(ValueError):
            parse_eqf(EQF_HEADER)

    def test_truncated_record(self):
        data = EQF_HEADER + _record('A', [33] * 11)
        with self.assertRaises(ValueError):
            parse_eqf(data[:-1])
        with self.assertRaises(ValueError):
            parse_eqf(data[:len(EQF_HEADER) + EQF_NAME_LEN + 5])

    def test_non_latin1_name_replaced(self):
        data = build_eqf([('caf\u00e9', [0.0] * 10, 0.0)])
        parsed = parse_eqf(data)
        self.assertEqual(parsed[0][0], 'café')  # e-acute is latin-1, kept
        self.assertEqual(parse_eqf(build_eqf([('cafē', [0.0] * 10, 0.0)]))[0][0], 'caf?')


class FilterChainTests(unittest.TestCase):

    def test_no_gain_no_preamp_empty_bands(self):
        self.assertEqual(filter_chain([0.0] * 10, 0.0), '')

    def test_preamp_volume_only(self):
        result = filter_chain([0.0] * 10, 3.0)
        self.assertEqual(result, 'volume=3.00dB')

    def test_gain_only(self):
        result = filter_chain([0.0] * 10, 0.0, gain_db=-2.0)
        self.assertEqual(result, 'volume=-2.00dB')

    def test_preamp_plus_gain(self):
        result = filter_chain([0.0] * 10, 3.0, gain_db=1.0)
        self.assertEqual(result, 'volume=4.00dB')

    def test_disabled_keeps_only_gain(self):
        result = filter_chain([6.0] * 10, 3.0, gain_db=2.0, enabled=False)
        self.assertEqual(result, 'volume=2.00dB')

    def test_low_shelf(self):
        self.assertEqual(filter_chain([-1.5] + [0.0] * 9, 0.0), 'lowshelf=f=60:g=-1.5')

    def test_high_shelf(self):
        self.assertEqual(filter_chain([0.0] * 9 + [4.0], 0.0), 'highshelf=f=16000:g=4.0')

    def test_middle_band(self):
        expected = 'equalizer=f=1000:t=q:w=1:g=2.5'
        self.assertEqual(filter_chain([0.0] * 4 + [2.5] + [0.0] * 5, 0.0), expected)

    def test_full_chain_joined(self):
        result = filter_chain([1.0] * 10, 5.0)
        parts = result.split(',')
        self.assertEqual(parts[0], 'volume=5.00dB')
        self.assertEqual(parts[1], 'lowshelf=f=60:g=1.0')
        self.assertEqual(parts[-1], 'highshelf=f=16000:g=1.0')
        self.assertIn('equalizer=f=600:t=q:w=1:g=1.0', parts)
        self.assertEqual(len(parts), 11)

    def test_tiny_values_ignored(self):
        self.assertEqual(filter_chain([0.04] * 10, 0.04), '')

    def test_builtin_preset_chain(self):
        bands, preamp = BUILTIN_PRESETS['Rock']
        result = filter_chain(bands, preamp)
        self.assertNotIn('volume=', result)
        self.assertEqual(result.split(',')[0], 'lowshelf=f=60:g=4.6')


if __name__ == '__main__':
    unittest.main()
