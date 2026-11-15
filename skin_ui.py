"""
skin_ui.py - the borderless skinned windows: main player and equalizer.

SkinWindow draws a classic Winamp skin onto a borderless canvas and reports
what the user does through the `callbacks` dict; the player pushes state in
with update_state(...). The windows import no player code.
"""

import tkinter as tk

from PIL import Image, ImageDraw, ImageTk

from skin_layout import (MAIN_LAYOUT, EQ_LAYOUT, hit_test, slider_value, slider_thumb_x,
                         volume_frame, balance_frame, eq_frame, eq_thumb_y, time_digits,
                         text_cells, marquee_slice, TIME_DIGIT_X, MINUS_SIGN)
from skin_sprites import SPRITES

MARQUEE_CHARS = 31
MARQUEE_MS = 200

MAIN_BUTTONS = ('menu', 'minimize', 'shade', 'close', 'previous', 'play', 'pause',
                'stop', 'next', 'eject', 'shuffle', 'repeat', 'eq_button', 'pl_button')
MAIN_CLICKS = ('time', 'heart', 'visualizer', 'clutter_o', 'clutter_a', 'clutter_i',
               'clutter_d', 'clutter_v')
EQ_SLIDERS = ('preamp',) + tuple('band%d' % i for i in range(10))
# A soft rim keeps every button visible on any skin (Jacob 2026-11-15: "not super bright but enough to see them")
RIM_MAIN = MAIN_BUTTONS + ('clutter_o', 'clutter_a', 'clutter_i', 'clutter_d', 'clutter_v')
RIM_EQ = ('on', 'auto', 'presets', 'close', 'shade')
RIM_LIGHT, RIM_DARK = (255, 255, 255, 110), (0, 0, 0, 120)
HEART_POINTS = ((4, 7), (0, 3), (0, 1), (1, 0), (3, 0), (4, 1),
                (5, 0), (7, 0), (8, 1), (8, 3))


def hex_rgb(h):
    h = str(h).lstrip('#')
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def frame_crop(skin, sheet, x, y, w, h, step, index):
    size = skin.sheet_size(sheet)
    if size is not None:
        last = (size[1] - y - h) // step
        if last >= 0:
            index = min(index, last)
    return skin.crop(sheet, x, y + index * step, w, h)


def text_image(skin, text):
    img = Image.new('RGBA', (5 * len(text), 6), (0, 0, 0, 0))
    for i, (row, col) in enumerate(text_cells(text)):
        img.paste(skin.crop('text.bmp', col * 5, row * 6, 5, 6), (5 * i, 0))
    return img


def heart_image(color_hex, filled):
    img = Image.new('RGBA', (9, 8), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = hex_rgb(color_hex)
    if filled:
        draw.polygon(HEART_POINTS, fill=color)
    else:
        draw.line(HEART_POINTS + (HEART_POINTS[0],), fill=color)
    return img


def rim_overlay(background, layout, names, band=3):
    """An RGBA layer the size of `background` with a 1 px outline on the edge of each named element's rect: light
    where the skin around it (a `band` px ring in `background`) is dark, dark where it is light."""
    gray = background.convert('L')
    width, height = gray.size
    out = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(out)
    for name in names:
        if name not in layout:
            continue
        x, y, w, h = layout[name]
        outer = gray.crop((max(0, x - band), max(0, y - band), min(width, x + w + band), min(height, y + h + band)))
        inner = gray.crop((x, y, min(width, x + w), min(height, y + h)))
        ring_px = outer.width * outer.height - inner.width * inner.height
        ring = (sum(outer.getdata()) - sum(inner.getdata())) / max(1, ring_px)
        draw.rectangle((x, y, x + w - 1, y + h - 1), outline=RIM_LIGHT if ring < 128 else RIM_DARK)
    return out


class SkinWindow(tk.Toplevel):
    def __init__(self, master, skin, scale, width, height, callbacks):
        super().__init__(master)
        self.overrideredirect(True)
        self.skin = skin
        self.scale = scale
        self.callbacks = callbacks or {}
        self.width = width
        self.height = height
        self.focused = False
        self._photos = []
        self._tag_photos = {}
        self._rims = None               # (key, rim layer, its PhotoImage): rebuilt only when skin/size/focus change
        self.canvas = tk.Canvas(self, width=width * scale, height=height * scale,
                                highlightthickness=0, bd=0)
        self.canvas.pack()
        self.bind('<FocusIn>', self._on_focus_in)
        self.bind('<FocusOut>', self._on_focus_out)
        self.bind('<Button-3>', self._on_right_click)

    def blit(self, img, x, y, tag=None):
        photo = ImageTk.PhotoImage(self.skin.scaled(img, self.scale), master=self.canvas)
        self._photos.append(photo)
        if tag is not None:
            self._tag_photos.setdefault(tag, []).append(photo)
        if tag is None:
            self.canvas.create_image(x * self.scale, y * self.scale, anchor='nw', image=photo)
        else:
            self.canvas.create_image(x * self.scale, y * self.scale, anchor='nw',
                                     image=photo, tags=(tag,))

    def redraw(self):
        self.canvas.delete('all')
        self._photos.clear()
        self._tag_photos.clear()
        self.draw()

    def _blit_rims(self, layout, names, under):
        """Draw the soft contrast rim layer on top. under: the (sprite, x, y) pieces drawn below the buttons
        (background, title bar, ...), composited to read what the skin really shows around each button."""
        key = (self.skin, self.scale, self.focused)
        if self._rims is None or self._rims[0] != key:
            base = Image.new('RGBA', (self.width, self.height), (0, 0, 0, 255))
            for name, x, y in under:
                base.alpha_composite(self.skin.sprite(name).convert('RGBA'), (x, y))
            layer = rim_overlay(base, layout, names)
            photo = ImageTk.PhotoImage(self.skin.scaled(layer, self.scale), master=self.canvas)
            self._rims = (key, layer, photo)
        self.canvas.create_image(0, 0, anchor='nw', image=self._rims[2], tags=('rims',))

    def _redraw_tag(self, tag, blit_fn):
        self.canvas.delete(tag)
        stale = self._tag_photos.pop(tag, [])
        if stale:
            self._photos = [p for p in self._photos if p not in stale]
        blit_fn()

    def set_skin(self, skin, scale):
        self.skin = skin
        self.scale = scale
        self.canvas.config(width=self.width * scale, height=self.height * scale)
        self.redraw()

    def fire(self, name, *args):
        cb = self.callbacks.get(name)
        if cb is not None:
            cb(*args)

    def destroy(self):
        # Drop the images here, on the Tk thread. Left to the garbage collector they could be freed on another
        # thread, and freeing a PhotoImage calls into Tk.
        self._photos.clear()
        self._tag_photos.clear()
        self._rims = None
        super().destroy()

    def _on_focus_in(self, event=None):
        self.focused = True
        self.redraw()

    def _on_focus_out(self, event=None):
        self.focused = False
        self.redraw()

    def _on_right_click(self, event):
        self.fire('menu', event.x_root, event.y_root)

    def draw(self):
        raise NotImplementedError


class SkinMainWindow(SkinWindow):
    def __init__(self, master, skin, scale, callbacks):
        super().__init__(master, skin, scale, 275, 116, callbacks)
        self.state = {
            'play_state': 'stopped', 'elapsed': 0, 'duration': 0, 'title': '',
            'kbps': '', 'khz': '', 'stereo': None, 'volume': 50, 'position': 0.0,
            'shuffle': False, 'repeat': False, 'eq_open': False, 'pl_open': False,
            'favorite': False, 'always_on_top': False, 'double_size': False,
        }
        self.show_remaining = False
        self.marquee_offset = 0
        self.flash_text = None
        self.pressed = None
        self.press_el = None
        self.drag = None
        self.vis = None
        self._flash_id = None
        self._marquee_id = None
        self.canvas.bind('<ButtonPress-1>', self._on_press)
        self.canvas.bind('<B1-Motion>', self._on_motion)
        self.canvas.bind('<ButtonRelease-1>', self._on_release)
        self._marquee_id = self.after(MARQUEE_MS, self._tick_marquee)
        self.redraw()

    def update_state(self, **kw):
        self.state.update(kw)
        self.redraw()

    def flash(self, text, ms=3000):
        if self._flash_id is not None:
            try:
                self.after_cancel(self._flash_id)
            except Exception:
                pass
            self._flash_id = None
        self.flash_text = text
        self.marquee_offset = 0
        self.redraw()
        self._flash_id = self.after(ms, self._end_flash)

    def _end_flash(self):
        self._flash_id = None
        self.flash_text = None
        self.marquee_offset = 0
        self.redraw()

    def set_vis(self, data):
        self.vis = data
        self._redraw_tag('vis', lambda: self.blit(self._vis_image(), 24, 43, tag='vis'))

    def destroy(self):
        for timer in (self._marquee_id, self._flash_id):
            if timer is not None:
                try:
                    self.after_cancel(timer)
                except Exception:
                    pass
        self._marquee_id = None
        self._flash_id = None
        super().destroy()

    def _tick_marquee(self):
        self._marquee_id = None
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        text = self.flash_text or self.state['title'] or ''
        if len(text) > MARQUEE_CHARS:
            self.marquee_offset += 1
            offset = self.marquee_offset
            self._redraw_tag('marquee', lambda: self.blit(
                text_image(self.skin, marquee_slice(text, offset, MARQUEE_CHARS)),
                111, 27, tag='marquee'))
        self._marquee_id = self.after(MARQUEE_MS, self._tick_marquee)

    def _vis_image(self):
        img = Image.new('RGBA', (76, 16), tuple(self.skin.viscolors[0]))
        vis = self.vis
        if isinstance(vis, tuple) and len(vis) == 2 and vis[0] == 'scope':
            color = tuple(self.skin.viscolors[18])
            for x, y in enumerate(vis[1]):
                y = int(y)
                if 0 <= y < 16:
                    img.putpixel((x, y), color)
        elif isinstance(vis, (list, tuple)):
            for i, height in enumerate(vis):
                height = min(max(int(height), 0), 16)
                for row in range(16 - height, 16):
                    color = tuple(self.skin.viscolors[2 + row])
                    for x in (4 * i, 4 * i + 1, 4 * i + 2):
                        if x < 76:
                            img.putpixel((x, row), color)
        return img

    def draw(self):
        skin = self.skin
        st = self.state
        S = skin.sprite

        self.blit(S('MAIN_WINDOW_BACKGROUND'), 0, 0)
        self.blit(S('MAIN_TITLE_BAR_SELECTED' if self.focused else 'MAIN_TITLE_BAR'), 0, 0)

        for el, base in (('menu', 'MAIN_OPTIONS_BUTTON'), ('minimize', 'MAIN_MINIMIZE_BUTTON'),
                         ('shade', 'MAIN_SHADE_BUTTON'), ('close', 'MAIN_CLOSE_BUTTON')):
            x, y, _w, _h = MAIN_LAYOUT[el]
            self.blit(S((base + '_DEPRESSED') if self.pressed == el else base), x, y)

        self.blit(S('MAIN_CLUTTER_BAR_BACKGROUND'), 10, 22)
        if st['always_on_top']:
            x, y, _w, _h = MAIN_LAYOUT['clutter_a']
            self.blit(S('MAIN_CLUTTER_BAR_BUTTON_A_SELECTED'), x, y)
        if st['double_size']:
            x, y, _w, _h = MAIN_LAYOUT['clutter_d']
            self.blit(S('MAIN_CLUTTER_BAR_BUTTON_D_SELECTED'), x, y)

        indicator = {'playing': 'MAIN_PLAYING_INDICATOR',
                     'paused': 'MAIN_PAUSED_INDICATOR'}.get(st['play_state'],
                                                            'MAIN_STOPPED_INDICATOR')
        x, y, _w, _h = MAIN_LAYOUT['playstate']
        self.blit(S(indicator), x, y)

        if st['play_state'] != 'stopped':
            seconds = st['duration'] - st['elapsed'] if self.show_remaining else st['elapsed']
            for i, d in enumerate(time_digits(seconds)):
                sx, sy, w, h = SPRITES['numbers.bmp']['DIGIT_%d' % d]
                self.blit(skin.crop('numbers.bmp', sx, sy, w, h), TIME_DIGIT_X[i], 26)
            if self.show_remaining:
                self.blit(skin.crop('numbers.bmp', *MINUS_SIGN), MINUS_SIGN[0], MINUS_SIGN[1])

        x, y, _w, _h = MAIN_LAYOUT['heart']
        color = skin.pledit.get('current' if st['favorite'] else 'normal', '#00FF00')
        self.blit(heart_image(color, st['favorite']), x, y)

        text = self.flash_text or st['title']
        self.blit(text_image(skin, marquee_slice(text, self.marquee_offset, MARQUEE_CHARS)),
                  111, 27, tag='marquee')

        self.blit(text_image(skin, str(st['kbps'] or '').rjust(3)), 111, 43)
        self.blit(text_image(skin, str(st['khz'] or '').rjust(2)), 156, 43)

        x, y, _w, _h = MAIN_LAYOUT['mono']
        self.blit(S('MAIN_MONO_SELECTED' if st['stereo'] is False else 'MAIN_MONO'), x, y)
        x, y, _w, _h = MAIN_LAYOUT['stereo']
        self.blit(S('MAIN_STEREO_SELECTED' if st['stereo'] is True else 'MAIN_STEREO'), x, y)

        self.blit(frame_crop(skin, 'volume.bmp', 0, 0, 68, 13, 15,
                             volume_frame(st['volume'])), 107, 57)
        thumb = 'MAIN_VOLUME_THUMB_SELECTED' if self._drag_is('volume') else 'MAIN_VOLUME_THUMB'
        self.blit(S(thumb), slider_thumb_x(st['volume'], 107, 68, 14, 0, 100), 58)

        b = self.drag[1] if self._drag_is('balance') else 0
        self.blit(frame_crop(skin, 'balance.bmp', 9, 0, 38, 13, 15,
                             balance_frame(b)), 177, 57)
        self.blit(S('MAIN_BALANCE_THUMB'), slider_thumb_x(b, 177, 38, 14, -100, 100), 58)

        x, y, _w, _h = MAIN_LAYOUT['eq_button']
        self.blit(S('MAIN_EQ_BUTTON_SELECTED' if st['eq_open'] else 'MAIN_EQ_BUTTON'), x, y)
        x, y, _w, _h = MAIN_LAYOUT['pl_button']
        self.blit(S('MAIN_PLAYLIST_BUTTON_SELECTED' if st['pl_open'] else 'MAIN_PLAYLIST_BUTTON'),
                  x, y)

        self.blit(S('MAIN_POSITION_SLIDER_BACKGROUND'), 16, 72)
        if st['duration'] > 0:
            dragging = self._drag_is('position')
            pos = self.drag[1] if dragging else st['position']
            thumb = 'MAIN_POSITION_SLIDER_THUMB_SELECTED' if dragging else 'MAIN_POSITION_SLIDER_THUMB'
            self.blit(S(thumb), slider_thumb_x(pos, 16, 248, 29, 0, 1), 72)

        for el, base in (('previous', 'MAIN_PREVIOUS_BUTTON'), ('play', 'MAIN_PLAY_BUTTON'),
                         ('pause', 'MAIN_PAUSE_BUTTON'), ('stop', 'MAIN_STOP_BUTTON'),
                         ('next', 'MAIN_NEXT_BUTTON'), ('eject', 'MAIN_EJECT_BUTTON')):
            x, y, _w, _h = MAIN_LAYOUT[el]
            self.blit(S((base + '_ACTIVE') if self.pressed == el else base), x, y)

        for el, base, on in (('shuffle', 'MAIN_SHUFFLE_BUTTON', st['shuffle']),
                             ('repeat', 'MAIN_REPEAT_BUTTON', st['repeat'])):
            x, y, _w, _h = MAIN_LAYOUT[el]
            name = base + ('_SELECTED' if on else '') + ('_DEPRESSED' if self.pressed == el else '')
            self.blit(S(name), x, y)

        self.blit(self._vis_image(), 24, 43, tag='vis')
        self._blit_rims(MAIN_LAYOUT, RIM_MAIN, (
            ('MAIN_WINDOW_BACKGROUND', 0, 0), ('MAIN_TITLE_BAR_SELECTED' if self.focused else 'MAIN_TITLE_BAR', 0, 0),
            ('MAIN_CLUTTER_BAR_BACKGROUND', 10, 22)))

    def _drag_is(self, kind):
        return self.drag is not None and self.drag[0] == kind

    def _on_press(self, event):
        el = hit_test(MAIN_LAYOUT, event.x, event.y, self.scale)
        self.press_el = el
        if el is None:
            return
        if el == 'titlebar':
            self.drag = ('window', event.x_root - self.winfo_x(), event.y_root - self.winfo_y())
            return
        if el == 'position':
            self.drag = ('position', slider_value(event.x / self.scale, 16, 248, 29, 0, 1))
            self.redraw()
            return
        if el == 'volume':
            value = slider_value(event.x / self.scale, 107, 68, 14, 0, 100)
            self.drag = ('volume', value)
            self.state['volume'] = value
            self.fire('volume', value)
            self.redraw()
            return
        if el == 'balance':
            self.drag = ('balance', slider_value(event.x / self.scale, 177, 38, 14, -100, 100))
            self.redraw()
            return
        if el in MAIN_BUTTONS:
            self.pressed = el
            self.redraw()

    def _on_motion(self, event):
        if self.drag is None:
            return
        kind = self.drag[0]
        if kind == 'window':
            self.geometry('+%d+%d' % (event.x_root - self.drag[1],
                                      event.y_root - self.drag[2]))
            return
        if kind == 'position':
            self.drag = ('position', slider_value(event.x / self.scale, 16, 248, 29, 0, 1))
        elif kind == 'volume':
            value = slider_value(event.x / self.scale, 107, 68, 14, 0, 100)
            self.drag = ('volume', value)
            self.state['volume'] = value
            self.fire('volume', value)
        elif kind == 'balance':
            self.drag = ('balance', slider_value(event.x / self.scale, 177, 38, 14, -100, 100))
        self.redraw()

    def _on_release(self, event):
        el = hit_test(MAIN_LAYOUT, event.x, event.y, self.scale)
        drag, self.drag = self.drag, None
        pressed, self.pressed = self.pressed, None
        press_el, self.press_el = self.press_el, None
        if drag is not None:
            if drag[0] == 'window':
                self.fire('moved', self.winfo_x(), self.winfo_y())
            elif drag[0] == 'position':
                self.fire('seek', drag[1])
            self.redraw()
            return
        if pressed is not None and pressed == el:
            if pressed == 'menu':
                self.fire('menu', event.x_root, event.y_root)
            elif pressed == 'eq_button':
                self.fire('eq')
            elif pressed == 'pl_button':
                self.fire('pl')
            else:
                self.fire(pressed)
        elif press_el == el and press_el in MAIN_CLICKS:
            if press_el == 'time':
                self.show_remaining = not self.show_remaining
            elif press_el == 'heart':
                self.fire('heart')
            elif press_el == 'visualizer':
                self.fire('vis_mode')
            elif press_el == 'clutter_o':
                self.fire('menu', event.x_root, event.y_root)
            else:
                self.fire(press_el)
        self.redraw()


class SkinEqWindow(SkinWindow):
    def __init__(self, master, skin, scale, callbacks):
        super().__init__(master, skin, scale, 275, 116, callbacks)
        self.state = {'enabled': True, 'bands': [0.0] * 10, 'preamp': 0.0}
        self.pressed = None
        self.press_el = None
        self.drag = None
        self._win_drag = None
        self.canvas.bind('<ButtonPress-1>', self._on_press)
        self.canvas.bind('<B1-Motion>', self._on_motion)
        self.canvas.bind('<ButtonRelease-1>', self._on_release)
        self.redraw()

    def update_state(self, **kw):
        self.state.update(kw)
        self.redraw()

    def draw(self):
        skin = self.skin
        st = self.state
        S = skin.sprite

        self.blit(S('EQ_WINDOW_BACKGROUND'), 0, 0)
        self.blit(S('EQ_TITLE_BAR_SELECTED' if self.focused else 'EQ_TITLE_BAR'), 0, 0)

        name = 'EQ_ON_BUTTON' + ('_SELECTED' if st['enabled'] else '')
        if self.pressed == 'on':
            name += '_DEPRESSED'
        x, y, _w, _h = EQ_LAYOUT['on']
        self.blit(S(name), x, y)

        x, y, _w, _h = EQ_LAYOUT['auto']
        self.blit(S('EQ_AUTO_BUTTON'), x, y)

        x, y, _w, _h = EQ_LAYOUT['presets']
        self.blit(S('EQ_PRESETS_BUTTON_SELECTED' if self.pressed == 'presets'
                    else 'EQ_PRESETS_BUTTON'), x, y)

        x, y, _w, _h = EQ_LAYOUT['close']
        self.blit(S('EQ_CLOSE_BUTTON_ACTIVE' if self.pressed == 'close' else 'EQ_CLOSE_BUTTON'),
                  x, y)

        self.blit(S('EQ_GRAPH_BACKGROUND'), 86, 17)
        self.blit(S('EQ_PREAMP_LINE'), 86, 17 + round((12 - st['preamp']) / 24 * 18))
        curve = Image.new('RGBA', (113, 19), (0, 0, 0, 0))
        line_colors = S('EQ_GRAPH_LINE_COLORS')
        bands = st['bands']
        for px in range(113):
            t = px / 112 * 9
            i = int(t)
            f = t - i
            db = bands[i] * (1 - f) + bands[min(i + 1, 9)] * f
            cy = min(max(round((12 - db) / 24 * 18), 0), 18)
            curve.putpixel((px, cy), line_colors.getpixel((0, cy)))
        self.blit(curve, 86, 17)

        for key in EQ_SLIDERS:
            x, y, _w, _h = EQ_LAYOUT[key]
            db = st['preamp'] if key == 'preamp' else st['bands'][int(key[4:])]
            f = eq_frame(db)
            self.blit(skin.crop('eqmain.bmp', 13 + (f % 14) * 15, 164 + (f // 14) * 65, 14, 63),
                      x, y)
            thumb = 'EQ_SLIDER_THUMB_SELECTED' if self.drag == key else 'EQ_SLIDER_THUMB'
            self.blit(S(thumb), x + 1, y + eq_thumb_y(db))
        self._blit_rims(EQ_LAYOUT, RIM_EQ, (
            ('EQ_WINDOW_BACKGROUND', 0, 0), ('EQ_TITLE_BAR_SELECTED' if self.focused else 'EQ_TITLE_BAR', 0, 0)))

    def _apply_slider(self, key, event):
        y = EQ_LAYOUT[key][1]
        db = 12 - ((event.y / self.scale) - y - 5.5) / 51 * 24
        db = round(min(max(db, -12.0), 12.0), 1)
        if key == 'preamp':
            self.state['preamp'] = db
        else:
            self.state['bands'][int(key[4:])] = db
        self.redraw()
        self.fire('change', list(self.state['bands']), self.state['preamp'],
                  self.state['enabled'])

    def _on_press(self, event):
        el = hit_test(EQ_LAYOUT, event.x, event.y, self.scale)
        self.press_el = el
        if el is None:
            return
        if el == 'titlebar':
            self._win_drag = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())
            return
        if el in EQ_SLIDERS:
            self.drag = el
            self._apply_slider(el, event)
            return
        if el in ('on', 'presets', 'close'):
            self.pressed = el
            self.redraw()

    def _on_motion(self, event):
        if self._win_drag is not None:
            x, y = event.x_root - self._win_drag[0], event.y_root - self._win_drag[1]
            if 'drag' in self.callbacks:          # part of a stack: the controller moves the whole stack
                self.callbacks['drag'](x, y)
            else:
                self.geometry('+%d+%d' % (x, y))
            return
        if self.drag is not None:
            self._apply_slider(self.drag, event)

    def _on_release(self, event):
        el = hit_test(EQ_LAYOUT, event.x, event.y, self.scale)
        pressed, self.pressed = self.pressed, None
        win_drag, self._win_drag = self._win_drag, None
        self.drag = None
        self.press_el = None
        if win_drag is not None:
            self.fire('moved', self.winfo_x(), self.winfo_y())
            self.redraw()
            return
        if pressed == 'on' and el == 'on':
            self.state['enabled'] = not self.state['enabled']
            self.fire('change', list(self.state['bands']), self.state['preamp'],
                      self.state['enabled'])
        elif pressed == 'presets' and el == 'presets':
            self.fire('presets', event.x_root, event.y_root)
        elif pressed == 'close' and el == 'close':
            self.fire('close')
        self.redraw()
