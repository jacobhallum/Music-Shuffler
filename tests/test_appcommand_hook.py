"""
tests/test_appcommand_hook.py - appcommand_hook: WM_APPCOMMAND on a Tk window's frame (Windows only).

Run with (hidden desktop):
    python scripts/run_hidden.py python -m unittest tests.test_appcommand_hook -v
"""

import ctypes
import os
import sys
import unittest
from ctypes import wintypes

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

_root = None
if sys.platform == 'win32':
    try:
        import tkinter as tk
        _root = tk.Tk()
        _root.withdraw()
    except Exception:
        _root = None

if _root is not None:
    import appcommand_hook  # noqa: E402

    _u = ctypes.WinDLL('user32', use_last_error=True)
    _u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    _u.GetAncestor.restype = wintypes.HWND
    _u.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _u.SendMessageW.restype = ctypes.c_ssize_t
    _get_long = getattr(_u, 'GetWindowLongPtrW', None) or _u.GetWindowLongW
    _get_long.argtypes = [wintypes.HWND, ctypes.c_int]
    _get_long.restype = ctypes.c_ssize_t

WM_APPCOMMAND = 0x0319
WM_SETTEXT = 0x000C
PLAY_PAUSE = 14


def _frame(win):
    win.update_idletasks()
    return _u.GetAncestor(win.winfo_id(), 2)


def _title(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    _u.GetWindowTextW(hwnd, buf, 256)
    return buf.value


@unittest.skipIf(_root is None, "Windows with a display only")
class AppCommandHookTests(unittest.TestCase):
    def setUp(self):
        self.win = tk.Toplevel(_root)
        self.win.title('hook test')
        self.addCleanup(self._destroy)
        self.win.update()
        self.hwnd = _frame(self.win)
        self.seen = []
        self.handled = True

    def _destroy(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def on_command(self, cmd):
        self.seen.append(cmd)
        return self.handled

    def send(self, cmd):
        return _u.SendMessageW(self.hwnd, WM_APPCOMMAND, self.hwnd, cmd << 16)

    def test_appcommand_reaches_on_command(self):
        hook = appcommand_hook.install(self.win, self.on_command)
        self.addCleanup(hook.remove)
        self.assertEqual(hook.hwnd, self.hwnd)
        self.assertEqual(self.send(PLAY_PAUSE), 1)
        self.assertEqual(self.seen, [PLAY_PAUSE])

    def test_device_bits_in_lparam_are_ignored(self):
        hook = appcommand_hook.install(self.win, self.on_command)
        self.addCleanup(hook.remove)
        _u.SendMessageW(self.hwnd, WM_APPCOMMAND, self.hwnd, (PLAY_PAUSE | 0x8000) << 16)   # FAPPCOMMAND_OEM
        self.assertEqual(self.seen, [PLAY_PAUSE])

    def test_unhandled_command_goes_on_to_tk(self):
        self.handled = False
        hook = appcommand_hook.install(self.win, self.on_command)
        self.addCleanup(hook.remove)
        self.send(99)
        self.assertEqual(self.seen, [99])

    def test_other_messages_reach_tk(self):
        hook = appcommand_hook.install(self.win, self.on_command)
        self.addCleanup(hook.remove)
        text = ctypes.create_unicode_buffer('renamed')
        _u.SendMessageW(self.hwnd, WM_SETTEXT, 0, ctypes.cast(text, ctypes.c_void_p).value)
        self.assertEqual(_title(self.hwnd), 'renamed')
        self.assertEqual(self.seen, [])

    def test_remove_restores_the_window(self):
        before = _get_long(self.hwnd, -4)
        hook = appcommand_hook.install(self.win, self.on_command)
        self.assertNotEqual(_get_long(self.hwnd, -4), before)
        hook.remove()
        self.assertEqual(_get_long(self.hwnd, -4), before)
        self.send(PLAY_PAUSE)
        self.assertEqual(self.seen, [])
        hook.remove()                    # twice is harmless

    def test_window_destroyed_while_hooked(self):
        hook = appcommand_hook.install(self.win, self.on_command)
        self.win.destroy()
        _root.update()
        hook.remove()                    # the window is gone: nothing to restore, no error

    def test_on_command_error_does_not_escape(self):
        def boom(cmd):
            raise RuntimeError('boom')
        hook = appcommand_hook.install(self.win, boom)
        self.addCleanup(hook.remove)
        self.send(PLAY_PAUSE)            # logged, not raised into Windows
        self.win.update()


if __name__ == '__main__':
    unittest.main()
