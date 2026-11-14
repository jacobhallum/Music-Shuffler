"""
sound_ui.py - two small Tkinter windows for the music player's sound settings.

SoundDialog:      song transitions (off / crossfade / smart) + loudness leveling.
EqualizerWindow:  Winamp-style 10-band equalizer with preamp and presets.

The windows never save anything: every user change is reported through the
`on_change` callback; the app saves and applies it.
"""

import tkinter as tk
import tkinter.ttk as ttk
from tkinter import simpledialog
from typing import Callable, Dict, Tuple

TRANSITION_MODES = ('off', 'crossfade', 'smart')
MIN_TRANSITION_SEC = 1
MAX_TRANSITION_SEC = 12
EQ_DB_LIMIT = 12.0
EQ_BAND_COUNT = 10
EQ_COL_LABELS = ('Pre', '60', '170', '310', '600', '1k', '3k', '6k', '12k', '14k', '16k')

ChangeCallback = Callable[[dict], None]
ScanStatusCallback = Callable[[], str]
SavePresetCallback = Callable[[str], None]
ImportEqfCallback = Callable[[], None]


class SoundDialog(tk.Toplevel):
    """Song transitions and loudness leveling."""

    def __init__(self, parent, settings: dict, on_change: ChangeCallback,
                 scan_status: ScanStatusCallback):
        super().__init__(parent)
        self.title('Sound')
        self.transient(parent)
        self.resizable(False, False)
        try:
            self.configure(bg=parent.cget('bg'))
        except tk.TclError:
            pass

        self._on_change_cb = on_change
        self._scan_status = scan_status
        self._settings = dict(settings)
        self._settings['eq_bands_db'] = list(settings.get('eq_bands_db', []))
        self._updating = False
        self._after_id = None

        body = ttk.Frame(self, padding=12)
        body.pack(fill='both', expand=True)
        body.columnconfigure(1, weight=1)

        # --- Song transitions -------------------------------------------------
        ttk.Label(body, text='Song transitions').grid(
            row=0, column=0, sticky='w', padx=(0, 12), pady=2)
        self._mode_var = tk.StringVar(value=str(self._settings.get('transition_mode', 'off')))
        radio_box = ttk.Frame(body)
        radio_box.grid(row=0, column=1, sticky='w', pady=2)
        for text, value in (('Off', 'off'), ('Crossfade', 'crossfade'), ('Smart', 'smart')):
            ttk.Radiobutton(radio_box, text=text, value=value, variable=self._mode_var,
                            command=self._on_mode_changed).pack(side='left', padx=(0, 12))

        # --- Length -----------------------------------------------------------
        ttk.Label(body, text='Length').grid(
            row=1, column=0, sticky='w', padx=(0, 12), pady=2)
        self._seconds_var = tk.StringVar(
            value=f"{int(self._settings.get('transition_seconds', 6))} s")
        self._scale = ttk.Scale(body, orient='horizontal', from_=MIN_TRANSITION_SEC,
                                to=MAX_TRANSITION_SEC, length=220,
                                command=self._on_scale_changed)
        self._scale.grid(row=1, column=1, sticky='w', pady=2)
        ttk.Label(body, textvariable=self._seconds_var).grid(
            row=1, column=2, sticky='w', padx=(8, 0), pady=2)

        # --- Loudness leveling ------------------------------------------------
        ttk.Label(body, text='Loudness leveling').grid(
            row=2, column=0, sticky='w', padx=(0, 12), pady=2)
        self._leveling_var = tk.BooleanVar(
            value=bool(self._settings.get('leveling_enabled', False)))
        ttk.Checkbutton(body, text='On', variable=self._leveling_var,
                        command=self._on_leveling_changed).grid(
            row=2, column=1, sticky='w', pady=2)

        # --- Scan status ------------------------------------------------------
        self._status_var = tk.StringVar(value='')
        ttk.Label(body, textvariable=self._status_var).grid(
            row=3, column=1, sticky='w', pady=(2, 2))

        ttk.Button(body, text='Close', command=self.destroy).grid(
            row=4, column=1, sticky='e', pady=(8, 0))

        # The title-bar X would otherwise run Tcl's destroy and skip our
        # override, leaving the status timer running.
        self.protocol('WM_DELETE_WINDOW', self.destroy)

        self._load_from_settings()
        self._set_status_text()
        self._after_id = self.after(1000, self._poll_status)

    # ------------------------------------------------------------------ helpers

    def _emit(self) -> None:
        out = dict(self._settings)
        out['eq_bands_db'] = list(self._settings['eq_bands_db'])
        self._on_change_cb(out)

    def _load_from_settings(self) -> None:
        self._updating = True
        try:
            seconds = int(self._settings.get('transition_seconds', 6))
            self._scale.set(float(seconds))
            self._seconds_var.set(f'{seconds} s')
            self._mode_var.set(str(self._settings.get('transition_mode', 'off')))
            self._update_scale_state()
        finally:
            self._updating = False

    def _update_scale_state(self) -> None:
        if self._settings.get('transition_mode', 'off') == 'off':
            self._scale.state(['disabled'])
        else:
            self._scale.state(['!disabled'])

    def _set_status_text(self) -> None:
        try:
            text = self._scan_status() or ''
        except Exception:
            text = ''
        self._status_var.set(text)

    # ------------------------------------------------------------- callbacks

    def _on_mode_changed(self) -> None:
        if self._updating:
            return
        self._settings['transition_mode'] = self._mode_var.get()
        self._update_scale_state()
        self._emit()

    def _on_scale_changed(self, value) -> None:
        if self._updating:
            return
        seconds = int(round(float(value)))
        if seconds != self._settings.get('transition_seconds'):
            self._settings['transition_seconds'] = seconds
            self._seconds_var.set(f'{seconds} s')
            self._emit()

    def _on_leveling_changed(self) -> None:
        if self._updating:
            return
        self._settings['leveling_enabled'] = bool(self._leveling_var.get())
        self._emit()

    def _poll_status(self) -> None:
        # A callback still queued when the window was destroyed outside our
        # destroy() override must not reschedule itself.
        try:
            alive = bool(self.winfo_exists())
        except tk.TclError:
            alive = False
        if not alive:
            self._after_id = None
            return
        self._set_status_text()
        self._after_id = self.after(1000, self._poll_status)

    def destroy(self) -> None:
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        super().destroy()


class EqualizerWindow(tk.Toplevel):
    """Winamp-style 10-band equalizer with preamp."""

    def __init__(self, parent, settings: dict, presets: Dict[str, Tuple[list, float]],
                 on_change: ChangeCallback, on_save_preset: SavePresetCallback,
                 on_import_eqf: ImportEqfCallback):
        super().__init__(parent)
        self.title('Equalizer')
        self.transient(parent)
        self.resizable(False, False)
        try:
            self.configure(bg=parent.cget('bg'))
        except tk.TclError:
            pass

        self._on_change_cb = on_change
        self._on_save_preset_cb = on_save_preset
        self._on_import_eqf_cb = on_import_eqf
        self._settings = dict(settings)
        self._settings['eq_bands_db'] = list(settings.get('eq_bands_db', []))
        self._presets = dict(presets)
        self._updating = False

        body = ttk.Frame(self, padding=12)
        body.pack(fill='both', expand=True)

        # --- Top row: enable, preset chooser, save / import --------------------
        top = ttk.Frame(body)
        top.grid(row=0, column=0, columnspan=len(EQ_COL_LABELS) + 1, sticky='w',
                 pady=(0, 8))
        self._enabled_var = tk.BooleanVar(
            value=bool(self._settings.get('eq_enabled', False)))
        ttk.Checkbutton(top, text='On', variable=self._enabled_var,
                        command=self._on_enabled_changed).pack(side='left', padx=(0, 12))
        ttk.Label(top, text='Preset').pack(side='left', padx=(0, 6))
        self._preset_var = tk.StringVar(value='Custom')
        self._preset_box = ttk.Combobox(top, textvariable=self._preset_var,
                                        state='readonly', width=28)
        self._preset_box['values'] = list(self._presets.keys())
        self._preset_box.pack(side='left', padx=(0, 12))
        self._preset_box.bind('<<ComboboxSelected>>', self._on_preset_selected)
        ttk.Button(top, text='Save...', command=self._on_save_clicked).pack(
            side='left', padx=(0, 6))
        ttk.Button(top, text='Import .eqf...', command=self._on_import_clicked).pack(
            side='left')

        # --- Slider area -------------------------------------------------------
        self._sliders = {}   # 'pre' / 0..9 -> ttk.Scale
        self._value_vars = {}  # 'pre' / 0..9 -> tk.StringVar (value display)

        ttk.Label(body, text='+12 dB').grid(row=1, column=0, sticky='w', padx=(0, 4))

        for col, label in enumerate(EQ_COL_LABELS, start=1):
            padx = (0, 8) if col == 1 else 2
            ttk.Label(body, text=label).grid(row=2, column=col, padx=padx)

        for col, label in enumerate(EQ_COL_LABELS, start=1):
            key = 'pre' if col == 1 else col - 2
            padx = (0, 8) if col == 1 else 2
            self._value_vars[key] = tk.StringVar(value='0.0')
            slider = ttk.Scale(body, orient='vertical', from_=EQ_DB_LIMIT,
                               to=-EQ_DB_LIMIT, length=160,
                               command=(lambda value, k=key: self._on_slider(k, value)))
            slider.grid(row=3, column=col, padx=padx)
            self._sliders[key] = slider
            ttk.Label(body, textvariable=self._value_vars[key]).grid(
                row=4, column=col, padx=padx)

        ttk.Label(body, text='-12 dB').grid(row=5, column=0, sticky='w', padx=(0, 4))

        self._load_from_settings()

    # ------------------------------------------------------------------ helpers

    def _emit(self) -> None:
        out = dict(self._settings)
        out['eq_bands_db'] = list(self._settings['eq_bands_db'])
        self._on_change_cb(out)

    def _show_preset_name(self) -> None:
        name = self._settings.get('eq_preset', '')
        if name in self._presets:
            self._preset_var.set(name)
        else:
            self._preset_var.set('Custom')

    def _apply_values_to_widgets(self) -> None:
        self._updating = True
        try:
            self._enabled_var.set(bool(self._settings.get('eq_enabled', False)))
            self._show_preset_name()
            pre = float(self._settings.get('eq_preamp_db', 0.0))
            self._sliders['pre'].set(pre)
            self._value_vars['pre'].set(f'{pre:.1f}')
            bands = self._settings.get('eq_bands_db', [])
            for i in range(EQ_BAND_COUNT):
                db = float(bands[i]) if i < len(bands) else 0.0
                self._sliders[i].set(db)
                self._value_vars[i].set(f'{db:.1f}')
        finally:
            self._updating = False

    def _load_from_settings(self) -> None:
        self._apply_values_to_widgets()

    # ------------------------------------------------------------- callbacks

    def _on_enabled_changed(self) -> None:
        if self._updating:
            return
        self._settings['eq_enabled'] = bool(self._enabled_var.get())
        self._emit()

    def _on_preset_selected(self, event=None) -> None:
        if self._updating:
            return
        name = self._preset_var.get()
        if name not in self._presets:
            return
        bands, preamp = self._presets[name]
        self._settings['eq_preset'] = name
        self._settings['eq_bands_db'] = [float(v) for v in bands]
        self._settings['eq_preamp_db'] = float(preamp)
        self._apply_values_to_widgets()
        self._emit()

    def _on_slider(self, key, raw_value) -> None:
        if self._updating:
            return
        try:
            value = round(float(raw_value), 1)
        except (TypeError, ValueError):
            return
        self._value_vars[key].set(f'{value:.1f}')
        if key == 'pre':
            if value == self._settings.get('eq_preamp_db'):
                return
            self._settings['eq_preamp_db'] = value
        else:
            bands = self._settings['eq_bands_db']
            if key >= len(bands) or value == bands[key]:
                return
            bands[key] = value
        self._settings['eq_preset'] = ''
        self._preset_var.set('Custom')
        self._emit()

    def _on_save_clicked(self) -> None:
        name = simpledialog.askstring('Save preset', 'Preset name:', parent=self)
        if isinstance(name, str) and name.strip():
            self._on_save_preset_cb(name.strip()[:64])

    def _on_import_clicked(self) -> None:
        self._on_import_eqf_cb()

    # ------------------------------------------------------------------ public

    def refresh(self, settings: dict, presets: Dict[str, Tuple[list, float]]) -> None:
        self._settings = dict(settings)
        self._settings['eq_bands_db'] = list(settings.get('eq_bands_db', []))
        self._presets = dict(presets)
        self._preset_box['values'] = list(self._presets.keys())
        self._apply_values_to_widgets()
