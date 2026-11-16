"""
skin_mode.py - runs the player in a classic Winamp skin.

SkinController owns the skinned windows (main, EQ, playlist, Albums) and the saved choice in ui_settings.json. When a
skin is picked it is saved and the default view (the root window) is withdrawn; the root keeps its title, media-key
hook and single-instance mutex, so a second launch still reaches the player (a second launch that shows the root is
turned back into raising the skinned main window). fn-lock skips hidden windows, so while skinned it sends F5-F8 and
the dial to the skinned main window ("Music Player V2 (skin)"), which gets the same media-key hook (appcommand_hook).
Picking "No skin", or a saved skin that is missing or broken at startup, brings the default view back.

It talks to the player only through MusicPlayerGUI's existing methods and state.
"""

import array
import collections
import ctypes
import ctypes.wintypes as wt
import json
import os
import tkinter as tk
from tkinter import messagebox, ttk
from types import SimpleNamespace

import appcommand_hook
import skin as skin_lib
import skin_layout
import skin_math
from albums_browser import AlbumsBrowser
from skin_playlist import SkinPlaylistWindow
from skin_ui import SkinEqWindow, SkinMainWindow

REFRESH_MS = 250
VIS_MS = 33
KEEP_MS = 60          # after a move: let docked windows follow before pulling title bars back on screen
VIS_MODES = ('spectrum', 'scope', 'off')
SAMPLE_RATE = 48000          # the engine's output format: 48 kHz stereo float32
SIZES = (1, 2, 3)     # whole-number scales only: nearest-neighbour pixels stay crisp
DEFAULTS = {'skin': None, 'size': 1, 'always_on_top': False, 'vis_mode': 'spectrum',
            'eq_open': True, 'pl_open': True, 'positions': {}, 'pl_height': 232}


def _log(text):
    """print, but never raise (a console that can't show ⚠ must not break the skin)."""
    try:
        print(text)
    except Exception:
        try:
            print(text.encode('ascii', 'replace').decode('ascii'))
        except Exception:
            pass


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def load_settings(path):
    """ui_settings.json, with every value checked: anything odd falls back to its default."""
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    out = dict(DEFAULTS)
    out['positions'] = {}
    if not isinstance(data, dict):
        return out
    name = data.get('skin')
    if isinstance(name, str) and name.lower().endswith('.wsz') and os.path.basename(name) == name \
            and '/' not in name and '\\' not in name:
        out['skin'] = name
    for k in ('always_on_top', 'eq_open', 'pl_open'):
        if isinstance(data.get(k), bool):
            out[k] = data[k]
    if _is_int(data.get('size')) and data['size'] in SIZES:
        out['size'] = data['size']
    elif 'size' not in data and data.get('double_size') is True:     # settings saved before sizes existed
        out['size'] = 2
    if data.get('vis_mode') in VIS_MODES:
        out['vis_mode'] = data['vis_mode']
    if _is_int(data.get('pl_height')):
        out['pl_height'] = min(max(data['pl_height'], 116), 2000)
    positions = data.get('positions')
    if isinstance(positions, dict):
        for k in ('main', 'eq', 'pl', 'albums'):
            v = positions.get(k)
            if isinstance(v, list) and len(v) == 2 and all(_is_int(n) for n in v):
                out['positions'][k] = list(v)
    return out


def virtual_screen(win):
    """(x, y, w, h) of the whole desktop across monitors (Windows), else the primary screen."""
    try:
        m = ctypes.windll.user32.GetSystemMetrics
        x, y, w, h = m(76), m(77), m(78), m(79)   # SM_XVIRTUALSCREEN .. SM_CYVIRTUALSCREEN
        if w > 0 and h > 0:
            return x, y, w, h
    except AttributeError:
        pass
    return 0, 0, win.winfo_screenwidth(), win.winfo_screenheight()


def work_areas(win):
    """(x, y, w, h) of every monitor's work area (the desktop minus the taskbar) on Windows, else the whole screen."""
    try:
        user32 = ctypes.WinDLL('user32')
    except (AttributeError, OSError):
        return [(0, 0, win.winfo_screenwidth(), win.winfo_screenheight())]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [('cbSize', wt.DWORD), ('rcMonitor', wt.RECT), ('rcWork', wt.RECT), ('dwFlags', wt.DWORD)]

    user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONITORINFO)]
    areas = []

    def one(hmon, _hdc, _rect, _data):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcWork
            if r.right > r.left and r.bottom > r.top:
                areas.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        return 1

    proc = ctypes.WINFUNCTYPE(ctypes.c_int, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)(one)
    try:
        user32.EnumDisplayMonitors(None, None, proc, 0)
    except Exception as e:
        _log(f"⚠ Could not list the monitors: {e!r}")
    return areas or [virtual_screen(win)]


def _overlap(rect, area):
    """Pixels of rect inside area (0 when area is None)."""
    if area is None:
        return 0
    x, y, w, h = rect
    ax, ay, aw, ah = area
    return max(0, min(x + w, ax + aw) - max(x, ax)) * max(0, min(y + h, ay + ah) - max(y, ay))


def _area_of(rect, areas):
    """The work area that holds most of rect (the first one when it is on none)."""
    return max(areas, key=lambda a: _overlap(rect, a)) if areas else None


def save_settings(path, settings):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=1)
    os.replace(tmp, path)


def set_app_window(win):
    """Give a borderless Toplevel its own taskbar button (Windows WS_EX_APPWINDOW). No-op elsewhere."""
    try:
        user32 = ctypes.windll.user32
    except AttributeError:
        return
    GWL_EXSTYLE, WS_EX_APPWINDOW, WS_EX_TOOLWINDOW = -20, 0x00040000, 0x00000080
    try:
        win.update_idletasks()     # Tk builds the window's outer frame when it first maps it; until then there is none
        hwnd = user32.GetParent(win.winfo_id())
        if not hwnd:
            _log("⚠ Could not give the skinned window a taskbar button: it has no frame yet")
            return
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW)
        win.withdraw()
        win.after(10, win.deiconify)
    except Exception as e:
        _log(f"⚠ Could not give the skinned window a taskbar button: {e}")


def minimize_window(win):
    """Minimize a borderless window (Tk's iconify() refuses override-redirect windows). Posted, not ShowWindow():
    ShowWindow minimizes inside the ctypes call, which runs Tk event bindings (Python) while ctypes has released the
    GIL: a fatal "GIL is released" crash."""
    WM_SYSCOMMAND, SC_MINIMIZE = 0x0112, 0xF020
    try:
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        ctypes.windll.user32.PostMessageW(hwnd, WM_SYSCOMMAND, SC_MINIMIZE, 0)
    except AttributeError:
        win.withdraw()


class SkinFrameWindow(SkinPlaylistWindow):
    """A window in the skin's playlist frame with arbitrary Tk content in self.body (used for Albums)."""

    HOVER_LABELS = {}              # its close button really closes it: no playlist labels

    def __init__(self, master, skin, scale, callbacks, height=290):
        self.body = None
        super().__init__(master, skin, scale, callbacks, height=height)
        self.body = tk.Frame(self.canvas, bd=0, highlightthickness=0)
        self.entry.destroy()
        self.listbox.destroy()
        self.redraw()

    def redraw(self):
        if self.body is None:      # the base __init__'s first draw, before the body exists
            return super().redraw()
        s = self.scale
        self.canvas.delete('frame', 'inner')
        self._photos.clear()
        self.canvas.config(width=275 * s, height=self.height * s)
        sel = '_SELECTED' if self.focused else ''
        S = self.skin.sprite
        self._blit(S('PLAYLIST_TOP_LEFT_SELECTED' if self.focused else 'PLAYLIST_TOP_LEFT_CORNER'), 0, 0)
        for x in range(25, 250, 25):
            self._blit(S('PLAYLIST_TOP_TILE' + sel), x, 0)
        self._blit(S('PLAYLIST_TITLE_BAR' + sel), 87, 0)
        self._blit(S('PLAYLIST_TOP_RIGHT_CORNER' + sel), 250, 0)
        for y in range(20, self.height - 38, 29):
            self._blit(S('PLAYLIST_LEFT_TILE'), 0, y)
            self._blit(S('PLAYLIST_RIGHT_TILE'), 255, y)
        self._blit(S('PLAYLIST_BOTTOM_LEFT_CORNER'), 0, self.height - 38)
        self._blit(S('PLAYLIST_BOTTOM_RIGHT_CORNER'), 125, self.height - 38)
        self.body.config(bg=self.skin.pledit['normalbg'])
        self.canvas.create_window(12 * s, 20 * s, anchor='nw', window=self.body, width=243 * s,
                                  height=(self.height - 58) * s, tags=('inner',))

    def _paint_rows(self):
        pass


class SkinController:
    def __init__(self, player):
        self.p = player
        base = os.path.dirname(str(player.listening_log_file))
        self.skins_dir = os.path.join(base, 'skins')
        self.settings_path = os.path.join(base, 'ui_settings.json')
        self.settings = load_settings(self.settings_path)
        self.skin = None
        self.main = self.eq = self.pl = self.albums = None
        self._chunks = collections.deque(maxlen=4)
        self._jobs = {}               # loop name -> its pending after() id
        self._map_bind = None
        self._albums_browser = None
        self._last_status = None
        self._last_icon = None
        self._last_rows = None
        self._pl_query = ''
        self._main_pos = None
        self._key_hook = None         # media keys (fn-lock WM_APPCOMMAND) on the skinned main window

    # ---- choosing a skin ---------------------------------------------------------

    @property
    def active(self):
        return self.main is not None

    def scale(self):
        return self.settings['size']

    def available(self):
        return skin_lib.list_skins(self.skins_dir)

    def start(self):
        """At startup: open the saved skin, or stay in the default view with a status line saying why not."""
        name = self.settings.get('skin')
        if not name:
            return
        path = os.path.join(self.skins_dir, name)
        if not os.path.exists(path):
            self.p.status_label.config(text=f"Skin {name} is missing from the skins folder - default view")
            return
        try:
            loaded = skin_lib.load_skin(path)
        except skin_lib.SkinError as e:
            self.p.status_label.config(text=f"Couldn't load skin {name}: {e} - default view")
            return
        error = self.enter(loaded)
        if error:
            self.p.status_label.config(text=f"Couldn't show skin {name}: {error} - default view")

    def pick(self, name):
        """Menu: a skin file name, or None for the default view. A skin that fails keeps what was showing."""
        if name is None:
            self.settings['skin'] = None
            self.save()
            self.leave()
            return
        try:
            loaded = skin_lib.load_skin(os.path.join(self.skins_dir, name))
        except skin_lib.SkinError as e:
            messagebox.showwarning("Skin", f"Couldn't load {name}: {e}", parent=self.dialog_parent())
            return
        old = self.skin if self.active else None
        if old is not None:
            self._remember_positions()
            self._close_windows()
        error = self.enter(loaded)
        if not error:
            self.settings['skin'] = name
            self.save()
            return
        messagebox.showwarning("Skin", f"Couldn't show {name}: {error}", parent=self.dialog_parent())
        if old is not None and not self.enter(old):
            return                                   # back on the previous skin, its name still saved
        self.settings['skin'] = None
        self.save()

    def open_folder(self):
        os.makedirs(self.skins_dir, exist_ok=True)
        try:
            os.startfile(self.skins_dir)
        except (AttributeError, OSError) as e:
            messagebox.showinfo("Skins", f"Put .wsz skins in:\n{self.skins_dir}\n\n({e})", parent=self.dialog_parent())

    def fill_skins_menu(self, menu):
        """(Re)build a Skins menu: every .wsz, No skin, Open skins folder."""
        menu.delete(0, tk.END)
        current = self.settings.get('skin') if self.active else None
        var = tk.StringVar(value=current or '')
        menu._skin_var = var
        for name in self.available():
            menu.add_radiobutton(label=os.path.splitext(name)[0], value=name, variable=var,
                                 command=lambda n=name: self.p.root.after(1, self.pick, n))
        menu.add_radiobutton(label="No skin (default view)", value='', variable=var,
                             command=lambda: self.p.root.after(1, self.pick, None))
        menu.add_separator()
        menu.add_command(label="Open skins folder", command=self.open_folder)

    # ---- entering and leaving --------------------------------------------------------

    def enter(self, loaded):
        """Show the skinned windows. Returns None, or a reason when it failed - then the default view is back
        and nothing is left half-built."""
        try:
            self._enter(loaded)
            return None
        except Exception as e:
            _log(f"⚠ Could not show skin {getattr(loaded, 'name', '?')}: {e!r}")
            self._back_to_default()
            return f"{e.__class__.__name__}: {e}"

    def _back_to_default(self, status=None):
        """Close whatever skinned windows are left and show the default view (the saved skin choice is kept)."""
        self._close_windows()
        self.skin = None
        p = self.p
        if p._engine is not None:
            p._engine.vis_tap = None
        try:
            p.apply_theme()
        except Exception:
            pass
        p.root.deiconify()
        p.root.lift()
        if status:
            p.status_label.config(text=status)

    def _alive(self, w):
        try:
            return w is not None and bool(w.winfo_exists())
        except tk.TclError:
            return False

    def _main_alive(self):
        return self._alive(self.main)

    def _check_windows(self):
        """A skinned window destroyed behind our back: forget a lost EQ/playlist/Albums; a lost main window brings
        the default view back. Returns False when skin mode ended."""
        if not self._main_alive():
            _log("⚠ The skin's main window was closed from outside - back to the default view")
            self._back_to_default("The skin window was closed - default view")
            return False
        lost = False
        for name in ('eq', 'pl', 'albums'):
            if getattr(self, name) is not None and not self._alive(getattr(self, name)):
                setattr(self, name, None)
                lost = True
                if name == 'eq':
                    self.settings['eq_open'] = False
        if self.pl is None:              # the playlist is always open: bring it back
            self._open_pl()
            self._apply_topmost()
        elif lost:
            self._layout_stack()
        return True

    def _skin_toplevel(self, win, on_close):
        """Common setup of every skinned window but the main one: it hides and returns with the main window
        (minimize), and Alt+F4 / the taskbar's Close does what its own close button does."""
        win.transient(self.main)
        win.protocol('WM_DELETE_WINDOW', on_close)
        # FocusIn also fires as focus moves between the window's own widgets; the extra lift is harmless, and
        # activating the playlist can hand focus straight to its search box
        win.bind('<FocusIn>', lambda e: self._raise_group(win), add='+')

    def _raise_group(self, focused):
        """Clicking any skinned window brings all of them forward together, like Winamp."""
        for w in self._windows():
            if w is not focused:
                try:
                    w.lift()
                except tk.TclError:
                    pass
        try:
            focused.lift()
        except tk.TclError:
            pass

    # ---- dialogs ---------------------------------------------------------------------------

    def dialog_parent(self):
        """The window a dialog should sit on: the skin's main window while skinned (so the dialog opens above an
        always-on-top skin), else the default view."""
        return self.main if self._main_alive() else self.p.root

    def own_dialog(self, win):
        """Tie a dialog Toplevel to the skin's main window while skinned: it shows (the default view is hidden),
        stays above the skin and minimizes with it. Does nothing in the default view."""
        if self._main_alive():
            win.transient(self.main)

    def _enter(self, loaded):
        self.skin = loaded
        p, s = self.p, self.scale()
        self._apply_ttk_colors()
        pos = self.settings['positions']
        mx, my = pos.get('main', (200, 200))
        self.main = SkinMainWindow(p.root, loaded, s, self._main_callbacks())
        self.main.title("Music Player V2 (skin)")
        self.main.geometry(f'+{mx}+{my}')
        self.main.bind('<Configure>', self._on_main_configure, add='+')
        self.main.protocol('WM_DELETE_WINDOW', p._on_close)     # Alt+F4 / taskbar Close = the close button
        self.main.bind('<FocusIn>', lambda e: self._raise_group(self.main), add='+')
        if self.settings['eq_open']:
            self._open_eq()
        self._open_pl()                  # always open while skinned
        self._apply_topmost()
        p.root.withdraw()
        if self._map_bind is None:
            self._map_bind = p.root.bind('<Map>', self._on_root_mapped, add='+')
        set_app_window(self.main)
        self._hook_media_keys()
        if p._engine is not None:
            p._engine.vis_tap = self._chunks.append
        self._last_icon = None
        self._schedule(REFRESH_MS, self._refresh)
        self._schedule(VIS_MS, self._vis_tick)
        self._refresh()
        self._later_keep_reachable()

    def leave(self):
        if not self.active:
            return
        self._remember_positions()
        self.save()
        self._close_windows()
        self.skin = None
        p = self.p
        if p._engine is not None:
            p._engine.vis_tap = None
        p.apply_theme()                 # the default view's ttk colors
        p.root.deiconify()
        p.root.lift()

    def _close_windows(self):
        for job in self._jobs.values():
            try:
                self.p.root.after_cancel(job)
            except (tk.TclError, ValueError):
                pass
        self._jobs.clear()
        if self._key_hook is not None:
            self._key_hook.remove()
            self._key_hook = None
        for name in ('albums', 'pl', 'eq', 'main'):
            w = getattr(self, name)
            if w is not None:
                try:
                    w.destroy()
                except tk.TclError:
                    pass
                setattr(self, name, None)
        browsers = getattr(self.p, '_album_browsers', None)
        if browsers is not None:
            self.p._album_browsers = [b for b in browsers if b is self.p.albums_browser]
        self._albums_browser = None
        self._last_rows = None
        self._main_pos = None

    def save(self):
        try:
            save_settings(self.settings_path, self.settings)
        except OSError as e:
            _log(f"⚠ Could not save ui_settings.json: {e}")

    def on_app_close(self):
        if self.active:
            self._remember_positions()
            self.save()

    def _remember_positions(self):
        pos = self.settings['positions']
        for name in ('main', 'eq', 'pl', 'albums'):
            w = getattr(self, name)
            if w is not None:
                try:
                    pos[name] = [w.winfo_x(), w.winfo_y()]
                except tk.TclError:
                    pass
        if self.pl is not None:
            self.settings['pl_height'] = self.pl.height

    def _hook_media_keys(self):
        """fn-lock sends F5-F8 and the dial to the first visible window titled "Music Player V2..."; the root is hidden
        while skinned, so that is the skinned main window. Hand its keys to the player like the root's hook does."""
        queue = getattr(self.p, '_queue_appcommand', None)
        if queue is not None and self.main is not None:
            self._key_hook = appcommand_hook.install(self.main, queue)

    def _on_root_mapped(self, event):
        """A second launch shows the root window: while skinned, hide it again and raise the skin instead."""
        if event.widget is self.p.root and self.active:
            self.p.root.after(1, self._raise_skin)

    def _raise_skin(self):
        if not self.active or not self._check_windows():
            return
        self.p.root.withdraw()
        self.main.deiconify()
        self.main.lift()
        self.main.focus_force()

    def _schedule(self, ms, fn):
        """Run fn every ms while skinned; one failing tick is logged and the loop keeps going."""
        name = fn.__name__

        def run():
            if not self.active or not self._check_windows():
                return
            try:
                fn()
            except Exception as e:
                _log(f"⚠ Skin {name} failed: {e!r}")
            finally:
                if self.active:
                    self._jobs[name] = self.p.root.after(ms, run)
        self._jobs[name] = self.p.root.after(ms, run)

    def _apply_ttk_colors(self):
        c = self.skin.pledit
        style = ttk.Style()
        style.configure('Treeview', background=c['normalbg'], foreground=c['normal'], fieldbackground=c['normalbg'])
        style.map('Treeview', background=[('selected', c['selectedbg'])], foreground=[('selected', c['current'])])
        style.configure('Treeview.Heading', background=c['normalbg'], foreground=c['current'])
        for name in ('TFrame', 'TLabelframe', 'TLabelframe.Label', 'TLabel', 'TRadiobutton', 'TNotebook'):
            style.configure(name, background=c['normalbg'], foreground=c['normal'])
        style.configure('TNotebook.Tab', background=c['normalbg'], foreground=c['normal'])
        style.map('TNotebook.Tab', background=[('selected', c['selectedbg'])])

    # ---- the windows -----------------------------------------------------------------

    def _main_callbacks(self):
        p = self.p
        return {
            'previous': p.previous_track, 'play': p.play, 'pause': p.pause, 'stop': p.stop,
            'next': p.next_track, 'eject': self.toggle_albums, 'shuffle': lambda: p.apply_shuffle(),
            'repeat': lambda: self.main.flash("REPEAT ISN'T AVAILABLE"),
            'eq': self.toggle_eq, 'pl': self.toggle_pl, 'seek': self._seek, 'volume': self._volume,
            'close': p._on_close, 'minimize': lambda: minimize_window(self.main), 'menu': self.show_menu,
            'heart': p.toggle_loved, 'clutter_a': self.toggle_topmost, 'clutter_d': self.cycle_size,
            'clutter_i': self.song_info, 'clutter_v': self.cycle_vis, 'vis_mode': self.cycle_vis,
            'moved': lambda x, y: self._snap(self.main, 'main'),
        }

    def _open_eq(self):
        s = self.scale()
        self.eq = SkinEqWindow(self.p.root, self.skin, s, {
            'change': self._eq_change, 'presets': self._eq_presets, 'close': self.toggle_eq,
            'menu': self.show_menu, 'drag': lambda x, y: self._drag_stack(self.eq, x, y),
            'moved': lambda x, y: self._snap(self.main, 'main')})
        self._skin_toplevel(self.eq, self.toggle_eq)
        self._layout_stack()
        self._sync_eq()

    def _open_pl(self):
        """The playlist is always open while skinned (Jacob 2026-11-14): nothing closes it."""
        s = self.scale()
        self.pl = SkinPlaylistWindow(self.p.root, self.skin, s, {
            'play': self._pl_play, 'search': self._pl_search, 'search_enter': self._pl_enter,
            'menu': self._pl_menu, 'close': lambda: None, 'drag': lambda x, y: self._drag_stack(self.pl, x, y),
            'moved': lambda x, y: self._snap(self.main, 'main'),
            'resized': lambda h: self.settings.__setitem__('pl_height', h)}, height=self.settings['pl_height'])
        self._skin_toplevel(self.pl, lambda: None)
        self.settings['pl_open'] = True
        self._layout_stack()
        self._last_rows = None

    def toggle_eq(self):
        if self.eq is not None:
            self.eq.destroy()
            self.eq = None
            self._layout_stack()
        else:
            self._open_eq()
            self._apply_topmost()
            self._later_keep_reachable()
        self.settings['eq_open'] = self.eq is not None
        self.save()

    def toggle_pl(self):
        """The playlist is always open while skinned: the main window's PL button and old callers do nothing."""

    def toggle_albums(self):
        if self.albums is not None:
            self.settings['positions']['albums'] = [self.albums.winfo_x(), self.albums.winfo_y()]
            browsers = self.p._album_browsers
            self.p._album_browsers = [b for b in browsers if b is not self._albums_browser]
            self.albums.destroy()
            self.albums = None
            return
        s = self.scale()
        win = SkinFrameWindow(self.p.root, self.skin, s, {'close': self.toggle_albums, 'menu': self.show_menu},
                              height=290)
        pos = self.settings['positions'].get('albums')
        if pos is None:
            self.main.update_idletasks()
            pos = (self.main.winfo_x() + 275 * s, self.main.winfo_y())
        win.geometry(f'+{pos[0]}+{pos[1]}')
        self._skin_toplevel(win, self.toggle_albums)
        c = self.skin.pledit
        query = tk.StringVar()
        entry = tk.Entry(win.body, textvariable=query, relief=tk.FLAT, bg=c['normalbg'], fg=c['normal'],
                         insertbackground=c['current'])
        entry.pack(fill=tk.X)
        holder = ttk.Frame(win.body)
        holder.pack(fill=tk.BOTH, expand=True)
        browser = AlbumsBrowser(self.p, holder, status=lambda t: self.main and self.main.flash(t.upper()),
                                get_query=query.get)
        query.trace_add('write', lambda *a: browser.filter(query.get()))
        self._albums_browser = browser
        self.p._album_browsers.append(browser)
        self.albums = win
        self._apply_topmost()
        self._later_keep_reachable()

    def toggle_topmost(self):
        self.settings['always_on_top'] = not self.settings['always_on_top']
        self._apply_topmost()
        self.save()

    def _apply_topmost(self):
        on = self.settings['always_on_top']
        for w in (self.main, self.eq, self.pl, self.albums):
            if w is not None:
                w.attributes('-topmost', on)

    def cycle_size(self):
        """The D button: 1x -> 2x -> 3x -> 1x."""
        self.set_size(SIZES[(SIZES.index(self.scale()) + 1) % len(SIZES)])

    def set_size(self, size):
        if size not in SIZES:
            return
        self._remember_positions()
        self.settings['size'] = size
        s = self.scale()
        for w in (self.main, self.eq, self.pl, self.albums):
            if w is not None:
                w.set_skin(self.skin, s)
        self._layout_stack()
        self.save()
        self._refresh()
        self._later_keep_reachable()

    def cycle_vis(self):
        i = VIS_MODES.index(self.settings['vis_mode'])
        self.settings['vis_mode'] = VIS_MODES[(i + 1) % len(VIS_MODES)]
        if self.settings['vis_mode'] == 'off':
            self.main.set_vis(None)
        self.save()

    def song_info(self):
        p = self.p
        if not (0 <= p.current_index < len(p.current_playlist)):
            return
        t = p.current_playlist[p.current_index]
        messagebox.showinfo("Song info", f"{t.title}\n{t.artist}\n{t.album}\n\n{t.filepath}", parent=self.main)

    # ---- menus -------------------------------------------------------------------------

    def show_menu(self, x_root, y_root):
        p = self.p
        menu = tk.Menu(self.main, tearoff=0)
        menu.add_command(label="Play / Pause", command=p.toggle_play_pause)
        menu.add_command(label="Stop", command=p.stop)
        menu.add_command(label="Previous", command=p.previous_track)
        menu.add_command(label="Next", command=p.next_track)
        menu.add_separator()
        menu.add_command(label="Albums…", command=self.toggle_albums)
        menu.add_command(label="Listening Stats…", command=p.open_stats_window)
        menu.add_checkbutton(label="Equalizer", command=self.toggle_eq,
                             variable=tk.BooleanVar(menu, value=self.eq is not None))
        menu.add_separator()
        skins = tk.Menu(menu, tearoff=0)
        self.fill_skins_menu(skins)
        menu.add_cascade(label="Skins", menu=skins)
        settings = tk.Menu(menu, tearoff=0)
        src = p.settings_menu
        for i in range((src.index(tk.END) or -1) + 1):
            kind = src.type(i)
            if kind == 'separator':
                settings.add_separator()
            elif kind == 'command':
                settings.add_command(label=src.entrycget(i, 'label'), command=src.entrycget(i, 'command'))
        menu.add_cascade(label="Settings", menu=settings)
        menu.add_separator()
        menu.add_checkbutton(label="Always on top", command=self.toggle_topmost,
                             variable=tk.BooleanVar(menu, value=self.settings['always_on_top']))
        sizes = tk.Menu(menu, tearoff=0)
        size_var = tk.IntVar(menu, value=self.scale())
        menu._size_var = size_var            # keep the variable alive while the menu is up
        for n in SIZES:
            sizes.add_radiobutton(label=f"{n}×", value=n, variable=size_var,
                                  command=lambda n=n: self.set_size(n))
        menu.add_cascade(label="Size", menu=sizes)
        menu.add_separator()
        menu.add_command(label="Exit", command=p._on_close)
        try:
            menu.tk_popup(x_root, y_root)
        finally:
            menu.grab_release()

    def _pl_menu(self, x_root, y_root, index):
        p = self.p
        if index is None:                  # off the songs: the main menu (Settings, Skins, ...)
            self.show_menu(x_root, y_root)
            return
        if not (0 <= index < len(self._pl_tracks())):
            return
        track = self._pl_tracks()[index]
        album = p.library_index.album_of(track) if p.library_index is not None else None
        menu = tk.Menu(self.pl, tearoff=0)
        menu.add_command(label="Play Next", command=lambda: p.queue_track(track, play_next=True))
        menu.add_command(label="Add to Up Next", command=lambda: p.queue_track(track))
        if album is not None:
            menu.add_separator()
            menu.add_command(label="Play album from start", command=lambda: p._album_action(album, 'album'))
            menu.add_command(label="Play artist", command=lambda: p._album_action(album, 'artist'))
            menu.add_command(label="Add album to Up Next", command=lambda: p._album_action(album, 'queue'))
        try:
            menu.tk_popup(x_root, y_root)
        finally:
            menu.grab_release()

    # ---- playlist -------------------------------------------------------------------------

    def _pl_tracks(self):
        p = self.p
        return p.filtered_playlist if p.filtered_playlist else p.current_playlist

    def _pl_search(self, text):
        self._pl_query = text
        self.p.search_var.set(text)   # the player's own search: same filter, takeover and Enter rules

    def _pl_enter(self):
        self.p._play_first_result()

    def _pl_play(self, index):
        p = self.p
        if p.filtered_playlist:
            if index < len(p.filtered_playlist):
                p._takeover_playlist(p.filtered_playlist, index)
        else:
            p.queued_count = 0
            p.play_track_at_index(index)

    def _sync_pl(self):
        p = self.p
        tracks = self._pl_tracks()
        key = (id(p.current_playlist), len(p.current_playlist), id(p.filtered_playlist), len(p.filtered_playlist),
               p.queued_count, p.current_index)
        if key == self._last_rows:
            return
        self._last_rows = key
        queued = set(range(p.current_index + 1, p.current_index + 1 + p.queued_count)) \
            if not p.filtered_playlist else set()
        rows = []
        for i, t in enumerate(tracks):
            mark = '+ ' if i in queued else ''
            rows.append(f"{i + 1}. {mark}{t.artist} - {t.title}  {t.format_duration()}")
        current = None
        if 0 <= p.current_index < len(p.current_playlist):
            playing = p.current_playlist[p.current_index]
            current = next((i for i, t in enumerate(tracks) if t is playing), None)
        self.pl.set_rows(rows, current)
        self.pl.see_current()

    # ---- EQ -----------------------------------------------------------------------------

    def _sync_eq(self):
        s = self.p.audio_settings
        self.eq.update_state(enabled=bool(s['eq_enabled']), bands=list(s['eq_bands_db']),
                             preamp=float(s['eq_preamp_db']))

    def _eq_change(self, bands, preamp, enabled, preset=''):
        self.p.apply_eq(bands, preamp, enabled, preset)

    def _eq_presets(self, x_root, y_root):
        presets = self.p._eq_presets()
        menu = tk.Menu(self.eq, tearoff=0)
        for name, (bands, preamp) in presets.items():
            menu.add_command(label=name, command=lambda n=name, b=bands, pa=preamp: (
                self._eq_change(b, pa, self.p.audio_settings['eq_enabled'], n), self._sync_eq()))
        menu.add_separator()
        menu.add_command(label="Import .eqf…", command=self.p._import_eqf)
        try:
            menu.tk_popup(x_root, y_root)
        finally:
            menu.grab_release()

    # ---- live state -----------------------------------------------------------------------

    def _seek(self, fraction):
        p = self.p
        width = max(1, p.progress_bar.winfo_width())
        p.on_progress_click(SimpleNamespace(x=fraction * width))

    def _volume(self, percent):
        p = self.p
        p.volume_var.set(percent)
        p.on_volume_change(percent)
        self.main.flash(f"VOLUME: {round(percent)}%", ms=1000)

    def _refresh(self):
        p = self.p
        track = None
        if p._playback_state in ('playing', 'paused') and 0 <= p.current_index < len(p.current_playlist):
            track = p.current_playlist[p.current_index]
        elapsed = 0.0
        if track is not None and p._engine is not None:
            try:
                with p._engine_lock:
                    elapsed = float(p._engine.position() or 0.0)
            except Exception:
                elapsed = 0.0
        duration = float(getattr(track, 'duration', 0) or 0) if track else 0.0
        kbps = ''
        if track is not None and duration > 0:
            try:
                kbps = str(min(999, round(os.path.getsize(track.filepath) * 8 / duration / 1000)))
            except OSError:
                kbps = ''
        self.main.update_state(
            play_state=p._playback_state if track is not None else 'stopped', elapsed=elapsed, duration=duration,
            title=skin_layout.marquee_text(getattr(track, 'artist', ''), getattr(track, 'title', ''), duration)
            if track else 'Music Player V2',
            kbps=kbps, khz='', stereo=True if track is not None else None,
            volume=float(p.volume_var.get()), position=(elapsed / duration) if duration else 0.0,
            shuffle=not p.is_temp_view, eq_open=self.eq is not None, pl_open=self.pl is not None,
            favorite=bool(getattr(track, 'loved', False)), always_on_top=self.settings['always_on_top'],
            double_size=self.scale() > 1)     # the D button is lit above 1x
        status = p.status_label.cget('text')
        if status != self._last_status:
            if self._last_status is not None and status:
                self.main.flash(str(status).upper())
            self._last_status = status
        icon = getattr(p, '_track_icon_photos', None) or getattr(p, '_default_icon_photos', None)
        if icon and icon is not self._last_icon:
            try:
                self.main.iconphoto(False, *icon)
            except tk.TclError:
                pass
            self._last_icon = icon
        if self.pl is not None:
            self._sync_pl()

    def _vis_tick(self):
        mode = self.settings['vis_mode']
        if mode == 'off' or self.p._playback_state != 'playing' or len(self._chunks) < 1:
            if self.main.vis is not None and self.p._playback_state != 'playing':
                self.main.set_vis(None)
            return
        chunk = self._chunks[-2] if len(self._chunks) >= 2 else self._chunks[-1]
        frames = len(chunk) // 8
        if frames < 64:
            return
        n = min(512, frames)
        start = (frames - n) // 2
        raw = array.array('f')
        raw.frombytes(chunk[start * 8:(start + n) * 8])
        mono = [(raw[2 * i] + raw[2 * i + 1]) / 2 for i in range(n)]
        if mode == 'spectrum':
            self.main.set_vis(skin_math.spectrum(mono, SAMPLE_RATE))
        else:
            self.main.set_vis(('scope', skin_math.scope(mono)))

    # ---- snapping and docking ----------------------------------------------------------------

    def _windows(self):
        return [w for w in (self.main, self.eq, self.pl, self.albums) if w is not None]

    def _rect(self, w):
        return (w.winfo_x(), w.winfo_y(), w.winfo_width(), w.winfo_height())

    def _snap(self, win, name):
        others = [self._rect(w) for w in self._windows() if w is not win]
        r = self._rect(win)
        screen = _area_of(r, work_areas(win)) or (0, 0, win.winfo_screenwidth(), win.winfo_screenheight())
        x, y = skin_math.snap(r, others, screen)
        win.geometry(f'+{x}+{y}')     # for the main window, <Configure> then moves the docked ones too
        self.settings['positions'][name] = [x, y]
        self.save()
        self._later_keep_reachable()  # once the docked windows have followed

    def _later_keep_reachable(self):
        self.p.root.after(KEEP_MS, self._keep_reachable)

    # ---- the stack: main on top, EQ under it when open, the playlist at the bottom ----------

    def _stack_offsets(self):
        """(window, y offset under the main window's top, height) for EQ (when open) and the playlist, in order."""
        s = self.scale()
        out, y = [], 116 * s
        for w in (self.eq, self.pl):
            if self._alive(w):
                h = (116 if w is self.eq else w.height) * s
                out.append((w, y, h))
                y += h
        return out

    def _stack_rects(self, origin):
        x, y = origin
        s = self.scale()
        return [(x, y, 275 * s, 116 * s)] + [(x, y + off, 275 * s, h) for _w, off, h in self._stack_offsets()]

    def _layout_stack(self, origin=None):
        """Put EQ and the playlist flush under the main window (same left edge). origin: the main window's
        top-left when it was just moved and Tk doesn't report the new spot yet."""
        if not self._main_alive():
            return
        if origin is None:
            self.main.update_idletasks()
            origin = (self.main.winfo_x(), self.main.winfo_y())
        mx, my = origin
        for w, off, _h in self._stack_offsets():
            if (w.winfo_x(), w.winfo_y()) != (mx, my + off):
                w.geometry(f'+{mx}+{my + off}')

    def _drag_stack(self, win, x, y):
        """EQ's or the playlist's title bar dragged so the window would sit at (x, y): move the whole stack."""
        off = next((o for w, o, _h in self._stack_offsets() if w is win), None)
        if off is not None and self._main_alive():
            self.main.geometry(f'+{x}+{y - off}')

    def _albums_docked(self, stack_rects):
        if not self._alive(self.albums):
            return False
        rects = list(stack_rects) + [self._rect(self.albums)]
        return len(rects) - 1 in skin_math.dock_group(0, rects)

    def _keep_reachable(self):
        """Pull title bars back where the mouse can grab them (off an edge, under the taskbar, on a monitor that is
        gone). The stack moves as one and always ends with the main window reachable; Albums moves with it when
        docked, else on its own."""
        if not self._main_alive():
            return
        self.main.update_idletasks()
        areas = work_areas(self.main)
        grip = 14 * self.scale()

        def pull(r):
            """Partly on a monitor: just enough to grab the title bar. On none at all (a monitor that is gone,
            a stack slid under the taskbar): the whole window back on screen."""
            if _overlap(r, _area_of(r, areas)) > 0:
                return skin_math.reachable(tuple(r), areas, grip)
            return skin_math.reachable(tuple(r), areas, grip=r[3], keep=r[2])

        mx, my, mw, mh = self._rect(self.main)
        stack = self._stack_rects((mx, my))
        docked = self._albums_docked(stack)
        albums = self._rect(self.albums) if self._alive(self.albums) else None
        dx = dy = 0
        for r in stack + ([albums] if docked else []):    # the stack moves by the biggest pull any part needs
            nx, ny = pull(r)
            if abs(nx - r[0]) > abs(dx):
                dx = nx - r[0]
            if abs(ny - r[1]) > abs(dy):
                dy = ny - r[1]
        # ... but the main window must end up reachable (a stack taller than the screen keeps its top on screen)
        nx, ny = pull((mx + dx, my + dy, mw, mh))
        dx, dy = nx - mx, ny - my
        changed = False
        if (dx, dy) != (0, 0):
            self._main_pos = (nx, ny)      # moved here with its stack: <Configure> must not move Albums again
            self.main.geometry(f'+{nx}+{ny}')
            self.settings['positions']['main'] = [nx, ny]
            changed = True
        self._layout_stack((nx, ny))
        if albums is not None:
            ax, ay = pull((albums[0] + (dx if docked else 0), albums[1] + (dy if docked else 0),
                           albums[2], albums[3]))
            if (ax, ay) != albums[:2]:
                self.albums.geometry(f'+{ax}+{ay}')
                self.settings['positions']['albums'] = [ax, ay]
                changed = True
        if changed:
            self.main.update_idletasks()
            self._main_pos = (self.main.winfo_x(), self.main.winfo_y())   # where it really is, moved or not
            self.save()

    def _on_main_configure(self, event):
        """The stack follows the main window (and its size); a docked Albums window moves with it too."""
        if event.widget is not self.main:
            return
        new = (self.main.winfo_x(), self.main.winfo_y())
        old = self._main_pos
        self._main_pos = new
        if old is not None and old != new and self._albums_docked(self._stack_rects(old)):
            ax, ay = self.albums.winfo_x(), self.albums.winfo_y()
            self.albums.geometry(f'+{ax + new[0] - old[0]}+{ay + new[1] - old[1]}')
        self._layout_stack(new)
