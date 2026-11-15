"""
scripts/run_hidden.py - run a command on a hidden Windows desktop, so the GUI tests' windows, dialogs and menus never
show on your screen, steal focus or add taskbar buttons. Output is printed when the command ends; the exit code is the
command's.

    python scripts/run_hidden.py                    # the whole test suite
    python scripts/run_hidden.py python -m unittest tests.test_skin_window_behavior -v

Windows only (elsewhere it just runs the command).
"""

import ctypes
import ctypes.wintypes as wt
import msvcrt
import os
import subprocess
import sys
import tempfile

DESKTOP = 'MusicPlayerTests'
GENERIC_ALL = 0x10000000
STARTF_USESTDHANDLES = 0x00000100
CREATE_NO_WINDOW = 0x08000000
INFINITE = 0xFFFFFFFF


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [('cb', wt.DWORD), ('lpReserved', wt.LPWSTR), ('lpDesktop', wt.LPWSTR), ('lpTitle', wt.LPWSTR),
                ('dwX', wt.DWORD), ('dwY', wt.DWORD), ('dwXSize', wt.DWORD), ('dwYSize', wt.DWORD),
                ('dwXCountChars', wt.DWORD), ('dwYCountChars', wt.DWORD), ('dwFillAttribute', wt.DWORD),
                ('dwFlags', wt.DWORD), ('wShowWindow', wt.WORD), ('cbReserved2', wt.WORD),
                ('lpReserved2', ctypes.c_void_p), ('hStdInput', wt.HANDLE), ('hStdOutput', wt.HANDLE),
                ('hStdError', wt.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [('hProcess', wt.HANDLE), ('hThread', wt.HANDLE), ('dwProcessId', wt.DWORD),
                ('dwThreadId', wt.DWORD)]


def run_hidden(argv, cwd):
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    user32.CreateDesktopW.restype = wt.HANDLE
    user32.CreateDesktopW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.c_void_p]
    user32.CloseDesktop.argtypes = [wt.HANDLE]
    kernel32.CreateProcessW.argtypes = [wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wt.BOOL, wt.DWORD,
                                        ctypes.c_void_p, wt.LPCWSTR, ctypes.POINTER(STARTUPINFOW),
                                        ctypes.POINTER(PROCESS_INFORMATION)]
    kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
    kernel32.CloseHandle.argtypes = [wt.HANDLE]

    desk = user32.CreateDesktopW(DESKTOP, None, None, 0, GENERIC_ALL, None)
    if not desk:
        raise OSError(ctypes.get_last_error(), 'CreateDesktopW failed')
    log = tempfile.TemporaryFile(mode='w+b')
    try:
        handle = msvcrt.get_osfhandle(log.fileno())
        os.set_handle_inheritable(handle, True)
        si = STARTUPINFOW()
        si.cb = ctypes.sizeof(si)
        si.lpDesktop = 'WinSta0\\' + DESKTOP
        si.dwFlags = STARTF_USESTDHANDLES
        si.hStdOutput = si.hStdError = handle
        pi = PROCESS_INFORMATION()
        cmd = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        ok = kernel32.CreateProcessW(None, cmd, None, None, True, CREATE_NO_WINDOW, None, cwd,
                                         ctypes.byref(si), ctypes.byref(pi))
        if not ok:
            raise OSError(ctypes.get_last_error(), 'CreateProcessW failed')
        kernel32.WaitForSingleObject(pi.hProcess, INFINITE)
        code = wt.DWORD()
        kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
        kernel32.CloseHandle(pi.hThread)
        kernel32.CloseHandle(pi.hProcess)
        log.seek(0)
        sys.stdout.buffer.write(log.read())
        sys.stdout.flush()
        return code.value
    finally:
        log.close()
        user32.CloseDesktop(desk)


def main():
    argv = sys.argv[1:] or [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests']
    if argv[0] == 'python':
        argv[0] = sys.executable
    cwd = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
    if sys.platform != 'win32':
        return subprocess.call(argv, cwd=cwd)
    env = os.environ.copy()
    env.setdefault('PYTHONIOENCODING', 'utf-8')
    os.environ.update(env)          # inherited by the child (CreateProcessW gets env None = ours)
    return run_hidden(argv, os.path.abspath(cwd))


if __name__ == '__main__':
    sys.exit(main())
