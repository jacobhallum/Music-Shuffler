"""
tests/test_player_skins_glue.py - SkinController: picking, saving, startup fallback, root hiding, status to marquee.

Uses a hidden Tk root and a fake player (only the attributes the controller touches), with a synthetic skin.

Run with:
    python -m unittest tests.test_player_skins_glue -v
"""

import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    import tkinter as tk
    from tkinter import ttk
    _root = tk.Tk()
    _root.withdraw()
except Exception:
    _root = None

if _root is not None:
    import skin_mode  # noqa: E402
    from tests.test_skin_ui import _skin  # noqa: E402


def _player(base):
    win = tk.Toplevel(_root)
    win.title("Music Player V2")
    calls = []
    stub = lambda name: (lambda *a, **k: calls.append((name,) + a))  # noqa: E731
    p = SimpleNamespace(
        root=win, calls=calls, listening_log_file=os.path.join(base, 'listening_log.jsonl'),
        status_label=ttk.Label(win, text='Ready'), _engine=None, _engine_lock=None,
        volume_var=tk.DoubleVar(win, value=40), _playback_state='stopped', current_playlist=[], current_index=0,
        is_temp_view=False, filtered_playlist=[], queued_count=0, library_index=None, _album_browsers=[],
        albums_browser=None, settings_menu=tk.Menu(win, tearoff=0), search_var=tk.StringVar(win),
        audio_settings={'eq_enabled': True, 'eq_bands_db': [0.0] * 10, 'eq_preamp_db': 0.0},
        _eq_presets=lambda: {'Flat': ([0.0] * 10, 0.0)},
        _queue_appcommand=lambda cmd: calls.append(('appcommand', cmd)) or True,
        current_artwork_image=None, _fallback_artwork_image=None)
    for name in ('previous_track', 'play', 'pause', 'stop', 'next_track', 'apply_shuffle', '_on_close',
                 'toggle_loved', 'open_stats_window', 'toggle_play_pause', '_import_eqf', 'apply_eq',
                 '_play_first_result', '_takeover_playlist', 'play_track_at_index', 'apply_theme',
                 'queue_track', '_album_action'):
        setattr(p, name, stub(name))
    return p


def _user32():
    import ctypes
    from ctypes import wintypes
    u = ctypes.WinDLL('user32')
    u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    u.GetAncestor.restype = wintypes.HWND
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u.SendMessageW.restype = ctypes.c_ssize_t
    return u


def _fnlock_targets(windows):
    """Frames of the given Tk windows that fn-lock's lookup could pick: visible, title starting "Music Player V2"."""
    import ctypes
    u, out = _user32(), []
    for w in windows:
        if w is None:
            continue
        hwnd = u.GetAncestor(w.winfo_id(), 2)
        buf = ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(hwnd, buf, 256)
        if u.IsWindowVisible(hwnd) and buf.value.startswith('Music Player V2'):
            out.append(hwnd)
    return out


def _send_appcommand(hwnd, cmd):
    _user32().SendMessageW(hwnd, 0x0319, hwnd, cmd << 16)


@unittest.skipIf(_root is None, "no display")
class SkinControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(os.path.join(self.tmp.name, 'skins'))
        syn = _skin(self.tmp.name)                       # writes syn.wsz into tmp
        os.replace(os.path.join(self.tmp.name, 'syn.wsz'), os.path.join(self.tmp.name, 'skins', 'syn.wsz'))
        self.skin_name = os.path.basename(syn.name)
        self.p = _player(self.tmp.name)
        self.addCleanup(self.p.root.destroy)
        self.c = skin_mode.SkinController(self.p)
        self.addCleanup(self.c._close_windows)

    def settings_file(self):
        with open(os.path.join(self.tmp.name, 'ui_settings.json'), encoding='utf-8') as f:
            return json.load(f)

    def test_pick_skin_saves_and_switches(self):
        self.c.pick('syn.wsz')
        _root.update()
        self.assertTrue(self.c.active)
        self.assertEqual(self.settings_file()['skin'], 'syn.wsz')
        self.assertEqual(self.p.root.state(), 'withdrawn')
        self.assertIsNotNone(self.c.eq)
        self.assertIsNotNone(self.c.pl)

    def test_missing_saved_skin_opens_default_view(self):
        skin_mode.save_settings(self.c.settings_path, dict(skin_mode.DEFAULTS, skin='gone.wsz'))
        c = skin_mode.SkinController(self.p)
        c.start()
        self.assertFalse(c.active)
        self.assertIn('gone.wsz', self.p.status_label.cget('text'))
        self.assertEqual(self.settings_file()['skin'], 'gone.wsz')    # kept for when the file comes back

    def test_broken_saved_skin_opens_default_view(self):
        with open(os.path.join(self.tmp.name, 'skins', 'bad.wsz'), 'wb') as f:
            f.write(b'nope')
        skin_mode.save_settings(self.c.settings_path, dict(skin_mode.DEFAULTS, skin='bad.wsz'))
        c = skin_mode.SkinController(self.p)
        c.start()
        self.assertFalse(c.active)
        self.assertIn("Couldn't load skin bad.wsz", self.p.status_label.cget('text'))

    def test_no_skin_brings_back_default_view(self):
        self.c.pick('syn.wsz')
        self.c.pick(None)
        _root.update()
        self.assertFalse(self.c.active)
        self.assertEqual(self.p.root.state(), 'normal')
        self.assertIsNone(self.settings_file()['skin'])
        self.assertIn(('apply_theme',), self.p.calls)

    def test_root_mapped_while_skinned_hides_again(self):
        self.c.pick('syn.wsz')
        _root.update()
        self.p.root.deiconify()          # what a second launch's ShowWindow does
        _root.update()
        for _ in range(5):
            _root.update()
        self.assertEqual(self.p.root.state(), 'withdrawn')

    def test_skinned_mode_keeps_the_root_hook_and_title(self):
        self.c.pick('syn.wsz')
        self.assertEqual(self.p.root.title(), 'Music Player V2')
        self.assertNotEqual(self.c.main.title(), 'Music Player V2')

    def fnlock_keys_reach_player(self):
        """fn-lock (AutoHotkey) sends F5-F8 / the dial to the first *visible* window whose title *starts with*
        "Music Player V2". Every window of ours it could pick must hand the key to the player."""
        _root.update()
        targets = _fnlock_targets([self.p.root, self.c.main, self.c.eq, self.c.pl])
        self.assertTrue(targets, 'fn-lock finds no window while skinned')
        before = self.p.calls.count(('appcommand', 14))
        for hwnd in targets:
            _send_appcommand(hwnd, 14)
        self.assertEqual(self.p.calls.count(('appcommand', 14)) - before, len(targets))

    @unittest.skipIf(sys.platform != 'win32', 'Windows only')
    def test_fnlock_keys_reach_player_while_skinned(self):
        self.c.pick('syn.wsz')
        self.fnlock_keys_reach_player()

    @unittest.skipIf(sys.platform != 'win32', 'Windows only')
    def test_fnlock_keys_reach_player_after_size_change(self):
        self.c.pick('syn.wsz')
        self.c.set_size(3)
        self.fnlock_keys_reach_player()

    @unittest.skipIf(sys.platform != 'win32', 'Windows only')
    def test_failed_enter_takes_the_key_hook_off(self):
        loaded = skin_mode.skin_lib.load_skin(os.path.join(self.tmp.name, 'skins', 'syn.wsz'))
        with unittest.mock.patch.object(self.c, '_later_keep_reachable', side_effect=RuntimeError('late')):
            reason = self.c.enter(loaded)       # fails after the hook went on
        self.assertIn('late', reason)
        self.assertFalse(self.c.active)
        self.assertIsNone(self.c._key_hook)

    @unittest.skipIf(sys.platform != 'win32', 'Windows only')
    def test_fnlock_keys_reach_player_after_leaving_and_coming_back(self):
        self.c.pick('syn.wsz')
        _root.update()
        self.c.pick(None)
        self.assertIsNone(self.c._key_hook)
        self.c.pick('syn.wsz')
        self.fnlock_keys_reach_player()

    def test_status_goes_to_marquee_while_skinned(self):
        self.c.pick('syn.wsz')
        self.c._refresh()
        self.p.status_label.config(text='Added to Up Next (2 queued)')
        self.c._refresh()
        self.assertEqual(self.c.main.flash_text, 'ADDED TO UP NEXT (2 QUEUED)')

    def test_window_choices_are_saved(self):
        self.c.pick('syn.wsz')
        self.c.toggle_eq()
        self.c.toggle_topmost()
        self.c.cycle_size()
        s = self.settings_file()
        self.assertEqual((s['eq_open'], s['always_on_top'], s['size']), (False, True, 2))
        self.assertNotIn('double_size', s)
        self.assertEqual(self.c.main.scale, 2)

    def test_size_cycles_one_two_three_and_is_saved(self):
        self.c.pick('syn.wsz')
        for want in (2, 3, 1):
            self.c.cycle_size()
            self.assertEqual(self.c.scale(), want)
            self.assertEqual(self.settings_file()['size'], want)
            self.assertEqual(self.c.main.scale, want)
        self.c.set_size(3)
        self.assertEqual((self.c.scale(), self.c.pl.scale, self.c.eq.scale), (3, 3, 3))

    def test_old_double_size_setting_reads_as_2x(self):
        for text, want in (('{"double_size": true}', 2), ('{"double_size": false}', 1), ('{"size": 7}', 1),
                           ('{"size": "3"}', 1), ('{"size": true}', 1), ('{"size": 3, "double_size": true}', 3)):
            with open(self.c.settings_path, 'w', encoding='utf-8') as f:
                f.write(text)
            self.assertEqual(skin_mode.load_settings(self.c.settings_path)['size'], want, text)

    def test_cover_open_setting(self):
        for text, want in (('{"cover_open": false}', False), ('{"cover_open": "yes"}', True), ('{}', True)):
            with open(self.c.settings_path, 'w', encoding='utf-8') as f:
                f.write(text)
            self.assertIs(skin_mode.load_settings(self.c.settings_path)['cover_open'], want, text)

    def test_eq_window_change_goes_to_the_player(self):
        self.c.pick('syn.wsz')
        self.c._eq_change([1.0] * 10, 2.0, True)
        self.assertEqual(self.p.calls[-1], ('apply_eq', [1.0] * 10, 2.0, True, ''))

    def test_skins_menu_lists_skins_and_no_skin(self):
        menu = tk.Menu(self.p.root, tearoff=0)
        self.c.fill_skins_menu(menu)
        labels = [menu.entrycget(i, 'label') for i in range(menu.index(tk.END) + 1)
                  if menu.type(i) != 'separator']
        self.assertEqual(labels, ['syn', 'No skin (default view)', 'Open skins folder'])

    def test_settings_survive_junk(self):
        with open(self.c.settings_path, 'w', encoding='utf-8') as f:
            f.write('{"skin": 5, "double_size": "yes", "vis_mode": "laser"}')
        s = skin_mode.load_settings(self.c.settings_path)
        self.assertEqual((s['skin'], s['size'], s['vis_mode']), (None, 1, 'spectrum'))
        bad = {'skin': '..\\..\\evil.wsz', 'pl_height': True,
               'positions': {'main': [1, 2], 'eq': 'x', 'pl': [1, 2, 3], 'albums': [True, 4]}}
        with open(self.c.settings_path, 'w', encoding='utf-8') as f:
            json.dump(bad, f)
        s = skin_mode.load_settings(self.c.settings_path)
        self.assertEqual((s['skin'], s['pl_height'], s['positions']), (None, 232, {'main': [1, 2]}))
        with open(self.c.settings_path, 'w', encoding='utf-8') as f:
            json.dump({'pl_height': 99999}, f)
        self.assertEqual(skin_mode.load_settings(self.c.settings_path)['pl_height'], 2000)

    def test_offscreen_positions_come_back_fully_on_screen(self):
        areas = [(0, 0, 1920, 1040)]
        self.c.settings['positions'] = {'main': [-99999, 50], 'eq': [300, 300]}
        with unittest.mock.patch.object(skin_mode, 'work_areas', lambda win: areas):
            self.c.pick('syn.wsz')
            done = []
            _root.after(skin_mode.KEEP_MS + 150, lambda: done.append(1))
            while not done:
                _root.update()
        x, y = self.c.settings['positions']['main']
        self.assertTrue(0 <= x <= 1920 - 275 and 0 <= y <= 1040 - 116, (x, y))
        self.assertEqual(self.c.settings['positions'].get('eq'), [300, 300])

    def test_a_skin_that_fails_to_show_brings_back_the_default_view(self):
        real = skin_mode.SkinEqWindow

        def broken(*a, **k):
            raise ValueError('odd skin data')
        skin_mode.SkinEqWindow = broken
        self.addCleanup(setattr, skin_mode, 'SkinEqWindow', real)
        with unittest.mock.patch.object(skin_mode.messagebox, 'showwarning'):
            self.c.pick('syn.wsz')
        _root.update()
        self.assertFalse(self.c.active)
        self.assertEqual(self.p.root.state(), 'normal')
        self.assertIsNone(self.settings_file()['skin'])

    def test_a_failing_switch_goes_back_to_the_previous_skin(self):
        import shutil
        shutil.copy(os.path.join(self.tmp.name, 'skins', 'syn.wsz'), os.path.join(self.tmp.name, 'skins', 'b.wsz'))
        self.c.pick('syn.wsz')
        real = skin_mode.SkinMainWindow

        def picky(master, loaded, *a, **k):
            if loaded.name == 'b.wsz':
                raise tk.TclError('nope')
            return real(master, loaded, *a, **k)
        skin_mode.SkinMainWindow = picky
        self.addCleanup(setattr, skin_mode, 'SkinMainWindow', real)
        with unittest.mock.patch.object(skin_mode.messagebox, 'showwarning'):
            self.c.pick('b.wsz')
        self.assertTrue(self.c.active)
        self.assertEqual(self.c.skin.name, 'syn.wsz')
        self.assertEqual(self.settings_file()['skin'], 'syn.wsz')

    def test_a_failing_tick_keeps_the_loop_alive(self):
        self.c.pick('syn.wsz')
        ticks = []

        def flaky():
            ticks.append(1)
            raise RuntimeError('boom')
        self.c._schedule(1, flaky)
        for _ in range(30):
            _root.update()
            if len(ticks) >= 3:
                break
            _root.after(5)
        self.assertGreaterEqual(len(ticks), 3)
        self.assertEqual(len(self.c._jobs), 3)     # refresh, vis, flaky: one pending id each


if __name__ == '__main__':
    unittest.main()
