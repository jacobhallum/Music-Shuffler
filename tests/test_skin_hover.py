"""
tests/test_skin_hover.py - hover labels on skinned windows: resting the mouse on a control for HOVER_MS shows a small
label saying what it does.

Run with (keeps the windows off the screen):
    python scripts/run_hidden.py python -m unittest tests.test_skin_hover -v
"""

import os
import sys
import unittest
import unittest.mock
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.withdraw()
except Exception:
    _root = None

if _root is not None:
    import skin_hover  # noqa: E402
    import skin_ui  # noqa: E402

COLORS = {'normalbg': '#000000', 'normal': '#00ff00', 'current': '#ffffff'}


def pump(ms):
    done = []
    _root.after(ms, lambda: done.append(1))
    while not done:
        _root.update()


def tip_text(h):
    """The label's text, or None when no label is showing."""
    if h.tip is None or not h.tip.winfo_exists():
        return None
    labels = [w for w in h.tip.winfo_children() if isinstance(w, tk.Label)]
    labels += [g for w in h.tip.winfo_children() for g in w.winfo_children() if isinstance(g, tk.Label)]
    return labels[0].cget('text') if labels else None


@unittest.skipIf(_root is None, "no display")
class HoverLabelTests(unittest.TestCase):
    def setUp(self):
        self.owner = tk.Toplevel(_root)
        self.owner.geometry('200x100+300+300')
        self.addCleanup(lambda: self.owner.winfo_exists() and self.owner.destroy())
        self.h = skin_hover.HoverLabel(self.owner, COLORS)

    def test_label_shows_only_after_the_mouse_rests(self):
        self.assertEqual(skin_hover.HOVER_MS, 500)
        self.h.point('play', 'Play', 400, 350)
        pump(200)
        self.assertIsNone(tip_text(self.h))
        pump(500)
        self.assertEqual(tip_text(self.h), 'Play')
        x, y = self.h.tip.winfo_x(), self.h.tip.winfo_y()
        self.assertEqual((x, y), (400 + 12, 350 + 18))

    def test_moving_to_nothing_or_leaving_hides_it(self):
        self.h.point('play', 'Play', 400, 350)
        pump(600)
        self.h.point(None, None, 450, 350)
        self.assertIsNone(tip_text(self.h))
        self.h.point('stop', 'Stop', 400, 350)
        pump(600)
        self.h.hide()
        self.assertIsNone(tip_text(self.h))

    def test_a_press_hides_it_until_the_mouse_moves_to_another_control(self):
        self.h.point('play', 'Play', 400, 350)
        pump(600)
        self.h.press()
        self.assertIsNone(tip_text(self.h))
        self.h.point('play', 'Play', 401, 351)          # still on the same control
        pump(700)
        self.assertIsNone(tip_text(self.h))
        self.h.point('stop', 'Stop', 430, 351)          # another control: waits again, then shows
        pump(700)
        self.assertEqual(tip_text(self.h), 'Stop')

    def test_staying_on_the_same_control_does_not_restart_the_wait(self):
        self.h.point('play', 'Play', 400, 350)
        pump(300)
        self.h.point('play', 'Play', 402, 351)
        pump(300)
        self.assertEqual(tip_text(self.h), 'Play')

    def test_fast_moves_leave_one_timer_and_at_most_one_label(self):
        for i in range(20):
            self.h.point('play' if i % 2 else 'stop', 'Play' if i % 2 else 'Stop', 400 + i, 350)
        pump(700)
        tips = [w for w in self.owner.winfo_children() if isinstance(w, tk.Toplevel)]
        self.assertEqual(len(tips), 1)
        self.assertEqual(tip_text(self.h), 'Play')       # the last control pointed at

    def test_owner_destroyed_takes_the_label_and_its_timer_with_it(self):
        self.h.point('play', 'Play', 400, 350)
        pump(600)
        tip = self.h.tip
        self.h.point('stop', 'Stop', 400, 350)           # a timer is pending again
        self.h.destroy()
        self.owner.destroy()
        pump(700)                                         # no TclError from a late timer
        self.assertIsNone(self.h.tip)
        self.assertFalse(tip.winfo_exists())

    def test_label_stays_inside_the_work_area(self):
        with unittest.mock.patch.object(skin_hover, '_areas', lambda win: [(0, 0, 800, 600)]):
            self.h.point('play', 'A rather long label for the edge', 795, 595)
            pump(600)
            t = self.h.tip
            t.update_idletasks()
            self.assertLessEqual(t.winfo_x() + t.winfo_width(), 800)
            self.assertLessEqual(t.winfo_y() + t.winfo_height(), 600)
            self.assertGreaterEqual(t.winfo_x(), 0)
            self.assertGreaterEqual(t.winfo_y(), 0)

    def test_label_is_placed_before_it_is_shown(self):
        """No flash at the screen's corner: the label is hidden while it is measured and moved into place."""
        seen = []
        real = tk.Toplevel.geometry

        def spy(win, *a):
            if a and a[0].startswith('+'):
                seen.append(bool(win.winfo_viewable()))
            return real(win, *a)
        with unittest.mock.patch.object(tk.Toplevel, 'geometry', spy):
            self.h.point('play', 'Play', 400, 350)
            pump(600)
        self.assertEqual(seen, [False])
        self.assertTrue(self.h.tip.winfo_viewable())

    def test_label_uses_the_skin_colours_and_a_fixed_small_font(self):
        self.h.point('play', 'Play', 400, 350)
        pump(600)
        labels = [g for w in [self.h.tip] + list(self.h.tip.winfo_children())
                  for g in ([w] if isinstance(w, tk.Label) else w.winfo_children()) if isinstance(g, tk.Label)]
        lab = labels[0]
        self.assertEqual((lab.cget('bg'), lab.cget('fg')), ('#000000', '#00ff00'))
        self.assertEqual(self.h.tip.cget('bg'), '#ffffff')      # 1 px border in the Current colour
        self.assertTrue(self.h.tip.attributes('-topmost'))

    def test_watch_feeds_the_key_under_the_pointer(self):
        canvas = tk.Canvas(self.owner, width=200, height=100)
        canvas.pack()
        self.owner.update()
        skin_hover.watch(self.h, canvas, lambda e: ('left', 'Left half') if e.x < 100 else None)
        canvas.event_generate('<Motion>', x=10, y=10)
        pump(600)
        self.assertEqual(tip_text(self.h), 'Left half')
        canvas.event_generate('<Motion>', x=150, y=10)
        self.assertIsNone(tip_text(self.h))
        canvas.event_generate('<Motion>', x=10, y=10)
        pump(600)
        canvas.event_generate('<ButtonPress-1>', x=10, y=10)
        self.assertIsNone(tip_text(self.h))
        canvas.event_generate('<Motion>', x=12, y=10)
        canvas.event_generate('<Leave>')
        self.assertIsNone(tip_text(self.h))


@unittest.skipIf(_root is None, "no display")
class WindowHoverTests(unittest.TestCase):
    """The skinned windows feed their own hit test to the label."""

    def setUp(self):
        import tempfile
        from tests.test_skin_ui import _skin
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.skin = _skin(self.tmp.name)

    def show(self, w):
        self.addCleanup(lambda: w.winfo_exists() and w.destroy())
        w.geometry('+300+300')
        pump(100)
        return w

    def hover_at(self, w, widget, x, y):
        widget.event_generate('<Motion>', x=x, y=y)
        pump(600)
        return tip_text(w.hover)

    def test_main_window_labels_the_control_under_the_pointer(self):
        w = self.show(skin_ui.SkinMainWindow(_root, self.skin, 1, {}))
        self.assertEqual(self.hover_at(w, w.canvas, 39 + 11, 88 + 9), 'Play')
        w.canvas.event_generate('<Leave>')
        self.assertIsNone(tip_text(w.hover))
        self.assertEqual(self.hover_at(w, w.canvas, 10 + 4, 47 + 4), 'Size: 1x / 2x / 3x')
        self.assertIsNone(self.hover_at(w, w.canvas, 150, 5))          # plain title bar: no label

    def test_main_window_at_3x_labels_the_right_control(self):
        w = self.show(skin_ui.SkinMainWindow(_root, self.skin, 3, {}))
        self.assertEqual(self.hover_at(w, w.canvas, (39 + 11) * 3, (88 + 9) * 3), 'Play')

    def test_pressing_hides_the_label(self):
        w = self.show(skin_ui.SkinMainWindow(_root, self.skin, 1, {}))
        self.assertEqual(self.hover_at(w, w.canvas, 85 + 11, 88 + 9), 'Stop')
        w.canvas.event_generate('<ButtonPress-1>', x=85 + 11, y=88 + 9)
        w.canvas.event_generate('<ButtonRelease-1>', x=85 + 11, y=88 + 9)
        self.assertIsNone(tip_text(w.hover))

    def test_eq_window_labels_bands(self):
        w = self.show(skin_ui.SkinEqWindow(_root, self.skin, 1, {}))
        self.assertEqual(self.hover_at(w, w.canvas, 78 + 7, 38 + 30), '60 Hz')
        self.assertEqual(self.hover_at(w, w.canvas, 14 + 13, 18 + 6), 'Equalizer on/off')

    def test_playlist_labels_grip_close_and_search(self):
        import skin_playlist
        w = self.show(skin_playlist.SkinPlaylistWindow(_root, self.skin, 1, {}))
        self.assertEqual(self.hover_at(w, w.canvas, 265, w.height - 10), 'Drag to resize')
        self.assertEqual(self.hover_at(w, w.canvas, 270, 5), 'Playlist stays open')
        self.assertEqual(self.hover_at(w, w.entry, 5, 5), 'Search (Enter plays the first match)')

    def test_albums_frame_window_has_no_playlist_labels(self):
        import skin_mode
        w = self.show(skin_mode.SkinFrameWindow(_root, self.skin, 1, {}))
        self.assertIsNone(self.hover_at(w, w.canvas, 270, 5))

    def test_closing_a_window_takes_its_label(self):
        w = self.show(skin_ui.SkinMainWindow(_root, self.skin, 1, {}))
        self.assertEqual(self.hover_at(w, w.canvas, 39 + 11, 88 + 9), 'Play')
        h = w.hover
        w.destroy()
        pump(100)
        self.assertIsNone(h.tip)


@unittest.skipIf(_root is None, "no display")
class LabelTableTests(unittest.TestCase):
    def test_every_main_control_has_a_label(self):
        names = set(skin_ui.RIM_MAIN) | {'time', 'heart', 'visualizer', 'volume', 'balance', 'position'}
        self.assertEqual(names - set(skin_hover.MAIN_LABELS), set())
        self.assertEqual(skin_hover.MAIN_LABELS['play'], 'Play')
        self.assertEqual(skin_hover.MAIN_LABELS['clutter_d'], 'Size: 1x / 2x / 3x')
        self.assertEqual(skin_hover.MAIN_LABELS['repeat'], 'Repeat (not available)')
        self.assertNotIn('titlebar', skin_hover.MAIN_LABELS)

    def test_every_eq_control_has_a_label(self):
        names = set(skin_ui.RIM_EQ) | {'preamp'} | {'band%d' % i for i in range(10)}
        self.assertEqual(names - set(skin_hover.EQ_LABELS), set())
        self.assertEqual([skin_hover.EQ_LABELS['band%d' % i] for i in range(10)],
                         ['60 Hz', '170 Hz', '310 Hz', '600 Hz', '1 kHz', '3 kHz', '6 kHz', '12 kHz', '14 kHz',
                          '16 kHz'])

    def test_playlist_labels(self):
        self.assertEqual(skin_hover.PL_LABELS, {'close': 'Playlist stays open', 'resize': 'Drag to resize',
                                                'search': 'Search (Enter plays the first match)'})


if __name__ == '__main__':
    unittest.main()
