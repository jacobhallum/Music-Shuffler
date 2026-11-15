"""
skin_hover.py - hover labels on the skinned windows: resting the mouse on a control for HOVER_MS shows a small
borderless label saying what it does.

The windows call point(...) with "which control is under the pointer"; this module imports no player code.
"""

import tkinter as tk

HOVER_MS = 500
OFFSET = (12, 18)
FONT = ('Segoe UI', 9)

MAIN_LABELS = {
    'menu': 'Menu',
    'minimize': 'Minimize',
    'shade': 'Compact mode (not available)',
    'close': 'Exit',
    'previous': 'Previous',
    'play': 'Play',
    'pause': 'Pause',
    'stop': 'Stop',
    'next': 'Next',
    'eject': 'Albums',
    'shuffle': 'Shuffle',
    'repeat': 'Repeat (not available)',
    'eq_button': 'Show/hide equalizer',
    'pl_button': 'Playlist (always open)',
    'clutter_o': 'Menu',
    'clutter_a': 'Always on top',
    'clutter_i': 'Song info',
    'clutter_d': 'Size: 1x / 2x / 3x',
    'clutter_v': 'Visualizer: spectrum / scope / off',
    'visualizer': 'Visualizer: spectrum / scope / off',
    'time': 'Elapsed / remaining',
    'heart': 'Favorite',
    'volume': 'Volume',
    'balance': 'Balance (not available)',
    'position': 'Seek',
}

EQ_LABELS = {
    'on': 'Equalizer on/off',
    'auto': 'Auto (not available)',
    'presets': 'Presets',
    'close': 'Close equalizer',
    'shade': 'Compact mode (not available)',
    'preamp': 'Preamp',
}
for _i, _hz in enumerate(['60 Hz', '170 Hz', '310 Hz', '600 Hz', '1 kHz', '3 kHz', '6 kHz', '12 kHz', '14 kHz',
                          '16 kHz']):
    EQ_LABELS['band%d' % _i] = _hz

PL_LABELS = {'close': 'Playlist stays open', 'resize': 'Drag to resize',
             'search': 'Search (Enter plays the first match)'}


def _areas(win):
    try:
        from skin_mode import work_areas
        return work_areas(win)
    except Exception:
        return [(0, 0, win.winfo_screenwidth(), win.winfo_screenheight())]


class HoverLabel:
    def __init__(self, owner, colors):
        self.owner = owner
        self.colors = colors
        self.tip = None
        self._job = None
        self._key = None
        self._text = None
        self._pos = (0, 0)
        self._blocked = None

    def point(self, key, text, x_root, y_root):
        if key is None or text is None:
            self._blocked = None
            self.hide()
            return
        if key == self._key:
            self._pos = (x_root, y_root)
            return
        if key == self._blocked:
            return
        self.hide()
        self._blocked = None
        self._key = key
        self._text = text
        self._pos = (x_root, y_root)
        self._job = self.owner.after(HOVER_MS, self._show)

    def press(self):
        blocked = self._key
        self.hide()
        self._blocked = blocked

    def hide(self):
        if self._job is not None:
            try:
                self.owner.after_cancel(self._job)
            except tk.TclError:
                pass
        self._job = None
        if self.tip is not None:
            try:
                self.tip.destroy()
            except tk.TclError:
                pass
        self.tip = None
        self._key = None

    def destroy(self):
        self.hide()
        self._blocked = None

    def _show(self):
        self._job = None
        try:
            if not self.owner.winfo_exists():
                return
            tip = tk.Toplevel(self.owner, bg=self.colors['current'])
            tip.withdraw()                 # measured and placed while hidden: no flash at the screen's corner
            self.tip = tip                 # set now so hide() can always find it
            tip.overrideredirect(True)
            tip.attributes('-topmost', True)
            tk.Label(tip, text=self._text, bg=self.colors['normalbg'], fg=self.colors['normal'], font=FONT,
                     padx=4, pady=1).pack(padx=1, pady=1)
            tip.update_idletasks()
            w, h = tip.winfo_reqwidth(), tip.winfo_reqheight()
            px, py = self._pos
            x, y = px + OFFSET[0], py + OFFSET[1]
            areas = _areas(self.owner)
            area = next((a for a in areas if a[0] <= px < a[0] + a[2] and a[1] <= py < a[1] + a[3]), areas[0])
            ax, ay, aw, ah = area
            x = max(ax, min(x, ax + aw - w))
            if y + h > ay + ah:
                y = py - h - 4
            y = max(ay, min(y, ay + ah - h))
            tip.geometry(f'+{x}+{y}')
            tip.deiconify()
        except tk.TclError:
            return


def watch(hover, widget, key_at):
    def on_motion(event):
        found = key_at(event)
        if found is None:
            hover.point(None, None, event.x_root, event.y_root)
        else:
            key, text = found
            hover.point(key, text, event.x_root, event.y_root)

    widget.bind('<Motion>', on_motion, add='+')
    widget.bind('<Leave>', lambda event: hover.point(None, None, 0, 0), add='+')
    # one binding per button: Tk runs only the most specific match, so a window's own <ButtonPress-1> handler
    # would shadow a generic <ButtonPress>
    for n in (1, 2, 3):
        widget.bind(f'<ButtonPress-{n}>', lambda event: hover.press(), add='+')
