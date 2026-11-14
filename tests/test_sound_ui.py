"""
tests/test_sound_ui.py - lifecycle checks for the Sound dialog with real Tk (skipped when Tk has no display).

Run with:
    python -m unittest tests.test_sound_ui -v
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.withdraw()
    _root.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


@unittest.skipUnless(HAVE_TK, 'no Tk display')
class SoundDialogLifecycleTests(unittest.TestCase):
    def test_closing_with_the_window_x_stops_the_status_timer(self):
        import audio_settings
        from sound_ui import SoundDialog
        root = tk.Tk()
        root.withdraw()
        calls = []
        try:
            d = SoundDialog(root, audio_settings.normalize(None), lambda s: None, lambda: calls.append(1) or '')
            root.update()
            root.tk.call('destroy', str(d))   # what the title-bar X does: Tcl destroy, not the Python override
            n = len(calls)
            end = time.monotonic() + 2.5
            while time.monotonic() < end:
                root.update()
                time.sleep(0.02)
            self.assertEqual(len(calls), n)
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
