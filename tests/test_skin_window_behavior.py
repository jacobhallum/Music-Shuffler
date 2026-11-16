"""
tests/test_skin_window_behavior.py - how the skinned windows behave as Windows windows: taskbar button, Alt+F4,
minimize, raising together, staying reachable on screen, dialogs on top. Windows only (reads Win32 window state).

Run with:
    python -m unittest tests.test_skin_window_behavior -v
"""

import ctypes
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

WIN = sys.platform == 'win32'
try:
    import tkinter as tk
    from tests.test_player_skins_glue import _player, _root
except Exception:
    _root = None

if _root is not None:
    import skin_cover  # noqa: E402
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

    def test_alt_f4_on_the_playlist_leaves_it_open(self):
        self.open_skin()
        pl = self.c.pl
        ctypes.windll.user32.PostMessageW(hwnd(pl), WM_CLOSE, 0, 0)
        pump(200)
        self.assertIs(self.c.pl, pl)
        self.assertTrue(pl.winfo_exists())
        self.assertTrue(self.c.settings['pl_open'])

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
            self.c.cycle_size()
            pump(400)
            for w in self.c._windows():
                x, y, ww, hh = rect(w)
                self.assertEqual(reachable((x, y, ww, hh), [(0, 0, 1000, 700)], grip=28), (x, y))

    def test_a_stack_taller_than_the_screen_keeps_the_main_window_reachable(self):
        self.c.settings['positions'] = {'main': [100, 300], 'eq': [100, 416], 'pl': [100, 532]}
        areas = [(0, 0, 1000, 400)]
        with unittest.mock.patch.object(skin_mode, 'work_areas', lambda win: areas):
            self.open_skin()
            pump(skin_mode.KEEP_MS + 200)
            x, y, ww, hh = rect(self.c.main)
            self.assertEqual(reachable((x, y, ww, hh), areas), (x, y))
            self.assertEqual(rect(self.c.eq)[:2], (x, y + 116))          # still one flush stack
            self.assertEqual(rect(self.c.pl)[:2], (x, y + 232))
            self.assertEqual(self.c._main_pos, (self.c.main.winfo_x(), self.c.main.winfo_y()))

    def test_eq_destroyed_from_outside_is_remembered_as_closed(self):
        self.open_skin()
        self.c.eq.destroy()
        pump(skin_mode.REFRESH_MS + 200)
        self.assertIsNone(self.c.eq)
        self.assertFalse(self.c.settings['eq_open'])
        self.assertTrue(self.c.active)

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


@unittest.skipIf(_root is None or not WIN, "needs Windows and a display")
class SkinStackTests(unittest.TestCase):
    """While skinned the player is one block: main, EQ under it when open, the playlist (always open) at the bottom."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(os.path.join(self.tmp.name, 'skins'))
        self.skin = _skin(self.tmp.name)
        os.replace(os.path.join(self.tmp.name, 'syn.wsz'), os.path.join(self.tmp.name, 'skins', 'syn.wsz'))
        self.p = _player(self.tmp.name)
        self.addCleanup(self.p.root.destroy)
        self.c = skin_mode.SkinController(self.p)
        self.addCleanup(self.c._close_windows)
        self.c.settings['positions'] = {'main': [300, 200]}

    def open_skin(self):
        self.c.pick('syn.wsz')
        pump(300)

    def assert_stacked(self):
        s = self.c.scale()
        mx, my = self.c.main.winfo_x(), self.c.main.winfo_y()
        y = my + 116 * s
        if self.c.eq is not None:
            self.assertEqual((self.c.eq.winfo_x(), self.c.eq.winfo_y()), (mx, y))
            y += 116 * s
        self.assertEqual((self.c.pl.winfo_x(), self.c.pl.winfo_y()), (mx, y))

    def test_playlist_opens_with_every_skin_even_if_saved_closed(self):
        self.c.settings['pl_open'] = False
        self.open_skin()
        self.assertTrue(self.c.pl is not None and self.c.pl.winfo_exists())
        self.assert_stacked()

    def test_playlist_cannot_be_closed(self):
        self.open_skin()
        pl = self.c.pl
        self.c.toggle_pl()
        pl.callbacks['close']()
        ctypes.windll.user32.PostMessageW(hwnd(pl), WM_CLOSE, 0, 0)
        pump(200)
        self.assertIs(self.c.pl, pl)
        self.assertTrue(pl.winfo_exists())

    def test_stack_ignores_saved_eq_and_playlist_spots(self):
        self.c.settings['positions'] = {'main': [300, 200], 'eq': [900, 900], 'pl': [50, 50]}
        self.open_skin()
        self.assertEqual(rect(self.c.eq)[:2], (300, 316))
        self.assertEqual(rect(self.c.pl)[:2], (300, 432))

    def test_closing_eq_moves_the_playlist_up_and_opening_moves_it_down(self):
        self.open_skin()
        self.c.toggle_eq()
        pump(200)
        self.assertIsNone(self.c.eq)
        self.assertEqual(rect(self.c.pl)[:2], (300, 316))
        self.c.toggle_eq()
        pump(200)
        self.assertEqual(rect(self.c.pl)[:2], (300, 432))
        self.assert_stacked()

    def test_moving_main_moves_the_stack(self):
        self.open_skin()
        self.c.main.geometry('+500+300')
        pump(200)
        self.assertEqual(rect(self.c.eq)[:2], (500, 416))
        self.assertEqual(rect(self.c.pl)[:2], (500, 532))

    def test_double_size_keeps_the_stack_flush(self):
        self.open_skin()
        self.c.cycle_size()
        pump(300)
        self.assertEqual(self.c.scale(), 2)
        self.assert_stacked()

    def test_3x_stack_is_flush_and_three_times_as_big(self):
        self.open_skin()
        with unittest.mock.patch.object(skin_mode, 'work_areas', lambda win: [(0, 0, 4000, 3000)]):
            self.c.set_size(3)
            pump(400)
        self.assertEqual(rect(self.c.main)[2:], (825, 348))
        self.assertEqual(rect(self.c.eq)[:2], (300, 200 + 348))
        self.assertEqual(rect(self.c.pl)[:2], (300, 200 + 696))
        self.assertEqual(rect(self.c.pl)[3], 232 * 3)

    def test_dragging_the_playlist_title_moves_the_whole_stack(self):
        self.open_skin()
        self.c.pl.callbacks['drag'](600, 500)
        pump(200)
        self.assertEqual(rect(self.c.main)[:2], (600, 268))
        self.assert_stacked()
        self.c.pl.callbacks['moved'](0, 0)
        pump(300)
        self.assert_stacked()

    def test_dragging_the_eq_title_moves_the_whole_stack(self):
        self.open_skin()
        self.c.eq.callbacks['drag'](700, 400)
        pump(200)
        self.assertEqual(rect(self.c.main)[:2], (700, 284))
        self.assert_stacked()

    def test_playlist_window_hands_a_title_drag_to_the_drag_callback(self):
        got = []
        import skin_playlist
        w = skin_playlist.SkinPlaylistWindow(_root, self.skin, 1, {'drag': lambda x, y: got.append((x, y))})
        self.addCleanup(w.destroy)
        w.geometry('+300+432')
        pump(100)
        ev = lambda xr, yr: SimpleNamespace(x=50, y=5, x_root=xr, y_root=yr)  # noqa: E731
        w._on_press(ev(350, 437))
        w._on_motion(ev(650, 505))
        pump(100)
        self.assertEqual(got, [(600, 500)])
        self.assertEqual((w.winfo_x(), w.winfo_y()), (300, 432))

    def test_eq_window_hands_a_title_drag_to_the_drag_callback(self):
        got = []
        from skin_ui import SkinEqWindow
        w = SkinEqWindow(_root, self.skin, 1, {'drag': lambda x, y: got.append((x, y))})
        self.addCleanup(w.destroy)
        w.geometry('+300+316')
        pump(100)
        ev = lambda xr, yr: SimpleNamespace(x=50, y=5, x_root=xr, y_root=yr)  # noqa: E731
        w._on_press(ev(350, 321))
        w._on_motion(ev(650, 421))
        pump(100)
        self.assertEqual(got, [(600, 416)])
        self.assertEqual((w.winfo_x(), w.winfo_y()), (300, 316))

    def test_albums_docked_beside_the_playlist_follows_the_stack(self):
        self.open_skin()
        self.c.toggle_albums()
        pump(200)
        px, py = self.c.pl.winfo_x(), self.c.pl.winfo_y()
        self.c.albums.geometry(f'+{px + 275}+{py}')
        pump(200)
        self.c.main.geometry('+340+230')
        pump(300)
        self.assertEqual(rect(self.c.albums)[:2], (px + 275 + 40, py + 30))
        self.assert_stacked()

    # ---- right-click on the playlist ----------------------------------------------------------

    def right_click(self, y):
        return SimpleNamespace(x=5, y=y, x_root=10, y_root=20)

    def test_right_click_below_the_songs_opens_the_main_menu(self):
        self.open_skin()
        self.c.pl.set_rows(['a', 'b'])
        pump(100)
        bx, by, bw, bh = self.c.pl.listbox.bbox(1)
        with unittest.mock.patch.object(self.c, 'show_menu') as menu:
            self.c.pl._on_menu(self.right_click(by + bh + 10))
        menu.assert_called_once_with(10, 20)
        self.assertEqual(self.c.pl.listbox.curselection(), ())

    def test_right_click_on_an_empty_playlist_opens_the_main_menu(self):
        self.open_skin()
        self.c.pl.set_rows([])
        pump(100)
        with unittest.mock.patch.object(self.c, 'show_menu') as menu:
            self.c.pl._on_menu(self.right_click(10))
        menu.assert_called_once_with(10, 20)

    def test_right_click_on_a_song_still_gives_the_song_menu(self):
        self.open_skin()
        self.c.pl.set_rows(['a', 'b'])
        pump(100)
        got = []
        self.c.pl.callbacks['menu'] = lambda *a: got.append(a)
        bx, by, bw, bh = self.c.pl.listbox.bbox(0)
        self.c.pl._on_menu(self.right_click(by + bh // 2))
        self.assertEqual(got, [(10, 20, 0)])

    def test_playlist_destroyed_from_outside_comes_back(self):
        self.open_skin()
        self.c.pl.destroy()
        pump(skin_mode.REFRESH_MS + 200)
        self.assertTrue(self.c.pl is not None and self.c.pl.winfo_exists())
        self.assert_stacked()


RED, BLUE = (255, 0, 0), (0, 0, 255)


@unittest.skipIf(_root is None or not WIN, "needs Windows and a display")
class SkinCoverTests(unittest.TestCase):
    """The album cover docked beside the stack (spec 2026-11-16-skin-album-art)."""

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
        self.c.settings['positions'] = {'main': [300, 200]}
        self.pics = []
        orig = skin_cover.SkinCoverWindow.set_picture

        def spy(win, image):
            self.pics.append(image)
            orig(win, image)
        patcher = unittest.mock.patch.object(skin_cover.SkinCoverWindow, 'set_picture', spy)
        patcher.start()
        self.addCleanup(patcher.stop)

    def open_skin(self):
        self.c.pick('syn.wsz')
        pump(300)

    def center(self):
        img = self.pics[-1]
        return img.getpixel((img.width // 2, img.height // 2))

    def saved(self):
        with open(self.c.settings_path, encoding='utf-8') as f:
            return json.load(f)

    # ---- where it is ---------------------------------------------------------------------------

    def test_cover_opens_beside_main(self):
        self.open_skin()
        m = rect(self.c.main)
        self.assertEqual(rect(self.c.cover), (m[0] + 275, m[1], 275, 301))

    def test_cover_follows_size(self):
        self.open_skin()
        self.c.set_size(2)
        pump(300)
        m = rect(self.c.main)
        self.assertEqual(rect(self.c.cover), (m[0] + 550, m[1], 550, 602))
        self.assertEqual(self.pics[-1].size, (486, 486))

    def test_cover_stays_level_when_eq_toggles(self):
        self.open_skin()
        for _ in range(2):
            self.c.toggle_eq()
            pump(200)
            self.assertEqual(rect(self.c.cover)[:2], (rect(self.c.main)[0] + 275, rect(self.c.main)[1]))

    def test_cover_drag_moves_stack(self):
        self.open_skin()
        (mx, my), (cx, cy) = rect(self.c.main)[:2], rect(self.c.cover)[:2]
        ev = lambda xr, yr: SimpleNamespace(x=50, y=5, x_root=xr, y_root=yr)  # noqa: E731
        self.c.cover._on_press(ev(cx + 50, cy + 5))
        self.c.cover._on_motion(ev(cx + 150, cy + 105))
        pump(200)
        self.assertEqual(rect(self.c.main)[:2], (mx + 100, my + 100))
        self.assertEqual(rect(self.c.cover)[:2], (cx + 100, cy + 100))

    # ---- hiding and showing ----------------------------------------------------------------------

    def test_cover_close_hides_and_saves(self):
        self.open_skin()
        self.c.cover._fire('close')
        self.assertIsNone(self.c.cover)
        self.assertFalse(self.saved()['cover_open'])
        self.c.toggle_cover()
        self.assertIsNotNone(self.c.cover)
        self.assertTrue(self.saved()['cover_open'])

    def test_alt_f4_on_cover_hides(self):
        self.open_skin()
        ctypes.windll.user32.PostMessageW(hwnd(self.c.cover), WM_CLOSE, 0, 0)
        pump(200)
        self.assertIsNone(self.c.cover)
        self.assertFalse(self.c.settings['cover_open'])
        self.assertTrue(self.c.active)

    def test_saved_hidden_cover_stays_hidden(self):
        self.c.settings['cover_open'] = False
        self.open_skin()
        self.assertIsNone(self.c.cover)

    def test_menu_album_art_item(self):
        self.open_skin()
        menus = []
        with unittest.mock.patch.object(tk.Menu, 'tk_popup', lambda m, x, y: menus.append(m)):
            self.c.show_menu(10, 20)
        menu = menus[0]
        found = [i for i in range(menu.index('end') + 1)
                 if menu.type(i) == 'checkbutton' and menu.entrycget(i, 'label') == 'Album art']
        self.assertEqual(len(found), 1)
        self.assertTrue(int(menu.getvar(menu.entrycget(found[0], 'variable'))))
        menu.invoke(found[0])
        self.assertIsNone(self.c.cover)

    def test_right_click_opens_main_menu(self):
        self.open_skin()
        self.c.toggle_albums()
        pump(200)
        with unittest.mock.patch.object(self.c, 'show_menu') as menu:
            for widget in (self.c.cover.art, self.c.cover.canvas, self.c.albums.canvas):
                widget.event_generate('<Button-3>', x=30, y=30)
                _root.update()
        self.assertEqual(menu.call_count, 3)
        for call in menu.call_args_list:
            self.assertEqual(len(call.args), 2)

    # ---- every window path -------------------------------------------------------------------------

    def test_cover_in_window_paths(self):
        self.open_skin()
        self.assertIn(self.c.cover, self.c._windows())
        self.c.toggle_topmost()
        pump(100)
        self.assertTrue(exstyle(self.c.cover) & WS_EX_TOPMOST)
        skin_mode.minimize_window(self.c.main)
        pump(300)
        self.assertEqual(self.c.cover.wm_state(), 'withdrawn')
        self.c.main.deiconify()
        pump(300)
        self.assertEqual(self.c.cover.wm_state(), 'normal')
        other = tk.Toplevel(_root)
        self.addCleanup(other.destroy)
        other.geometry('600x600+250+150')
        pump(100)
        other.lift()
        pump(100)
        self.c._raise_group(self.c.main)
        pump(100)
        self.assertTrue(above(self.c.cover, other))

    def test_lost_cover_is_remembered_as_hidden(self):
        self.open_skin()
        self.c.cover.destroy()
        pump(skin_mode.REFRESH_MS + 200)
        self.assertIsNone(self.c.cover)
        self.assertFalse(self.c.settings['cover_open'])
        self.assertTrue(self.c.active)

    def test_cover_torn_down(self):
        self.open_skin()
        old = self.c.cover
        self.c.pick('syn.wsz')                          # switching skins rebuilds every window
        pump(200)
        self.assertFalse(old.winfo_exists())
        self.assertTrue(self.c.cover.winfo_exists())
        self.c.pick(None)
        self.assertIsNone(self.c.cover)
        loaded = skin_mode.skin_lib.load_skin(os.path.join(self.tmp.name, 'skins', 'syn.wsz'))
        with unittest.mock.patch.object(self.c, '_later_keep_reachable', side_effect=RuntimeError('late')):
            self.c.enter(loaded)
        self.assertIsNone(self.c.cover)

    # ---- the picture ---------------------------------------------------------------------------------

    def test_cover_shows_track_art(self):
        from PIL import Image, ImageColor
        self.p._fallback_artwork_image = Image.new('RGB', (100, 100), BLUE)
        self.open_skin()
        cases = [('playing', Image.new('RGB', (100, 100), RED), RED),
                 ('paused', Image.new('RGB', (100, 100), RED), RED),
                 ('stopped', Image.new('RGB', (100, 100), RED), BLUE),
                 ('playing', None, BLUE)]
        for state, art, want in cases:
            with self.subTest(state=state, art=art is not None):
                self.p._playback_state, self.p.current_artwork_image = state, art
                self.c._refresh()
                self.assertEqual(self.center(), want)
        self.p._fallback_artwork_image = None
        self.c._refresh()
        self.assertEqual(self.center(), ImageColor.getrgb(self.c.skin.pledit['normalbg']))

    def test_render_only_on_change(self):
        from PIL import Image
        self.p._playback_state, self.p.current_artwork_image = 'playing', Image.new('RGB', (50, 50), RED)
        self.open_skin()
        self.c._refresh()
        n = len(self.pics)
        self.c._refresh()
        self.assertEqual(len(self.pics), n)
        self.p.current_artwork_image = Image.new('RGB', (50, 50), RED)
        self.c._refresh()
        self.assertEqual(len(self.pics), n + 1)
        self.c.set_size(2)
        self.assertEqual(len(self.pics), n + 2)
        self.assertEqual(self.pics[-1].size, (486, 486))

    def test_reshown_cover_shows_current_art(self):
        from PIL import Image
        self.p._playback_state, self.p.current_artwork_image = 'playing', Image.new('RGB', (50, 50), RED)
        self.open_skin()
        self.c.toggle_cover()
        self.p.current_artwork_image = Image.new('RGB', (50, 50), BLUE)
        self.c._refresh()
        self.c.toggle_cover()
        self.assertEqual(self.center(), BLUE)

    def test_paused_skin_switch_keeps_track_art(self):
        from PIL import Image
        self.p._fallback_artwork_image = Image.new('RGB', (100, 100), BLUE)
        self.p._playback_state, self.p.current_artwork_image = 'paused', Image.new('RGB', (50, 50), RED)
        self.open_skin()
        self.c.pick('syn.wsz')
        pump(200)
        self.assertEqual(self.center(), RED)


if __name__ == '__main__':
    unittest.main()
