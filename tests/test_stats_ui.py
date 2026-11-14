"""
tests/test_stats_ui.py - the Listening Stats window (real Tk, hidden root).

Run with:
    python -m unittest tests.test_stats_ui -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    import tkinter as tk
    from tkinter import ttk
    _root = tk.Tk()
    _root.withdraw()
except Exception:  # no display
    _root = None

import listening_stats as ls  # noqa: E402
from stats_ui import StatsWindow, fmt_hours, fmt_summary, period_title  # noqa: E402

THEME = {'bg': '#202020', 'fg': '#e0e0e0', 'frame_bg': '#2a2a2a', 'button_bg': '#3a3a3a', 'secondary_fg': '#909090'}
A = ls.Place('art a', 'Art A', 'alb1', 'Album 1')


def _stats(share=0.75):
    song = ls.Row('/a1', 'Song One', 'Art A', plays=4, hours=0.25, skips=2, starts=6)
    return ls.PeriodStats(
        summary=ls.Summary(hours=2.5, plays=10, songs=6, artists=3, new_songs=1, shuffle_share=share),
        top_artists=[ls.Row('art a', 'Art A', '', plays=7, hours=2.0)],
        top_songs=[song, ls.Row('/gone', 'Gone', 'Old Band', plays=1, hours=0.1)],
        top_albums=[ls.Row('alb1', 'Album 1', 'Art A', plays=7, hours=2.0)],
        skipped_songs=[song], skipped_artists=[],
        by_hour=[0.0] * 23 + [1.0], by_weekday=[0.5] * 7)


@unittest.skipIf(_root is None, "no display")
class StatsWindowTests(unittest.TestCase):
    def setUp(self):
        self.periods = []
        self.ran = []

        def menu_items(kind, key):
            if key == '/gone':
                return []
            return [("Play", lambda: self.ran.append((kind, key)))]

        self.win = StatsWindow(_root, self.periods.append, menu_items, THEME)
        self.win.withdraw()
        months = [ls.MonthRow(2026, 11, 2.5, 10, 'Art A', 'Song One'), ls.MonthRow(2026, 10, 1.0, 3, 'Art A', 'X')]
        favs = [ls.TrackInfo('/f1', 'Fav', A, last_played=None, loved=True)]
        never = [ls.TrackInfo('/n1', 'New', A, date_added=None)]
        self.win.show('This week', _stats(), months, favs, never, False)
        _root.update_idletasks()

    def tearDown(self):
        self.win.destroy()

    def test_show_fills_lists_and_summary(self):
        w = self.win
        self.assertEqual(len(w.songs_tree.get_children()), 2)
        self.assertEqual(len(w.artists_tree.get_children()), 1)
        self.assertEqual(len(w.skipped_songs_tree.get_children()), 1)
        self.assertEqual(w.skipped_songs_tree.item(w.skipped_songs_tree.get_children()[0], 'values')[2], '2 of 6')
        self.assertEqual(len(w.months_tree.get_children()), 2)
        self.assertEqual(w.summary_label.cget('text'), '2.5 h · 10 plays · 6 songs · 3 artists · 1 new · 75% shuffle')
        self.assertEqual(w.never_frame_label.get(), 'Never played (1 songs)')
        self.assertEqual(w.note_label.cget('text'), '')
        self.assertEqual(w.title_label.cget('text'), 'This week')

    def test_all_time_note(self):
        self.win.show('All time', _stats(None), [], [], [], True)
        self.assertIn('iTunes history', self.win.note_label.cget('text'))
        self.assertIn('– shuffle', self.win.summary_label.cget('text'))

    def test_period_buttons_call_on_period(self):
        buttons = []

        def walk(w):
            for c in w.winfo_children():
                if isinstance(c, ttk.Radiobutton):
                    buttons.append(c)
                walk(c)

        walk(self.win)
        self.assertEqual([b.cget('text') for b in buttons], ['This week', 'This month', 'This year', 'All time'])
        buttons[3].invoke()
        self.assertEqual(self.periods, ['all'])

    def test_month_double_click_picks_that_month(self):
        first = self.win.months_tree.get_children()[0]
        self.win._pick_month(first)
        self.assertEqual(self.periods, [(2026, 11)])
        self.assertEqual(self.win.period_var.get(), '')

    def test_row_menu_calls_action(self):
        w = self.win
        rows = w.songs_tree.get_children()
        items = w._items_for(w.songs_tree, rows[0])
        items[0][1]()
        self.assertEqual(self.ran, [('song', '/a1')])
        self.assertEqual(w._items_for(w.songs_tree, rows[1]), [])      # gone from the library: no menu
        fav = w.favorites_tree.get_children()[0]
        w._items_for(w.favorites_tree, fav)[0][1]()
        self.assertEqual(self.ran[-1], ('song', '/f1'))

    def test_reshow_replaces_rows(self):
        self.win.show('This month', _stats(), [], [], [], False)
        self.assertEqual(len(self.win.songs_tree.get_children()), 2)
        self.assertEqual(len(self.win.months_tree.get_children()), 0)


class FormatTests(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(fmt_hours(0), '0 min')
        self.assertEqual(fmt_hours(0.5), '30 min')
        self.assertEqual(fmt_hours(2.44), '2.4 h')
        self.assertEqual(period_title((2026, 3)), 'March 2026')
        self.assertEqual(period_title('year'), 'This year')
        s = ls.Summary(hours=0, plays=0, songs=0, artists=0, new_songs=0, shuffle_share=None)
        self.assertEqual(fmt_summary(s), '0 min · 0 plays · 0 songs · 0 artists · 0 new · – shuffle')


if __name__ == '__main__':
    unittest.main()
