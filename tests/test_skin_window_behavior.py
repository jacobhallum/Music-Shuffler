"""
tests/test_skin_window_behavior.py - how the skinned windows behave as Windows windows: taskbar button, Alt+F4,
minimize, raising together, staying reachable on screen, dialogs on top. Windows only (reads Win32 window state).

Run with:
    python -m unittest tests.test_skin_window_behavior -v
"""

import ctypes
import os
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

WIN = sys.platform == 'win32'
try:
    import tkinter as tk
    from tests.test_player_skins_glue import _player, _root
except Exception:
    _root = None

if _root is not None:
    import skin_mode  # noqa: E402
    from skin_math import reachable  # noqa: E402
    from tests.test_skin_ui import _skin  # noqa: E402

WS_EX_APPWINDOW, WS_EX_TOOLWINDOW, WS_EX_TOPMOST = 0x40000, 0x80, 0x8
WM_CLOSE = 0x0010


def pump(ms):
    done = []
    _root.after(ms, lambda: done.append(1))
    while not done:
        _root.update()


def hwnd(w):
    return ctypes.windll.user32.GetParent(w.winfo_id())


def exstyle(w):
    return ctypes.windll.user32.GetWindowLongW(hwnd(w), -20)


def above(a, b):
    """True when window a is higher than window b in the desktop z-order."""
    u = ctypes.windll.user32
    h = u.GetTopWindow(None)
    while h:
        if h == hwnd(a):
            return True
        if h == hwnd(b):
            return False
        h = u.GetWindow(h, 2)        # GW_HWNDNEXT
    return None


def rect(w):
    return (w.winfo_x(), w.winfo_y(), w.winfo_width(), w.winfo_height())


@unittest.skipIf(_root is None or not WIN, "needs Windows and a display")
class SkinWindowBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(os.path.join(self.tmp.name, 'skins'))
        _skin(self.tmp.name)
        os.replace(os.path.join(self.tmp.name, 'syn.wsz'), os.path.join(self.tmp.name, 'skins', 'syn.wsz'))
        self.p = _player(self.tmp.name)
        self.addCleanup(self.p.root.destroy)
        self.c = skin_mode.SkinController(self.p)
        self.addCleanup(self.c._close_windows)
        # like the real app after the first run: every window has a saved spot
        self.c.settings['positions'] = {'main': [300, 200], 'eq': [300, 316], 'pl': [300, 432]}

    def open_skin(self):
        self.c.pick('syn.wsz')
        pump(300)

    # ---- taskbar button ----------------------------------------------------------------

    def test_main_window_gets_a_taskbar_button_when_positions_are_saved(self):
        self.open_skin()
        self.assertTrue(exstyle(self.c.main) & WS_EX_APPWINDOW)
        self.assertFalse(exstyle(self.c.main) & WS_EX_TOOLWINDOW)

    # ---- Alt+F4 / taskbar "Close window" ------------------------------------------------

    def test_alt_f4_on_eq_closes_it_like_its_close_button(self):
        self.open_skin()
        ctypes.windll.user32.PostMessageW(hwnd(self.c.eq), WM_CLOSE, 0, 0)
        pump(200)
        self.assertIsNone(self.c.eq)
        self.assertFalse(self.c.settings['eq_open'])
        self.assertTrue(self.c.active)

    def test_alt_f4_on_playlist_closes_it_like_its_close_button(self):
        self.open_skin()
        ctypes.windll.user32.PostMessageW(hwnd(self.c.pl), WM_CLOSE, 0, 0)
        pump(200)
        self.assertIsNone(self.c.pl)
        self.assertFalse(self.c.settings['pl_open'])

    def test_alt_f4_on_main_exits_like_its_close_button(self):
        self.open_skin()
        ctypes.windll.user32.PostMessageW(hwnd(self.c.main), WM_CLOSE, 0, 0)
        pump(200)
        self.assertIn(('_on_close',), self.p.calls)

    def test_main_window_destroyed_from_outside_brings_the_default_view_back(self):
        self.open_skin()
        self.c.main.destroy()
        pump(skin_mode.REFRESH_MS + 200)
        self.assertFalse(self.c.active)
        self.assertEqual(self.p.root.wm_state(), 'normal')
        self.assertIsNone(self.c.eq)
        self.assertEqual(self.c.settings['skin'], 'syn.wsz')     # still the saved pick for the next start

    def test_second_launch_after_main_window_vanished_shows_the_default_view(self):
        self.open_skin()
        self.c.main.destroy()
        self.p.root.deiconify()                                  # what a second launch does
        pump(150)
        self.assertEqual(self.p.root.wm_state(), 'normal')
        self.assertFalse(self.c.active)

    # ---- minimize and raise together -------------------------------------------------------

    def test_eq_and_playlist_hide_with_main_minimize_and_come_back(self):
        self.c.settings['always_on_top'] = True
        self.open_skin()
        skin_mode.minimize_window(self.c.main)
        pump(300)
        self.assertEqual(self.c.eq.wm_state(), 'withdrawn')
        self.assertEqual(self.c.pl.wm_state(), 'withdrawn')
        self.c.main.deiconify()
        pump(300)
        for w in (self.c.eq, self.c.pl):
            self.assertEqual(w.wm_state(), 'normal')
            self.assertTrue(ctypes.windll.user32.IsWindowVisible(hwnd(w)))
            self.assertTrue(exstyle(w) & WS_EX_TOPMOST)

    def test_activating_the_main_window_brings_eq_and_playlist_forward(self):
        self.open_skin()
        cover = tk.Toplevel(_root)
        self.addCleanup(cover.destroy)
        cover.geometry('500x500+250+250')
        pump(100)
        cover.lift()
        pump(100)
        self.assertTrue(above(cover, self.c.eq))
        self.c.main.event_generate('<FocusIn>')
        pump(100)
        self.assertTrue(above(self.c.eq, cover))
        self.assertTrue(above(self.c.pl, cover))

    # ---- staying reachable ------------------------------------------------------------------

    def test_drag_off_the_left_edge_pulls_the_title_bar_back(self):
        self.open_skin()
        with unittest.mock.patch.object(skin_mode, 'work_areas', lambda win: [(0, 0, 1920, 1040)]):
            self.c.main.geometry('+-250+300')
            pump(100)
            self.c.main.callbacks['moved'](0, 0)
            pump(300)
            self.assertEqual((self.c.main.winfo_x(), self.c.main.winfo_y()), (-175, 300))
            self.assertEqual(self.c.settings['positions']['main'], [-175, 300])

    def test_drag_under_the_taskbar_pulls_the_whole_stack_back(self):
        self.open_skin()
        with unittest.mock.patch.object(skin_mode, 'work_areas', lambda win: [(0, 0, 1920, 1040)]):
            self.c.main.geometry('+100+1030')
            pump(100)
            self.c.main.callbacks['moved'](0, 0)
            pump(400)
            for w in self.c._windows():
                x, y, ww, hh = rect(w)
                self.assertEqual(reachable((x, y, ww, hh), [(0, 0, 1920, 1040)]), (x, y))

    def test_double_size_near_the_bottom_keeps_every_title_bar_on_screen(self):
        self.c.settings['positions'] = {'main': [600, 300], 'eq': [600, 416], 'pl': [600, 532]}
        self.open_skin()
        with unittest.mock.patch.object(skin_mode, 'work_areas', lambda win: [(0, 0, 1000, 700)]):
            self.c.toggle_double()
            pump(400)
            for w in self.c._windows():
                x, y, ww, hh = rect(w)
                self.assertEqual(reachable((x, y, ww, hh), [(0, 0, 1000, 700)], grip=28), (x, y))

    def test_saved_spot_off_every_monitor_opens_on_screen(self):
        self.c.settings['positions'] = {'main': [5000, 3000]}
        with unittest.mock.patch.object(skin_mode, 'work_areas', lambda win: [(0, 0, 1920, 1040)]):
            self.open_skin()
            for w in self.c._windows():
                x, y, ww, hh = rect(w)
                self.assertEqual(reachable((x, y, ww, hh), [(0, 0, 1920, 1040)]), (x, y))

    def test_work_areas_lists_at_least_one_monitor(self):
        areas = skin_mode.work_areas(_root)
        self.assertTrue(areas)
        self.assertTrue(all(w > 0 and h > 0 for _x, _y, w, h in areas))

    # ---- dialogs on top of the skin --------------------------------------------------------

    def test_dialog_parent_is_the_skin_while_skinned_else_the_default_view(self):
        self.assertIs(self.c.dialog_parent(), self.p.root)
        self.open_skin()
        self.assertIs(self.c.dialog_parent(), self.c.main)
        self.c.pick(None)
        pump(100)
        self.assertIs(self.c.dialog_parent(), self.p.root)

    def test_owned_dialog_shows_over_an_always_on_top_skin_and_survives_a_skin_change(self):
        self.c.settings['always_on_top'] = True
        self.open_skin()
        d = tk.Toplevel(self.p.root)
        self.addCleanup(lambda: d.winfo_exists() and d.destroy())
        d.geometry('300x100+320+220')
        self.c.own_dialog(d)
        pump(200)
        self.assertTrue(ctypes.windll.user32.IsWindowVisible(hwnd(d)))
        self.assertTrue(above(d, self.c.main))
        self.c.pick(None)
        pump(200)
        self.assertTrue(d.winfo_exists())

    def test_own_dialog_does_nothing_in_the_default_view(self):
        d = tk.Toplevel(self.p.root)
        self.addCleanup(d.destroy)
        self.c.own_dialog(d)
        self.assertEqual(d.transient(), '')


if __name__ == '__main__':
    unittest.main()
