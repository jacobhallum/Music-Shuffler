"""
skin_mode.py - runs the player in a classic Winamp skin.

SkinController owns the skinned windows (main, EQ, playlist, Albums) and the saved choice in ui_settings.json. When a
skin is picked it is saved and the default view (the root window) is withdrawn; the root keeps its title, media-key
hook and single-instance mutex, so media keys and a second launch still reach the player (a second launch that shows
the root is turned back into raising the skinned main window). Picking "No skin", or a saved skin that is missing or
broken at startup, brings the default view back.

It talks to the player only through MusicPlayerGUI's existing methods and state.
"""

import array
import collections
import ctypes
import json
import os
import tkinter as tk
from tkinter import messagebox, ttk
from types import SimpleNamespace

import skin as skin_lib
import skin_layout
import skin_math
from albums_browser import AlbumsBrowser
from skin_playlist import SkinPlaylistWindow
from skin_ui import SkinEqWindow, SkinMainWindow

REFRESH_MS = 250
VIS_MS = 33
VIS_MODES = ('spectrum', 'scope', 'off')
SAMPLE_RATE = 48000          # the engine's output format: 48 kHz stereo float32
DEFAULTS = {'skin': None, 'double_size': False, 'always_on_top': False, 'vis_mode': 'spectrum',
            'eq_open': True, 'pl_open': True, 'positions': {}, 'pl_height': 232}


def load_settings(path):
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    out = dict(DEFAULTS)
    out['positions'] = {}
    if isinstance(data, dict):
        for k in DEFAULTS:
            if k in data and isinstance(data[k], type(DEFAULTS[k]) if DEFAULTS[k] is not None else (str, type(None))):
                out[k] = data[k]
    if out['vis_mode'] not in VIS_MODES:
        out['vis_mode'] = 'spectrum'
    return out


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
        hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW)
        win.withdraw()
        win.after(10, win.deiconify)
    except Exception as e:
        print(f"⚠ Could not give the skinned window a taskbar button: {e}")


def minimize_window(win):
    try:
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        ctypes.windll.user32.ShowWindow(hwnd, 6)   # SW_MINIMIZE
    except AttributeError:
        win.withdraw()


class SkinFrameWindow(SkinPlaylistWindow):
    """A window in the skin's playlist frame with arbitrary Tk content in self.body (used for Albums)."""

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
        self._jobs = []
        self._last_status = None
        self._last_icon = None
        self._last_rows = None
        self._pl_query = ''
        self._main_pos = None

    # ---- choosing a skin ---------------------------------------------------------

    @property
    def active(self):
        return self.main is not None

    def scale(self):
        return 2 if self.settings['double_size'] else 1

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
            self.enter(skin_lib.load_skin(path))
        except skin_lib.SkinError as e:
            self.p.status_label.config(text=f"Couldn't load skin {name}: {e} - default view")

    def pick(self, name):
        """Menu: a skin file name, or None for the default view."""
        if name is None:
            self.settings['skin'] = None
            self.save()
            self.leave()
            return
        try:
            loaded = skin_lib.load_skin(os.path.join(self.skins_dir, name))
        except skin_lib.SkinError as e:
            messagebox.showwarning("Skin", f"Couldn't load {name}: {e}")
            return
        self.settings['skin'] = name
        self.save()
        if self.active:
            self._remember_positions()
            self._close_windows()
        self.enter(loaded)

    def open_folder(self):
        os.makedirs(self.skins_dir, exist_ok=True)
        try:
            os.startfile(self.skins_dir)
        except (AttributeError, OSError) as e:
            messagebox.showinfo("Skins", f"Put .wsz skins in:\n{self.skins_dir}\n\n({e})")

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
        self.skin = loaded
        p, s = self.p, self.scale()
        self._apply_ttk_colors()
        pos = self.settings['positions']
        mx, my = pos.get('main', (200, 200))
        self.main = SkinMainWindow(p.root, loaded, s, self._main_callbacks())
        self.main.title("Music Player V2 (skin)")
        self.main.geometry(f'+{mx}+{my}')
        self.main.bind('<Configure>', self._on_main_configure, add='+')
        if self.settings['eq_open']:
            self._open_eq()
        if self.settings['pl_open']:
            self._open_pl()
        self._apply_topmost()
        p.root.withdraw()
        p.root.bind('<Map>', self._on_root_mapped, add='+')
        set_app_window(self.main)
        if p._engine is not None:
            p._engine.vis_tap = self._chunks.append
        self._last_icon = None
        self._schedule(REFRESH_MS, self._refresh)
        self._schedule(VIS_MS, self._vis_tick)
        self._refresh()

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
        for job in self._jobs:
            try:
                self.p.root.after_cancel(job)
            except (tk.TclError, ValueError):
                pass
        self._jobs.clear()
        for name in ('albums', 'pl', 'eq', 'main'):
            w = getattr(self, name)
            if w is not None:
                if name == 'albums' and w in getattr(self.p, '_album_browsers', []):
                    pass
                try:
                    w.destroy()
                except tk.TclError:
                    pass
                setattr(self, name, None)
        browsers = getattr(self.p, '_album_browsers', None)
        if browsers is not None:
            self.p._album_browsers = [b for b in browsers if b is self.p.albums_browser]
        self._last_rows = None

    def save(self):
        try:
            save_settings(self.settings_path, self.settings)
        except OSError as e:
            print(f"⚠ Could not save ui_settings.json: {e}")

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

    def _on_root_mapped(self, event):
        """A second launch shows the root window: while skinned, hide it again and raise the skin instead."""
        if event.widget is self.p.root and self.active:
            self.p.root.after(1, self._raise_skin)

    def _raise_skin(self):
        if not self.active:
            return
        self.p.root.withdraw()
        self.main.deiconify()
        self.main.lift()
        self.main.focus_force()

    def _schedule(self, ms, fn):
        def run():
            if self.active:
                fn()
                self._jobs.append(self.p.root.after(ms, run))
        self._jobs.append(self.p.root.after(ms, run))

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
            'heart': p.toggle_loved, 'clutter_a': self.toggle_topmost, 'clutter_d': self.toggle_double,
            'clutter_i': self.song_info, 'clutter_v': self.cycle_vis, 'vis_mode': self.cycle_vis,
            'moved': lambda x, y: self._snap(self.main, 'main'),
        }

    def _open_eq(self):
        s = self.scale()
        x, y = self._place('eq', 116 * s)
        self.eq = SkinEqWindow(self.p.root, self.skin, s, {
            'change': self._eq_change, 'presets': self._eq_presets, 'close': self.toggle_eq,
            'menu': self.show_menu, 'moved': lambda x, y: self._snap(self.eq, 'eq')})
        self.eq.geometry(f'+{x}+{y}')
        self._sync_eq()

    def _open_pl(self):
        s = self.scale()
        below = 116 * s * (2 if self.eq is not None else 1)
        x, y = self._place('pl', below)
        self.pl = SkinPlaylistWindow(self.p.root, self.skin, s, {
            'play': self._pl_play, 'search': self._pl_search, 'search_enter': self._pl_enter,
            'menu': self._pl_menu, 'close': self.toggle_pl, 'moved': lambda x, y: self._snap(self.pl, 'pl'),
            'resized': lambda h: self.settings.__setitem__('pl_height', h)}, height=self.settings['pl_height'])
        self.pl.geometry(f'+{x}+{y}')
        self._last_rows = None

    def _place(self, name, below):
        pos = self.settings['positions'].get(name)
        if pos:
            return pos
        self.main.update_idletasks()
        return self.main.winfo_x(), self.main.winfo_y() + below

    def toggle_eq(self):
        if self.eq is not None:
            self.settings['positions']['eq'] = [self.eq.winfo_x(), self.eq.winfo_y()]
            self.eq.destroy()
            self.eq = None
        else:
            self._open_eq()
            self._apply_topmost()
        self.settings['eq_open'] = self.eq is not None
        self.save()

    def toggle_pl(self):
        if self.pl is not None:
            self.settings['positions']['pl'] = [self.pl.winfo_x(), self.pl.winfo_y()]
            self.pl.destroy()
            self.pl = None
        else:
            self._open_pl()
            self._apply_topmost()
        self.settings['pl_open'] = self.pl is not None
        self.save()

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

    def toggle_topmost(self):
        self.settings['always_on_top'] = not self.settings['always_on_top']
        self._apply_topmost()
        self.save()

    def _apply_topmost(self):
        on = self.settings['always_on_top']
        for w in (self.main, self.eq, self.pl, self.albums):
            if w is not None:
                w.attributes('-topmost', on)

    def toggle_double(self):
        self._remember_positions()
        self.settings['double_size'] = not self.settings['double_size']
        s = self.scale()
        old = 1 if s == 2 else 2
        mx, my = self.main.winfo_x(), self.main.winfo_y()
        for w in (self.main, self.eq, self.pl):
            if w is not None:
                docked_below = w is not self.main and w.winfo_x() == mx
                w.set_skin(self.skin, s)
                if docked_below:   # keep a stack under the main window a stack
                    rel = (w.winfo_y() - my) // old
                    w.geometry(f'+{mx}+{my + rel * s}')
        if self.albums is not None:
            self.albums.set_skin(self.skin, s)
        self.save()
        self._refresh()

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
        menu.add_checkbutton(label="Playlist", command=self.toggle_pl,
                             variable=tk.BooleanVar(menu, value=self.pl is not None))
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
        menu.add_checkbutton(label="Double size", command=self.toggle_double,
                             variable=tk.BooleanVar(menu, value=self.settings['double_size']))
        menu.add_separator()
        menu.add_command(label="Exit", command=p._on_close)
        try:
            menu.tk_popup(x_root, y_root)
        finally:
            menu.grab_release()

    def _pl_menu(self, x_root, y_root, index):
        p = self.p
        if index is None or not (0 <= index < len(self._pl_tracks())):
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
            double_size=self.settings['double_size'])
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
        screen = (0, 0, win.winfo_screenwidth(), win.winfo_screenheight())
        x, y = skin_math.snap(self._rect(win), others, screen)
        win.geometry(f'+{x}+{y}')
        self.settings['positions'][name] = [x, y]
        if win is self.main:
            self._main_pos = (x, y)
        self.save()

    def _on_main_configure(self, event):
        """Windows docked to the main window move with it."""
        if event.widget is not self.main:
            return
        new = (self.main.winfo_x(), self.main.winfo_y())
        old = self._main_pos
        self._main_pos = new
        if old is None or old == new:
            return
        dx, dy = new[0] - old[0], new[1] - old[1]
        rects = [(old[0], old[1], self.main.winfo_width(), self.main.winfo_height())]
        others = [w for w in self._windows() if w is not self.main]
        rects += [self._rect(w) for w in others]
        group = skin_math.dock_group(0, rects)
        for i, w in enumerate(others, start=1):
            if i in group:
                w.geometry(f'+{w.winfo_x() + dx}+{w.winfo_y() + dy}')
