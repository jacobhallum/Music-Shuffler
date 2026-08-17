"""
scripts/send_appcommand.py - Manual WM_APPCOMMAND test harness

Sends a real WM_APPCOMMAND message to the running Music Player V2 window so the
hardware-dial handler (_setup_wm_appcommand_hook in music_player.py) can be
exercised without the physical dial.

Usage:
    python scripts/send_appcommand.py play_pause
    python scripts/send_appcommand.py 11
    python scripts/send_appcommand.py next --title "Music Player V2"
"""

import argparse
import ctypes
from ctypes import wintypes
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from appcommand import (
    WM_APPCOMMAND,
    APPCOMMAND_MEDIA_NEXTTRACK,
    APPCOMMAND_MEDIA_PREVIOUSTRACK,
    APPCOMMAND_MEDIA_STOP,
    APPCOMMAND_MEDIA_PLAY_PAUSE,
)

_NAME_TO_CMD = {
    'play_pause': APPCOMMAND_MEDIA_PLAY_PAUSE,
    'next': APPCOMMAND_MEDIA_NEXTTRACK,
    'previous': APPCOMMAND_MEDIA_PREVIOUSTRACK,
    'stop': APPCOMMAND_MEDIA_STOP,
}


def _resolve_cmd(value):
    if value.lower() in _NAME_TO_CMD:
        return _NAME_TO_CMD[value.lower()]
    try:
        return int(value)
    except ValueError:
        raise SystemExit(f"Unknown cmd '{value}' (use an int or one of {list(_NAME_TO_CMD)})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cmd', help="APPCOMMAND cmd: int (14/11/12/13) or name (play_pause/next/previous/stop)")
    parser.add_argument('--title', default='Music Player V2', help="Target window title")
    args = parser.parse_args()

    cmd = _resolve_cmd(args.cmd)

    LRESULT = ctypes.c_ssize_t
    LONG_PTR = ctypes.c_ssize_t

    user32 = ctypes.WinDLL('user32', use_last_error=True)

    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND

    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL

    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = LRESULT

    hwnd = user32.FindWindowW(None, args.title)
    if not hwnd:
        print(f"✗ Window titled '{args.title}' not found")
        sys.exit(1)

    user32.SetForegroundWindow(hwnd)

    lparam = LONG_PTR(cmd << 16)
    print(f"Sending WM_APPCOMMAND cmd={cmd} to hwnd={hwnd} (title='{args.title}')")
    result = user32.SendMessageW(hwnd, WM_APPCOMMAND, hwnd, lparam)
    print(f"SendMessage returned {result}")


if __name__ == '__main__':
    main()
