"""appcommand_hook.py - receive WM_APPCOMMAND (fn-lock's F5-F8 and the media dial) on a Tk window. Windows only.

fn-lock (a separate AutoHotkey app, fn-lock.ahk SendAppCommand) sends WM_APPCOMMAND with SendMessage to
`WinGet, hwnd, ID, Music Player V2`. With AutoHotkey's defaults that is the first *visible* top-level window whose
title *starts with* "Music Player V2" (hidden windows are skipped). So the hook has to sit on whichever such window is
visible: the root in the default view, the skinned main window ("Music Player V2 (skin)") while a skin is on, because
the root is withdrawn then.

on_command runs inside the window procedure, and must not touch Tk. fn-lock's SendMessage is cross-process, so Windows
calls the procedure reentrantly, nested inside Tcl's own blocked message wait (Tcl releases the GIL while it waits).
Running Python there is fine (ctypes' callback trampoline takes the GIL), but calling back into Tkinter/Tcl (even
root.after) corrupts Tcl's saved thread state and kills the process with a fatal "GIL is released" error once control
returns to Tcl's notifier. So on_command only records the action; a root.after poller dispatches it later.
"""

import ctypes
import sys

import appcommand

GWLP_WNDPROC = -4
GA_ROOT = 2
WM_NCDESTROY = 0x0082

_user32 = None
_WNDPROC = None
_set_long = _get_long = None
_kept = []          # hooks that could not be taken off (another subclass sits on top): their callback must outlive them


def _api():
    global _user32, _WNDPROC, _set_long, _get_long
    if _user32 is None:
        from ctypes import wintypes
        LRESULT = LONG_PTR = ctypes.c_ssize_t
        u = ctypes.WinDLL('user32', use_last_error=True)       # own instance: argtypes don't leak to other modules
        u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        u.GetAncestor.restype = wintypes.HWND
        u.CallWindowProcW.argtypes = [LONG_PTR, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.CallWindowProcW.restype = LRESULT
        set_long = getattr(u, 'SetWindowLongPtrW', None) or u.SetWindowLongW     # 32-bit Windows has only the W one
        set_long.argtypes = [wintypes.HWND, ctypes.c_int, LONG_PTR]
        set_long.restype = LONG_PTR
        get_long = getattr(u, 'GetWindowLongPtrW', None) or u.GetWindowLongW
        get_long.argtypes = [wintypes.HWND, ctypes.c_int]
        get_long.restype = LONG_PTR
        _WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        _set_long, _get_long, _user32 = set_long, get_long, u
    return _user32


class AppCommandHook:
    """One subclassed top-level window. Keep the object alive while it is installed: the window calls into it."""

    def __init__(self, hwnd, on_command):
        self.hwnd = hwnd
        self._on_command = on_command
        self._old_proc = 0
        self._proc = _WNDPROC(self._wndproc)       # strong reference: a collected callback crashes the next message
        self._proc_ptr = ctypes.cast(self._proc, ctypes.c_void_p).value

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == appcommand.WM_APPCOMMAND:
                try:
                    if self._on_command(appcommand.appcommand_from_lparam(lparam)):
                        return 1
                except Exception as e:
                    print(f"⚠ Error handling a media key: {e}", flush=True)
            result = _user32.CallWindowProcW(self._old_proc, hwnd, msg, wparam, lparam)
            if msg == WM_NCDESTROY:
                self.hwnd = None                   # the window is gone; remove() has nothing left to restore
            return result
        except Exception as e:
            print(f"⚠ Error handling window message (msg={msg}): {e}", flush=True)
            return 0

    def _install(self):
        ctypes.set_last_error(0)
        old = _set_long(self.hwnd, GWLP_WNDPROC, self._proc_ptr)
        if old == 0 and ctypes.get_last_error() != 0:
            return False
        self._old_proc = old
        return True

    def remove(self):
        """Put the window's own procedure back. Harmless twice, or after the window is destroyed."""
        hwnd, self.hwnd = self.hwnd, None
        if not hwnd:
            return
        try:
            if _get_long(hwnd, GWLP_WNDPROC) == self._proc_ptr:
                _set_long(hwnd, GWLP_WNDPROC, self._old_proc)
            else:
                _kept.append(self)                 # someone subclassed after us: unhooking would cut them off
        except Exception as e:
            print(f"⚠ Could not remove the media key hook: {e}", flush=True)


def install(tk_window, on_command):
    """Hook tk_window's top-level frame. on_command(cmd) gets the APPCOMMAND number and returns True when it handled it
    (anything else goes on to Tk). Returns the hook, or None off Windows or when it fails (logged)."""
    if sys.platform != 'win32':
        return None
    try:
        u = _api()
        tk_window.update_idletasks()
        raw = tk_window.winfo_id()
        hwnd = u.GetAncestor(raw, GA_ROOT) or raw    # winfo_id is Tk's child window; WM_APPCOMMAND goes to the frame
        hook = AppCommandHook(hwnd, on_command)
        if not hook._install():
            print("⚠ Media key hook disabled: SetWindowLong failed", flush=True)
            return None
        return hook
    except Exception as e:
        print(f"⚠ Media key hook disabled: {e}", flush=True)
        return None
