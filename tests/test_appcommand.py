import unittest

from appcommand import appcommand_from_lparam, action_for_cmd


class TestActionForCmd(unittest.TestCase):
    def test_play_pause(self):
        self.assertEqual(action_for_cmd(14), 'play_pause')

    def test_next(self):
        self.assertEqual(action_for_cmd(11), 'next')

    def test_previous(self):
        self.assertEqual(action_for_cmd(12), 'previous')

    def test_stop(self):
        self.assertEqual(action_for_cmd(13), 'stop')

    def test_unknown_cmd_returns_none(self):
        self.assertIsNone(action_for_cmd(1))
        self.assertIsNone(action_for_cmd(47))


class TestAppcommandFromLparam(unittest.TestCase):
    def test_extracts_cmd_from_positive_lparam(self):
        # cmd=14 in the high word, device/keys bits zero
        lparam = 14 << 16
        self.assertEqual(appcommand_from_lparam(lparam), 14)

    def test_extracts_cmd_with_device_bits_set(self):
        # Real APPCOMMAND lParam: high nibble carries device type (e.g. 1 = FAPPCOMMAND_MOUSE),
        # which can set the sign bit and make Python's lparam negative.
        lparam = (0xF000 | 14) << 16
        self.assertEqual(appcommand_from_lparam(lparam), 14)

    def test_extracts_cmd_from_negative_lparam(self):
        # Simulate a signed LPARAM (high bit set -> negative as a Python int)
        # cmd=11, with the top bit of the 32-bit value set.
        unsigned = (0x8000 | 11) << 16
        signed = unsigned - (1 << 32)
        self.assertLess(signed, 0)
        self.assertEqual(appcommand_from_lparam(signed), 11)


if __name__ == '__main__':
    unittest.main()
