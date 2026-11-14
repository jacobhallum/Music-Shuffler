"""
stats_ui.py - the Listening Stats window.

Shows what listening_stats.py counted: a period picker, a summary line, and four tabs (Top, Skips, Over time,
Library). It never counts anything itself. The player passes:
  - on_period(period): called with 'week' | 'month' | 'year' | 'all' or a (year, month) tuple when the user picks one;
    the player computes off the main thread and calls show() back on it.
  - menu_items(kind, key): the right-click items for a row, as [(label, callable)] ('song' -> path,
    'album' -> album key, 'artist' -> artist key). An empty list means no menu.
  - theme: the player's theme dict (bg, fg, frame_bg, button_bg, secondary_fg) for the canvas bars.
"""

import time
import tkinter as tk
from tkinter import ttk

PERIOD_LABELS = (('week', 'This week'), ('month', 'This month'), ('year', 'This year'), ('all', 'All time'))
WEEKDAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
MONTH_NAMES = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
               'November', 'December')


def fmt_hours(h):
    """'2.4 h' from an hour, '35 min' below one hour, '0 min' for nothing."""
    h = float(h or 0)
    if h >= 1:
        return f"{h:.1f} h"
    return f"{round(h * 60)} min"


def fmt_summary(s):
    share = '–' if s.shuffle_share is None else f"{round(s.shuffle_share * 100)}%"
    return (f"{fmt_hours(s.hours)} · {s.plays} plays · {s.songs} songs · {s.artists} artists · "
            f"{s.new_songs} new · {share} shuffle")


def fmt_date(ts):
    return time.strftime('%Y-%m-%d', time.localtime(ts)) if ts else '–'


def month_label(year, month):
    return f"{MONTH_NAMES[month - 1]} {year}"


def period_title(period):
    """'This week' / 'All time' / 'November 2026' for a period ('week'... or a (year, month) tuple)."""
    if isinstance(period, tuple):
        return month_label(*period)
    return dict(PERIOD_LABELS)[period]


class StatsWindow(tk.Toplevel):
    def __init__(self, master, on_period, menu_items, theme):
        super().__init__(master)
        self.title("Listening Stats")
        self.geometry("980x620")
        self.minsize(720, 460)
        self.configure(bg=theme['bg'])
        self._on_period = on_period
        self._menu_items = menu_items
        self._theme = theme
        self._rows = {}          # (tree, iid) -> (kind, key)
        self._months = {}        # months tree iid -> (year, month)
        self._clock = None       # (by_hour, by_weekday) last shown, redrawn on resize
        self._menu = tk.Menu(self, tearoff=0)

        top = ttk.Frame(self, padding=(10, 8))
        top.pack(fill=tk.X)
        self.period_var = tk.StringVar(value='week')
        for kind, text in PERIOD_LABELS:
            ttk.Radiobutton(top, text=text, value=kind, variable=self.period_var,
                            command=lambda k=kind: self._on_period(k)).pack(side=tk.LEFT, padx=(0, 10))
        self.title_label = ttk.Label(top, text='', font=('Arial', 11, 'bold'))
        self.title_label.pack(side=tk.LEFT, padx=(14, 0))
        self.summary_label = ttk.Label(self, text='Loading…', padding=(10, 0, 10, 6))
        self.summary_label.pack(fill=tk.X)
        self.note_label = ttk.Label(self, text='', padding=(10, 0, 10, 4), foreground=theme['secondary_fg'])
        self.note_label.pack(fill=tk.X)

        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.notebook = nb

        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text='Top')
        self.artists_tree = self._tree(tab, 0, 'Top artists', (('name', 'Artist', 160), ('hours', 'Time', 60),
                                                               ('plays', 'Plays', 50)))
        self.songs_tree = self._tree(tab, 1, 'Top songs', (('name', 'Song', 170), ('sub', 'Artist', 110),
                                                           ('plays', 'Plays', 50), ('hours', 'Time', 60)))
        self.albums_tree = self._tree(tab, 2, 'Top albums', (('name', 'Album', 160), ('sub', 'Artist', 110),
                                                             ('hours', 'Time', 60)))

        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text='Skips')
        self.skipped_songs_tree = self._tree(tab, 0, 'Most skipped songs', (('name', 'Song', 220),
                                             ('sub', 'Artist', 140), ('rate', 'Skipped', 110)))
        self.skipped_artists_tree = self._tree(tab, 1, 'Most skipped artists', (('name', 'Artist', 200),
                                               ('rate', 'Skipped', 110)))

        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text='Over time')
        tab.columnconfigure(0, weight=3)
        tab.columnconfigure(1, weight=2)
        tab.rowconfigure(0, weight=1)
        self.months_tree = self._tree(tab, 0, 'Months (double-click to open one)',
                                      (('month', 'Month', 120), ('hours', 'Time', 60), ('plays', 'Plays', 50),
                                       ('artist', 'Top artist', 130), ('song', 'Top song', 150)))
        self.months_tree.bind('<Double-1>', lambda e: self._pick_month(self.months_tree.identify_row(e.y)))
        clock = ttk.LabelFrame(tab, text='When you listen', padding=4)
        clock.grid(row=0, column=1, sticky='nsew', padx=(6, 0))
        self.clock_canvas = tk.Canvas(clock, bg=theme['frame_bg'], highlightthickness=0, height=260)
        self.clock_canvas.pack(fill=tk.BOTH, expand=True)
        self.clock_canvas.bind('<Configure>', lambda e: self._draw_clock())

        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text='Library')
        self.favorites_tree = self._tree(tab, 0, 'Forgotten favorites (hearted, not played in 90+ days)',
                                         (('name', 'Song', 200), ('sub', 'Artist', 130), ('when', 'Last played', 90)))
        self.never_frame_label = tk.StringVar(value='Never played')
        self.never_tree = self._tree(tab, 1, self.never_frame_label, (('name', 'Song', 200), ('sub', 'Artist', 130),
                                                                      ('when', 'Added', 90)))

        self.protocol('WM_DELETE_WINDOW', self.destroy)

    # ---- building ------------------------------------------------------------

    def _tree(self, parent, column, title, cols):
        parent.columnconfigure(column, weight=1)
        parent.rowconfigure(0, weight=1)
        frame = ttk.LabelFrame(parent, padding=4)
        if isinstance(title, tk.StringVar):
            label = ttk.Label(frame, textvariable=title)
            frame.configure(labelwidget=label)
        else:
            frame.configure(text=title)
        frame.grid(row=0, column=column, sticky='nsew', padx=(0 if column == 0 else 6, 0))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=[c[0] for c in cols], show='headings', selectmode='browse')
        for name, heading, width in cols:
            numeric = name in ('hours', 'plays', 'rate', 'when')
            tree.heading(name, text=heading)
            tree.column(name, width=width, stretch=not numeric, anchor=tk.E if numeric else tk.W)
        bar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=bar.set)
        tree.grid(row=0, column=0, sticky='nsew')
        bar.grid(row=0, column=1, sticky='ns')
        tree.bind('<Button-3>', lambda e, t=tree: self._popup(t, e))
        return tree

    def _fill(self, tree, rows):
        """rows: [(kind, key, values)]; kind None = no menu."""
        tree.delete(*tree.get_children())
        for k in [k for k in self._rows if k[0] is tree]:
            del self._rows[k]
        for kind, key, values in rows:
            iid = tree.insert('', tk.END, values=values)
            if kind is not None:
                self._rows[(tree, iid)] = (kind, key)

    # ---- showing ---------------------------------------------------------------

    def show_loading(self, label):
        self.title_label.config(text=label)
        self.summary_label.config(text='Loading…')

    def show_error(self, text):
        self.summary_label.config(text=text)

    def show(self, label, stats, months, favorites, never, all_time):
        self.title_label.config(text=label)
        self.summary_label.config(text=fmt_summary(stats.summary))
        self.note_label.config(text=("All time: the top lists and skips include iTunes history and every start in "
                                     "this player; the summary and the clock come from the listening log.")
                               if all_time else '')
        self._fill(self.artists_tree, [('artist', r.key, (r.name, fmt_hours(r.hours), r.plays))
                                       for r in stats.top_artists])
        self._fill(self.songs_tree, [('song', r.key, (r.name, r.sub, r.plays, fmt_hours(r.hours)))
                                     for r in stats.top_songs])
        self._fill(self.albums_tree, [('album', r.key, (r.name, r.sub, fmt_hours(r.hours)))
                                      for r in stats.top_albums])
        self._fill(self.skipped_songs_tree, [('song', r.key, (r.name, r.sub, f"{r.skips} of {r.starts}"))
                                             for r in stats.skipped_songs])
        self._fill(self.skipped_artists_tree, [('artist', r.key, (r.name, f"{r.skips} of {r.starts}"))
                                               for r in stats.skipped_artists])
        self.months_tree.delete(*self.months_tree.get_children())
        self._months.clear()
        for m in months:
            iid = self.months_tree.insert('', tk.END, values=(month_label(m.year, m.month), fmt_hours(m.hours),
                                                              m.plays, m.top_artist, m.top_song))
            self._months[iid] = (m.year, m.month)
        self._fill(self.favorites_tree, [('song', t.path, (t.title, t.place.artist_name, fmt_date(t.last_played)))
                                         for t in favorites])
        self.never_frame_label.set(f"Never played ({len(never)} songs)")
        self._fill(self.never_tree, [('song', t.path, (t.title, t.place.artist_name, fmt_date(t.date_added)))
                                     for t in never])
        self._clock = (list(stats.by_hour), list(stats.by_weekday))
        self._draw_clock()

    def _draw_clock(self):
        c = self.clock_canvas
        c.delete('all')
        if self._clock is None:
            return
        by_hour, by_weekday = self._clock
        w = max(c.winfo_width(), 200)
        h = max(c.winfo_height(), 200)
        fg, sub = self._theme['fg'], self._theme['secondary_fg']
        half = h // 2

        def bars(values, top, height, labels):
            peak = max(values) if values and max(values) > 0 else 1
            n = len(values)
            slot = (w - 20) / n
            for i, v in enumerate(values):
                x0 = 10 + i * slot + 1
                bh = (height - 34) * (v / peak)
                c.create_rectangle(x0, top + height - 18 - bh, x0 + slot - 2, top + height - 18,
                                   fill=fg, outline='')
                if labels[i]:
                    c.create_text(x0 + (slot - 2) / 2, top + height - 8, text=labels[i], fill=sub,
                                  font=('Arial', 7))

        c.create_text(10, 8, text='By hour of day', anchor='nw', fill=sub, font=('Arial', 8))
        bars(by_hour, 6, half, [str(i) if i % 3 == 0 else '' for i in range(24)])
        c.create_text(10, half + 8, text='By weekday', anchor='nw', fill=sub, font=('Arial', 8))
        bars(by_weekday, half + 6, half - 6, list(WEEKDAYS))

    # ---- interaction -----------------------------------------------------------

    def _pick_month(self, iid):
        ym = self._months.get(iid) if iid else None
        if ym is not None:
            self.period_var.set('')
            self._on_period(ym)

    def _items_for(self, tree, iid):
        kind_key = self._rows.get((tree, iid)) if iid else None
        return self._menu_items(*kind_key) if kind_key else []

    def _popup(self, tree, event):
        iid = tree.identify_row(event.y)
        items = self._items_for(tree, iid)
        if not items:
            return
        tree.selection_set(iid)
        self._menu.delete(0, tk.END)
        for label, command in items:
            if label is None:
                self._menu.add_separator()
            else:
                self._menu.add_command(label=label, command=command)
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self._menu.grab_release()
            except tk.TclError:
                pass
