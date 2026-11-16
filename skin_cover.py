"""
skin_cover.py - the album-art window: the playlist frame with one picture inside.

It is the playlist window minus the search box, the song list, the resize grip, the "PLAYLIST" title text and the
bottom buttons: a plain frame holding a Label that shows the current song's cover. It imports no player code:
set_picture() shows the cover and the callbacks dict reports what the user does:
  'menu'(x_root, y_root, None)   right-click (on the picture or the frame)
  'close'()  'moved'(x, y)
  'drag'(x, y)          the title bar is dragged so the window would sit at (x, y) (the controller moves the stack)
"""

import tkinter as tk

from PIL import ImageTk

from skin_playlist import WIDTH, SkinPlaylistWindow

COVER_HEIGHT = 232    # main + EQ (116 + 116), so the cover's bottom lines up with EQ's
INSIDE_W, INSIDE_H = WIDTH - 32, COVER_HEIGHT - 58    # 243 x 174 inside the frame (12/20 sides, 20/38 top/bottom)

COVER_LABELS = {'close': 'Hide album art'}


class SkinCoverWindow(SkinPlaylistWindow):
    HOVER_LABELS = COVER_LABELS

    def __init__(self, master, skin, scale, callbacks):
        self.art = None
        self._art_photo = None
        super().__init__(master, skin, scale, callbacks, height=COVER_HEIGHT)
        self.entry.destroy()
        self.listbox.destroy()
        self.art = tk.Label(self.canvas, bd=0, highlightthickness=0)
        self.art.bind('<Button-3>', lambda e: self._fire('menu', e.x_root, e.y_root, None))
        self.redraw()

    def redraw(self):
        s, w, h = self.scale, WIDTH, self.height
        self.canvas.config(width=w * s, height=h * s)
        self.canvas.delete('frame')
        self._photos.clear()
        sel = '_SELECTED' if self.focused else ''
        S = self.skin.sprite
        # top row: left corner, tiles, right corner (no PLAYLIST_TITLE_BAR)
        self._blit(S('PLAYLIST_TOP_LEFT_SELECTED' if self.focused else 'PLAYLIST_TOP_LEFT_CORNER'), 0, 0)
        for x in range(25, w - 25, 25):
            self._blit(S('PLAYLIST_TOP_TILE' + sel), x, 0)
        self._blit(S('PLAYLIST_TOP_RIGHT_CORNER' + sel), w - 25, 0)
        for y in range(20, h - 38, 29):
            self._blit(S('PLAYLIST_LEFT_TILE'), 0, y)
            self._blit(S('PLAYLIST_RIGHT_TILE'), w - 20, y)
        self._blit(S('PLAYLIST_BOTTOM_LEFT_CORNER'), 0, h - 38)
        self._blit(S('PLAYLIST_BOTTOM_RIGHT_CORNER'), w - 150, h - 38)
        for x in list(range(12, 230, 25)) + [230]:
            self._blit(S('PLAYLIST_BOTTOM_TILE'), x, h - 38)
        self.canvas.delete('inner')
        if self.art is not None:
            self.art.config(bg=self.skin.pledit['normalbg'])
            self.canvas.create_window(12 * s, 20 * s, anchor='nw', window=self.art,
                                      width=INSIDE_W * s, height=INSIDE_H * s, tags=('inner',))

    def _hit(self, event):
        kind = super()._hit(event)
        return None if kind == 'resize' else kind

    def set_height(self, height):
        """The cover never resizes."""

    def set_picture(self, image):
        """Show image (an RGB PIL image, cover_art.ART*scale square), centered in the frame."""
        self._art_photo = ImageTk.PhotoImage(image, master=self.canvas)
        self.art.config(image=self._art_photo)

    def destroy(self):
        if self.art is not None:
            try:
                self.art.config(image='')
            except tk.TclError:
                pass
        self._art_photo = None
        super().destroy()
