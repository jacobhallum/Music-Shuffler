"""Pure logic for WM_APPCOMMAND handling (no GUI/ctypes deps, unit-testable)."""

WM_APPCOMMAND = 0x0319

APPCOMMAND_MEDIA_NEXTTRACK = 11
APPCOMMAND_MEDIA_PREVIOUSTRACK = 12
APPCOMMAND_MEDIA_STOP = 13
APPCOMMAND_MEDIA_PLAY_PAUSE = 14

_ACTIONS = {
    APPCOMMAND_MEDIA_NEXTTRACK: 'next',
    APPCOMMAND_MEDIA_PREVIOUSTRACK: 'previous',
    APPCOMMAND_MEDIA_STOP: 'stop',
    APPCOMMAND_MEDIA_PLAY_PAUSE: 'play_pause',
}


def appcommand_from_lparam(lparam):
    """Extract the APPCOMMAND cmd value from a WM_APPCOMMAND lParam.

    cmd occupies the high word, low 12 bits (GET_APPCOMMAND_LPARAM). lParam is
    signed (ctypes LPARAM), so mask after the shift rather than relying on an
    unsigned right shift.
    """
    return (lparam >> 16) & 0xFFF


def action_for_cmd(cmd):
    """Map an APPCOMMAND cmd value to an action name, or None if unhandled."""
    return _ACTIONS.get(cmd)
