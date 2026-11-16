"""
tests/test_player_media_keys.py - MusicPlayerGUI._queue_appcommand: what a WM_APPCOMMAND (fn-lock's F5-F8 / dial)
queues, with no Tk or audio. The real method is bound onto a small fake.

Run with:
    python -m unittest tests.test_player_media_keys -v
"""

import os
import sys
import unittest
from collections import deque

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from music_player import MusicPlayerGUI  # noqa: E402


class _Player:
    _queue_appcommand = MusicPlayerGUI._queue_appcommand

    def __init__(self):
        self._appcmd_pending = deque()

    def toggle_play_pause(self):
        pass

    def next_track(self):
        pass

    def previous_track(self):
        pass

    def stop(self):
        pass


class QueueAppCommandTests(unittest.TestCase):
    def test_transport_commands_are_queued_in_order(self):
        p = _Player()
        for cmd in (14, 11, 12, 13):
            self.assertTrue(p._queue_appcommand(cmd))
        self.assertEqual([label for _fn, label in p._appcmd_pending], ['Play/Pause', 'Next', 'Previous', 'Stop'])
        self.assertEqual(p._appcmd_pending[0][0], p.toggle_play_pause)

    def test_other_commands_are_left_alone(self):
        p = _Player()
        self.assertFalse(p._queue_appcommand(8))       # APPCOMMAND_VOLUME_MUTE: Windows handles it
        self.assertEqual(len(p._appcmd_pending), 0)


if __name__ == '__main__':
    unittest.main()
