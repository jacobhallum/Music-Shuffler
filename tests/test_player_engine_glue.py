"""
tests/test_player_engine_glue.py - how MusicPlayerGUI reacts to sound-engine events, with no Tk or audio.

The real MusicPlayerGUI methods are bound onto a small fake (same pattern as tests/test_player_shuffle.py).

Run with:
    python -m unittest tests.test_player_engine_glue -v
"""

import os
import queue
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# music_player prints ✓/⚠ status lines; a cp1252 console would raise on them.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from audio_engine import PlayItem  # noqa: E402
from music_player import MusicPlayerGUI  # noqa: E402


class _Track:
    def __init__(self, path):
        self.filepath = path
        self.title = os.path.basename(path)
        self.duration = 200.0


class _FakePlayer:
    def __init__(self, n=3):
        self.current_playlist = [_Track(f'/m/t{i}.mp3') for i in range(n)]
        self.current_index = 0
        self.is_playing = True
        self._playback_state = 'playing'
        self.calls = []
        self._engine_item = PlayItem(key=self.current_playlist[0], path='/m/t0.mp3', duration_sec=200.0)
        self._next_item = None
        self._outgoing_item = None

    # stand-ins for the parts of the GUI these handlers touch
    def _begin_track_bookkeeping(self, track):
        self.calls.append(('bookkeeping', track.filepath))

    def _finalize_playback(self, reason):
        self.calls.append(('finalize', reason))

    def next_track(self, _internal=False):
        self.calls.append(('next', _internal))

    def _prepare_next(self):
        pass

    _on_engine_event = MusicPlayerGUI._on_engine_event
    _handle_track_end = MusicPlayerGUI._handle_track_end


class EngineEventGlueTests(unittest.TestCase):
    def test_started_cut_runs_bookkeeping_once(self):
        p = _FakePlayer()
        p._on_engine_event('started', item=p._engine_item, how='cut')
        self.assertEqual(p.calls, [('bookkeeping', '/m/t0.mp3')])

    def test_finished_natural_end_for_current_item_advances(self):
        p = _FakePlayer()
        p._on_engine_event('finished', item=p._engine_item, reason='natural_end', continued=False)
        self.assertEqual(p.calls, [('finalize', 'natural_end'), ('next', True)])
        self.assertEqual(p._playback_state, 'stopped')

    def test_stale_finished_is_ignored(self):
        p = _FakePlayer()
        old = PlayItem(key=p.current_playlist[1], path='/m/t1.mp3', duration_sec=200.0)
        p._on_engine_event('finished', item=old, reason='natural_end', continued=False)
        self.assertEqual(p.calls, [])

    def test_failure_finish_skips_like_today(self):
        p = _FakePlayer()
        p._on_engine_event('error', item=p._engine_item, message='Invalid data found')
        p._on_engine_event('finished', item=p._engine_item, reason='failure', continued=False)
        self.assertEqual(p.calls, [('finalize', 'failure'), ('next', True)])

    def test_failure_resumes_the_loudness_scan(self):
        # play_track_at_index paused the scan; a song that never starts must not leave it paused.
        p = _FakePlayer()
        resumed = []

        class Svc:
            def resume_background(self):
                resumed.append(1)

        p._loudness = Svc()
        p._on_engine_event('finished', item=p._engine_item, reason='failure', continued=False)
        self.assertEqual(resumed, [1])

    def test_cut_finish_does_nothing(self):
        p = _FakePlayer()
        p._on_engine_event('finished', item=p._engine_item, reason='cut', continued=False)
        self.assertEqual(p.calls, [])


class _TransitionFake(_FakePlayer):
    """Adds the queue state and the second tracker slot that transitions need."""

    def __init__(self, n=3):
        super().__init__(n)
        self.queued_count = 0
        self.is_temp_view = False
        self._active_algo = 'Smart Shuffle'
        self._pb_tracker, self._pb_track = 'T0', self.current_playlist[0]
        self._pb_outgoing = None
        self._outgoing_item = None
        self._outgoing_tr = 'cut'
        self._next_item = PlayItem(key=self.current_playlist[1], path='/m/t1.mp3', duration_sec=200.0)
        self._next_tr = 'crossfade'

    def _begin_track_bookkeeping(self, track):
        self.calls.append(('bookkeeping', track.filepath))
        self._pb_tracker, self._pb_track = 'T-' + track.filepath, track

    def _finalize_playback(self, reason, transition='cut'):
        self.calls.append(('finalize', reason, transition, self._pb_tracker))
        self._pb_tracker = self._pb_track = None

    def _prepare_next(self):
        self.calls.append(('prepare',))

    _on_transition_started = MusicPlayerGUI._on_transition_started
    _finalize_outgoing = MusicPlayerGUI._finalize_outgoing


class TransitionGlueTests(unittest.TestCase):
    def test_start_of_a_superseded_prepared_item_still_transitions(self):
        # A re-prepare can land between the engine starting the song and the main thread handling it.
        p = _TransitionFake()
        started = p._next_item
        p._next_item = PlayItem(key=started.key, path=started.path, duration_sec=200.0)
        p._on_engine_event('started', item=started, how='overlap')
        self.assertEqual(p.current_index, 1)
        self.assertIs(p._engine_item, started)
        self.assertIn(('bookkeeping', '/m/t1.mp3'), p.calls)

    def test_failed_song_that_continued_is_a_failure(self):
        # The decoder failed mid-song and the engine went straight on to the prepared one.
        p = _TransitionFake()
        p._on_engine_event('finished', item=p._engine_item, reason='failure', continued=True)
        self.assertEqual(p.calls, [('finalize', 'failure', 'cut', 'T0')])

    def test_transition_start_makes_incoming_current(self):
        p = _TransitionFake()
        inc = p._next_item
        p._on_engine_event('started', item=inc, how='overlap')
        self.assertEqual(p.current_index, 1)
        self.assertIs(p._engine_item, inc)
        self.assertEqual(p._pb_outgoing, ('T0', p.current_playlist[0]))
        self.assertEqual(p.calls, [('bookkeeping', '/m/t1.mp3'), ('prepare',)])

    def test_outgoing_finalized_when_its_audio_stops(self):
        p = _TransitionFake()
        out = p._engine_item
        p._on_engine_event('started', item=p._next_item, how='overlap')
        p._on_engine_event('finished', item=out, reason='natural_end', continued=True)
        self.assertEqual(p.calls[-1], ('finalize', 'natural_end', 'crossfade', 'T0'))
        self.assertEqual(p._pb_tracker, 'T-/m/t1.mp3')   # the incoming song's tracker is untouched
        self.assertIsNone(p._pb_outgoing)

    def test_gapless_finish_then_start(self):
        p = _TransitionFake()
        p._next_tr = 'gapless'
        p._on_engine_event('finished', item=p._engine_item, reason='natural_end', continued=True)
        p._on_engine_event('started', item=p._next_item, how='gapless')
        self.assertEqual(p.calls[0], ('finalize', 'natural_end', 'gapless', 'T0'))
        self.assertEqual(p.current_index, 1)
        self.assertIsNone(p._pb_outgoing)

    def test_stale_transition_start_is_ignored(self):
        p = _TransitionFake()
        other = PlayItem(key=p.current_playlist[2], path='/m/t2.mp3', duration_sec=200.0)
        p._on_engine_event('started', item=other, how='overlap')
        self.assertEqual((p.current_index, p.calls), (0, []))


class _Engine:
    def __init__(self):
        self.prepared = []

    def prepare_next(self, item, plan):
        self.prepared.append((item, plan))


class PrepareNextTests(unittest.TestCase):
    def _fake(self, index):
        import threading
        p = _FakePlayer(3)
        p.current_index = index
        p.queued_count = 0
        p.is_temp_view = False
        p._active_algo = 'Smart Shuffle'
        p._engine = _Engine()
        p._engine_lock = threading.RLock()
        p._engine_item = PlayItem(key=p.current_playlist[index], path=p.current_playlist[index].filepath, duration_sec=200.0)
        p.audio_settings = {'transition_mode': 'crossfade', 'transition_seconds': 6, 'eq_enabled': False,
                            'eq_bands_db': [0.0] * 10, 'eq_preamp_db': 0.0, 'leveling_enabled': False}
        for t in p.current_playlist:
            t.track_number = None
        p._loudness_for = lambda track: None
        p._filters_for = lambda track: ''
        p._play_item_for = lambda track: MusicPlayerGUI._play_item_for(p, track)
        p._prepare_next = lambda **kw: MusicPlayerGUI._prepare_next(p, **kw)
        return p

    def test_album_tracks_fade_like_any_other(self):
        # Decided 2026-11-13: a fade mode fades every natural end, consecutive album tracks included.
        p = self._fake(0)
        p.current_playlist[0].track_number, p.current_playlist[1].track_number = '1', '2'
        p._prepare_next()
        item, plan = p._engine.prepared[-1]
        self.assertIs(item.key, p.current_playlist[1])
        self.assertEqual((plan.kind, plan.overlap_sec), ('overlap', 6))
        self.assertEqual(p._next_tr, 'crossfade')

    def test_unchanged_next_song_keeps_its_prepared_item(self):
        # The engine matches by identity: a fresh PlayItem would drop its pre-opened stream.
        p = self._fake(0)
        p._prepare_next()
        p._prepare_next()
        self.assertIs(p._engine.prepared[0][0], p._engine.prepared[1][0])

    def test_missing_loudness_is_analysed_once_per_prepare(self):
        # An unreadable next song never gets loudness: re-preparing must not re-analyse it forever.
        p = self._fake(0)
        p.audio_settings['leveling_enabled'] = True

        class Svc:
            def get(self, path):
                return None

            def known_bad(self, path):
                return False

        p._loudness = Svc()
        runs = []

        def analyze_then(tracks, callback):
            runs.append([t.filepath for t in tracks])
            if len(runs) < 5:
                callback()

        p._analyze_then = analyze_then
        p._prepare_next()
        self.assertEqual(len(runs), 1)
        self.assertIs(p._engine.prepared[-1][0].key, p.current_playlist[1])  # still prepared, unlevelled

    def test_prepares_next_track_with_plan(self):
        p = self._fake(0)
        p._prepare_next()
        item, plan = p._engine.prepared[-1]
        self.assertIs(item.key, p.current_playlist[1])
        self.assertEqual((plan.kind, plan.overlap_sec), ('overlap', 6))
        self.assertEqual(p._next_tr, 'crossfade')

    def test_reshuffle_wrap_prepares_nothing(self):
        p = self._fake(2)
        p._prepare_next()
        self.assertEqual(p._engine.prepared[-1], (None, None))
        self.assertIsNone(p._next_item)


class LevelingGlueTests(unittest.TestCase):
    def test_filters_include_leveling_gain(self):
        from loudness import TrackLoudness

        class P:
            _filters_for = MusicPlayerGUI._filters_for
            _album_gain_for = MusicPlayerGUI._album_gain_for
            _context_album_for = MusicPlayerGUI._context_album_for

        p = P()
        p.audio_settings = {'eq_enabled': False, 'eq_bands_db': [0.0] * 10, 'eq_preamp_db': 0.0,
                            'leveling_enabled': True}
        p._loudness_for = lambda track: TrackLoudness(-9.4, -0.5, 200.0, (), ())
        self.assertEqual(p._filters_for(_Track('/m/x.mp3')), 'volume=-4.60dB')
        p.audio_settings['leveling_enabled'] = False
        self.assertEqual(p._filters_for(_Track('/m/x.mp3')), '')


class _Root:
    def __init__(self):
        self.jobs, self.n = {}, 0

    def after(self, ms, fn):
        self.n += 1
        self.jobs[self.n] = fn
        return self.n

    def after_cancel(self, job):
        self.jobs.pop(job, None)

    def run_all(self):
        jobs, self.jobs = self.jobs, {}
        for fn in jobs.values():
            fn()


class _RefilterEngine:
    def __init__(self):
        self.refilters = 0

    def refilter(self, filters_for):
        self.refilters += 1


class SoundSettingsGlueTests(unittest.TestCase):
    def _fake(self, d):
        import threading
        import audio_settings

        class P:
            _apply_sound_settings = MusicPlayerGUI._apply_sound_settings
            _commit_sound_settings = MusicPlayerGUI._commit_sound_settings
            _import_eqf_file = MusicPlayerGUI._import_eqf_file

        p = P()
        p.root = _Root()
        p.audio_settings = audio_settings.normalize(None)
        p.audio_settings_file = os.path.join(d, 'audio_settings.json')
        p._sound_apply_job = None
        p._engine = _RefilterEngine()
        p._engine_lock = threading.RLock()
        p.prepared = 0
        p._prepare_next = lambda: setattr(p, 'prepared', p.prepared + 1)
        p._start_loudness_scan = lambda: None
        p._filters_for = lambda track: ''
        return p

    def test_eq_change_debounced_then_refilters_once(self):
        import tempfile
        import audio_settings
        with tempfile.TemporaryDirectory() as d:
            p = self._fake(d)
            for g in (1.0, 2.0, 3.0):
                s = dict(p.audio_settings)
                s['eq_enabled'], s['eq_bands_db'] = True, [g] + [0.0] * 9
                p._apply_sound_settings(s)
            self.assertEqual(len(p.root.jobs), 1)
            p.root.run_all()
            self.assertEqual(p._engine.refilters, 1)
            self.assertEqual(audio_settings.load(p.audio_settings_file)['eq_bands_db'][0], 3.0)

    def test_transition_change_reprepares_next(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = self._fake(d)
            s = dict(p.audio_settings)
            s['transition_mode'] = 'crossfade'
            p._apply_sound_settings(s)
            p.root.run_all()
            self.assertEqual((p.prepared, p._engine.refilters), (1, 0))

    def test_windows_change_only_their_own_settings(self):
        # Sound and Equalizer both open: each emits its whole snapshot from when it opened.
        import tempfile
        import music_player
        with tempfile.TemporaryDirectory() as d:
            p = self._fake(d)
            stale = dict(p.audio_settings)
            s = dict(stale)
            s['leveling_enabled'] = True
            p._apply_sound_settings(s, getattr(music_player, 'SOUND_DIALOG_KEYS', None))
            e = dict(stale)
            e['eq_enabled'], e['eq_bands_db'] = True, [4.0] + [0.0] * 9
            p._apply_sound_settings(e, getattr(music_player, 'EQ_WINDOW_KEYS', None))
            p.root.run_all()
            self.assertTrue(p.audio_settings['leveling_enabled'])
            self.assertEqual(p.audio_settings['eq_bands_db'][0], 4.0)

    def test_close_saves_a_change_still_in_its_debounce(self):
        import tempfile
        import audio_settings
        with tempfile.TemporaryDirectory() as d:
            p = self._fake(d)
            p._flush_sound_settings = getattr(MusicPlayerGUI, '_flush_sound_settings').__get__(p)
            s = dict(p.audio_settings)
            s['transition_mode'] = 'smart'
            p._apply_sound_settings(s)
            p._flush_sound_settings()
            self.assertIsNone(p._sound_apply_job)
            self.assertEqual(audio_settings.load(p.audio_settings_file)['transition_mode'], 'smart')

    def test_import_eqf_adds_user_presets(self):
        import tempfile
        from eq import build_eqf
        with tempfile.TemporaryDirectory() as d:
            p = self._fake(d)
            f = os.path.join(d, 'mine.eqf')
            with open(f, 'wb') as fh:
                fh.write(build_eqf([('Warm', [3.1] + [0.0] * 9, 0.0), ('Bright', [0.0] * 9 + [6.2], -1.9)]))
            self.assertEqual(p._import_eqf_file(f), 2)
            p.root.run_all()
            self.assertEqual(sorted(p.audio_settings['eq_user_presets']), ['Bright', 'Warm'])
            with self.assertRaises(ValueError):
                with open(f, 'wb') as fh:
                    fh.write(b'not an eqf')
                p._import_eqf_file(f)


class OldMp3CacheTests(unittest.TestCase):
    def test_delete_old_mp3_cache_only_touches_convert_cache(self):
        import tempfile
        from pathlib import Path

        class P:
            _old_mp3_cache_size = MusicPlayerGUI._old_mp3_cache_size
            _delete_old_mp3_cache = MusicPlayerGUI._delete_old_mp3_cache

        with tempfile.TemporaryDirectory() as d:
            cache = Path(d)
            (cache / 'convert_cache').mkdir()
            (cache / 'convert_cache' / 'a.mp3').write_bytes(b'x' * 1000)
            (cache / 'convert_cache' / 'b.mp3').write_bytes(b'y' * 500)
            (cache / 'library_cache.json').write_text('{}')
            (cache / 'music_player_metadata.xml').write_text('<x/>')
            p = P()
            p.convert_cache_dir = cache / 'convert_cache'
            self.assertEqual(p._old_mp3_cache_size(), (2, 1500))
            self.assertEqual(p._delete_old_mp3_cache(), (2, 1500))
            self.assertFalse((cache / 'convert_cache').exists())
            self.assertTrue((cache / 'library_cache.json').exists())
            self.assertTrue((cache / 'music_player_metadata.xml').exists())
            self.assertEqual(p._old_mp3_cache_size(), (0, 0))


class _ThreadCheckRoot:
    """Records which thread called after(); never runs the jobs itself."""

    def __init__(self):
        self.after_threads, self.jobs = [], []

    def after(self, ms, fn, *args):
        self.after_threads.append(threading.current_thread())
        self.jobs.append((ms, fn))
        return len(self.jobs)

    def after_cancel(self, job):
        pass


def _drain_until(p, pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not pred():
        p._drain_main_calls()
        time.sleep(0.01)
    return pred()


class MainThreadPostingTests(unittest.TestCase):
    """The engine thread holds _engine_lock while it emits; it must never block on Tk (deadlock)."""

    class P:
        _engine_emit = MusicPlayerGUI._engine_emit
        _analyze_then = MusicPlayerGUI._analyze_then
        _post_to_main = getattr(MusicPlayerGUI, '_post_to_main', None)
        _drain_main_calls = getattr(MusicPlayerGUI, '_drain_main_calls', None)

    def _fake(self):
        p = self.P()
        p.root = _ThreadCheckRoot()
        p._main_calls = queue.SimpleQueue()
        return p

    def test_engine_events_reach_tk_only_from_the_main_thread(self):
        p = self._fake()
        got = []
        p._on_engine_event = lambda name, **d: got.append((name, d))
        t = threading.Thread(target=lambda: p._engine_emit('started', item='x', how='cut'))
        t.start()
        t.join(5.0)
        self.assertEqual(p.root.after_threads, [])
        p._drain_main_calls()
        self.assertEqual(got, [('started', {'item': 'x', 'how': 'cut'})])
        self.assertTrue(all(th is threading.main_thread() for th in p.root.after_threads))
        self.assertEqual(len(p.root.jobs), 1)  # the drain reschedules itself

    def test_analysis_callback_runs_even_when_the_song_is_already_in_flight(self):
        # Next pressed while the next-song prep is still measuring that song: the song must still start.
        p = self._fake()
        gate = threading.Event()

        class Svc:
            def analyze_now(self, path):
                gate.wait(5.0)

        p._loudness = Svc()
        done = []
        t = _Track('/m/a.mp3')
        p._analyze_then([t], lambda: done.append('prep'))
        p._analyze_then([t], lambda: done.append('play'))
        gate.set()
        self.assertTrue(_drain_until(p, lambda: len(done) == 2))
        self.assertEqual(sorted(done), ['play', 'prep'])
        self.assertTrue(all(th is threading.main_thread() for th in p.root.after_threads))

    def test_analysis_error_still_runs_the_callback(self):
        p = self._fake()

        class Svc:
            def analyze_now(self, path):
                raise AttributeError('cache not loaded')

        p._loudness = Svc()
        done = []
        p._analyze_then([_Track('/m/a.mp3')], lambda: done.append(1))
        self.assertTrue(_drain_until(p, lambda: done == [1]))


class FfmpegPathTests(unittest.TestCase):
    def test_loudness_uses_the_current_ffmpeg_and_retries_after_it_changes(self):
        # FFmpeg set via Settings after startup: analysis must use it, and earlier failures get another go.
        from unittest import mock
        import music_player

        class P:
            _analyze_loudness = getattr(MusicPlayerGUI, '_analyze_loudness', None)
            _ffmpeg_path_changed = getattr(MusicPlayerGUI, '_ffmpeg_path_changed', None)

        p = P()
        forgot, scans = [], []

        class Svc:
            def forget_failures(self):
                forgot.append(1)

        p._loudness = Svc()
        p._start_loudness_scan = lambda: scans.append(1)
        p.ffmpeg_path = None
        with mock.patch.object(music_player, 'analyze_file', lambda ff, path, **kw: (ff, path)):
            self.assertEqual(p._analyze_loudness('/m/a.mp3'), ('ffmpeg', '/m/a.mp3'))
            p.ffmpeg_path = 'C:/tools/ffmpeg.exe'
            self.assertEqual(p._analyze_loudness('/m/a.mp3')[0], 'C:/tools/ffmpeg.exe')
        p._ffmpeg_path_changed()
        self.assertEqual((forgot, scans), ([1], [1]))


class _Label:
    def config(self, **kw):
        pass


class _Var:
    def set(self, value):
        pass


class _PlayEngine:
    def __init__(self):
        self.cur, self.stops, self.pauses = None, 0, 0

    def current(self):
        return self.cur

    def stop(self):
        self.stops += 1
        self.cur = None

    def pause(self):
        self.pauses += 1


class _PendingStartFake:
    """A song waiting for its loudness analysis before it starts (leveling on, not yet measured)."""
    play_track_at_index = MusicPlayerGUI.play_track_at_index
    _run_pending_start = getattr(MusicPlayerGUI, '_run_pending_start', None)
    _cancel_pending_start = getattr(MusicPlayerGUI, '_cancel_pending_start', None)
    stop = MusicPlayerGUI.stop
    pause = MusicPlayerGUI.pause
    play = MusicPlayerGUI.play

    def __init__(self):
        self.audio_available = True
        self.current_playlist = [_Track(f'/m/t{i}.mp3') for i in range(3)]
        self.current_index = 0
        self.ffmpeg_path = 'ffmpeg'
        self.status_label = self.now_playing_label = self.now_playing_artist = self.time_label = _Label()
        self.progress_var = _Var()
        self._engine_lock = threading.RLock()
        self._engine = _PlayEngine()
        self._engine_item = None
        self._pb_tracker = None
        self._play_token = 0
        self._start_pending = False
        self._loudness = self
        self.events, self.pending = [], []

    def pause_background(self):
        self.events.append('scan-pause')

    def resume_background(self):
        self.events.append('scan-resume')

    def _finalize_playback(self, reason, transition='cut'):
        self.events.append(('finalize', reason))

    def _needs_analysis(self, track):
        return True

    def _analyze_then(self, tracks, callback):
        self.pending.append(callback)

    def _start_item(self, track):
        self.events.append(('start', track.filepath))

    def _show_placeholder_artwork(self):
        pass


class PendingStartTests(unittest.TestCase):
    def test_stop_cancels_a_pending_start(self):
        p = _PendingStartFake()
        p.play_track_at_index(1)
        p.stop()
        for cb in p.pending:
            cb()
        self.assertNotIn(('start', '/m/t1.mp3'), p.events)
        self.assertEqual(p.events.count('scan-resume'), 1)  # the scan isn't left paused

    def test_pause_during_a_pending_start_holds_the_new_song(self):
        p = _PendingStartFake()
        p._engine.cur = 'old song'          # still audible while the new one is measured
        p.play_track_at_index(1)
        p.pause()
        for cb in p.pending:
            cb()
        self.assertNotIn(('start', '/m/t1.mp3'), p.events)
        self.assertEqual(p._engine.stops, 1)   # old song cut, not left paused under the new title
        self.assertEqual(p._playback_state, 'paused')
        p.pending.clear()
        p.play()                               # resumes = starts the song that was picked
        self.assertEqual(len(p.pending), 1)
        p.pending[0]()
        self.assertIn(('start', '/m/t1.mp3'), p.events)


class ListeningLogGlueTests(unittest.TestCase):
    def test_log_written_per_finished_playback(self):
        import tempfile
        import time
        import listening_log
        from playback_feedback import PlaybackTracker

        class P:
            _finalize_playback = MusicPlayerGUI._finalize_playback

            def _update_track_metadata(self, track):
                pass

        with tempfile.TemporaryDirectory() as d:
            p = P()
            p.listening_log_file = os.path.join(d, 'listening_log.jsonl')
            t = _Track('/m/a.mp3')
            t.artist, t.album, t.genre = 'A', 'B', 'G'
            t.last_played = int(time.time())
            t.qualified_play_count = t.manual_skip_count = None
            p._pb_tracker, p._pb_track = PlaybackTracker(1, duration=200.0, now=time.monotonic()), t
            p._finalize_playback('next')
            p._pb_tracker, p._pb_track = PlaybackTracker(2, duration=200.0, now=time.monotonic() - 199), t
            p._finalize_playback('natural_end', 'gapless')
            recs = listening_log.read(p.listening_log_file)
        self.assertEqual([(r['reason'], r['tr']) for r in recs], [('next', 'cut'), ('natural_end', 'gapless')])


if __name__ == '__main__':
    unittest.main()
