"""
skin_playlist.py - the skinned playlist window (Winamp's "PL" window).

The frame (corners, tiles, title bar) is cut from the skin's pledit.bmp; inside it sit an ordinary search Entry and
Listbox colored with pledit.txt (Normal, Current, NormalBG, SelectedBG, Font), so the list scrolls and selects like any
Tk list. It imports no player code: set_rows() fills it and the callbacks dict reports what the user does:
  'play'(index)          double-click or Enter on a row
  'search'(text)         the search text changed
  'search_enter'()       Enter in the search box
  'menu'(x_root, y_root, index)   right-click (index None outside rows)
  'close'()  'moved'(x, y)
"""

import tkinter as tk

from PIL import ImageTk

WIDTH = 275
MIN_HEIGHT = 116
STEP_H = 29          # Winamp resizes the playlist in 29 px steps


class SkinPlaylistWindow(tk.Toplevel):
    def __init__(self, master, skin, scale, callbacks, height=232):
        super().__init__(master)
        self.overrideredirect(True)
        self.skin, self.scale, self.callbacks = skin, scale, callbacks
        self.height = max(MIN_HEIGHT, height)
        self.focused = False
        self._photos = []
        self._drag = None
        self._rows = []
        self._current = None
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.search_var = tk.StringVar()
        self.search_var.trace_add('write', lambda *a: self._fire('search', self.search_var.get()))
        self.entry = tk.Entry(self.canvas, textvariable=self.search_var, relief=tk.FLAT, bd=1)
        self.listbox = tk.Listbox(self.canvas, relief=tk.FLAT, bd=0, highlightthickness=0, activestyle='none',
                                  exportselection=False)
        self.entry.bind('<Return>', lambda e: self._fire('search_enter'))
        self.entry.bind('<Escape>', lambda e: self.search_var.set(''))
        self.listbox.bind('<Double-Button-1>', self._on_double)
        self.listbox.bind('<Return>', self._on_double)
        self.listbox.bind('<Button-3>', self._on_menu)
        self.canvas.bind('<Button-3>', lambda e: self._fire('menu', e.x_root, e.y_root, None))
        self.canvas.bind('<ButtonPress-1>', self._on_press)
        self.canvas.bind('<B1-Motion>', self._on_motion)
        self.canvas.bind('<ButtonRelease-1>', self._on_release)
        self.bind('<FocusIn>', lambda e: self._set_focus(True))
        self.bind('<FocusOut>', lambda e: self._set_focus(False))
        self.redraw()

    # ---- drawing -----------------------------------------------------------------

    def _fire(self, name, *args):
        cb = self.callbacks.get(name)
        if cb is not None:
            cb(*args)

    def _set_focus(self, focused):
        if focused != self.focused:
            self.focused = focused
            self.redraw()

    def destroy(self):
        # Drop the images on the Tk thread; freed later by the garbage collector on another thread they would
        # call into Tk from there.
        self._photos.clear()
        super().destroy()

    def _blit(self, img, x, y):
        photo = ImageTk.PhotoImage(self.skin.scaled(img, self.scale), master=self.canvas)
        self._photos.append(photo)
        self.canvas.create_image(x * self.scale, y * self.scale, anchor='nw', image=photo, tags=('frame',))

    def set_skin(self, skin, scale):
        self.skin, self.scale = skin, scale
        self.redraw()

    def set_height(self, height):
        self.height = max(MIN_HEIGHT, MIN_HEIGHT + round((height - MIN_HEIGHT) / STEP_H) * STEP_H)
        self.redraw()

    def redraw(self):
        s, w, h = self.scale, WIDTH, self.height
        self.canvas.config(width=w * s, height=h * s)
        self.canvas.delete('frame')
        self._photos.clear()
        sel = '_SELECTED' if self.focused else ''
        S = self.skin.sprite
        # top row: left corner, tiles, title in the middle, tiles, right corner
        self._blit(S('PLAYLIST_TOP_LEFT_SELECTED' if self.focused else 'PLAYLIST_TOP_LEFT_CORNER'), 0, 0)
        for x in range(25, w - 25, 25):
            self._blit(S('PLAYLIST_TOP_TILE' + sel), x, 0)
        self._blit(S('PLAYLIST_TITLE_BAR' + sel), (w - 100) // 2, 0)
        self._blit(S('PLAYLIST_TOP_RIGHT_CORNER' + sel), w - 25, 0)
        for y in range(20, h - 38, 29):
            self._blit(S('PLAYLIST_LEFT_TILE'), 0, y)
            self._blit(S('PLAYLIST_RIGHT_TILE'), w - 20, y)
        self._blit(S('PLAYLIST_BOTTOM_LEFT_CORNER'), 0, h - 38)
        self._blit(S('PLAYLIST_BOTTOM_RIGHT_CORNER'), w - 150, h - 38)
        # inside: the search box on top, the list below, in the skin's playlist colors
        c = self.skin.pledit
        size = 8 * s
        font = (c.get('font') or 'Arial', -size)
        for wdg in (self.entry, self.listbox):
            wdg.config(bg=c['normalbg'], fg=c['normal'], font=font)
        self.entry.config(insertbackground=c['current'], selectbackground=c['selectedbg'],
                          selectforeground=c['current'])
        self.listbox.config(selectbackground=c['selectedbg'], selectforeground=c['current'])
        inner_x, inner_y, inner_w = 12 * s, 20 * s, (w - 32) * s
        entry_h = (size + 6)
        self.canvas.delete('inner')
        self.canvas.create_window(inner_x, inner_y, anchor='nw', window=self.entry, width=inner_w, height=entry_h,
                                  tags=('inner',))
        self.canvas.create_window(inner_x, inner_y + entry_h + 2, anchor='nw', window=self.listbox, width=inner_w,
                                  height=(h - 58) * s - entry_h - 2, tags=('inner',))
        self._paint_rows()

    # ---- rows ----------------------------------------------------------------------

    def set_rows(self, rows, current=None):
        """rows: display strings; current: the index of the playing song (shown in the Current color)."""
        if rows != self._rows:
            top = self.listbox.yview()[0] if self._rows else 0.0
            self._rows = list(rows)
            self.listbox.delete(0, tk.END)
            if rows:
                self.listbox.insert(tk.END, *rows)
            self.listbox.yview_moveto(top)
            self._current = None
        if current != self._current:
            self._current = current
            self._paint_rows()

    def _paint_rows(self):
        c = self.skin.pledit
        n = self.listbox.size()
        if self._current is not None and 0 <= self._current < n:
            self.listbox.itemconfig(self._current, fg=c['current'])
        for i in range(n):   # cheap enough for a playlist; keeps old "current" rows from staying lit
            if i != self._current:
                self.listbox.itemconfig(i, fg=c['normal'])

    def see_current(self):
        if self._current is not None and 0 <= self._current < self.listbox.size():
            self.listbox.see(self._current)

    def _on_double(self, event):
        sel = self.listbox.curselection()
        if sel:
            self._fire('play', sel[0])
        return 'break'

    def _on_menu(self, event):
        i = self.listbox.nearest(event.y)
        if 0 <= i < self.listbox.size():
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(i)
            self._fire('menu', event.x_root, event.y_root, i)
        return 'break'

    # ---- moving, closing, resizing ---------------------------------------------------

    def _hit(self, event):
        x, y = event.x / self.scale, event.y / self.scale
        if y < 20:
            return 'close' if x >= WIDTH - 12 else 'titlebar'
        if y >= self.height - 38 and x >= WIDTH - 20:
            return 'resize'
        return None

    def _on_press(self, event):
        kind = self._hit(event)
        if kind == 'titlebar':
            self._drag = ('move', event.x_root - self.winfo_x(), event.y_root - self.winfo_y())
        elif kind == 'resize':
            self._drag = ('resize', event.y_root, self.height)
        elif kind == 'close':
            self._drag = ('close',)

    def _on_motion(self, event):
        if not self._drag:
            return
        if self._drag[0] == 'move':
            self.geometry(f'+{event.x_root - self._drag[1]}+{event.y_root - self._drag[2]}')
        elif self._drag[0] == 'resize':
            self.set_height(self._drag[2] + (event.y_root - self._drag[1]) / self.scale)

    def _on_release(self, event):
        drag, self._drag = self._drag, None
        if not drag:
            return
        if drag[0] == 'move':
            self._fire('moved', self.winfo_x(), self.winfo_y())
        elif drag[0] == 'resize':
            self._fire('resized', self.height)
        elif drag[0] == 'close' and self._hit(event) == 'close':
            self._fire('close')
