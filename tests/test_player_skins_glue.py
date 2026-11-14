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
        _eq_presets=lambda: {'Flat': ([0.0] * 10, 0.0)})
    for name in ('previous_track', 'play', 'pause', 'stop', 'next_track', 'apply_shuffle', '_on_close',
                 'toggle_loved', 'open_stats_window', 'toggle_play_pause', '_import_eqf', 'apply_eq',
                 '_play_first_result', '_takeover_playlist', 'play_track_at_index', 'apply_theme',
                 'queue_track', '_album_action'):
        setattr(p, name, stub(name))
    return p


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
        self.c.toggle_double()
        s = self.settings_file()
        self.assertEqual((s['eq_open'], s['always_on_top'], s['double_size']), (False, True, True))
        self.assertEqual(self.c.main.scale, 2)

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
        self.assertEqual((s['skin'], s['double_size'], s['vis_mode']), (None, False, 'spectrum'))


if __name__ == '__main__':
    unittest.main()
