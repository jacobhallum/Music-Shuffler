"""
audio_engine.py - two-channel chunk scheduler for the sound engine (no pygame, no threads).

The player feeds songs to two playback channels in chunks that a decoder produces (FFmpeg in production).
Each channel plays one chunk and holds one queued, so a song keeps playing without gaps while the engine
thread calls tick() every ~20 ms.

Transitions between songs that end on their own:
  - gapless: the next song's chunks follow on the SAME channel, so playback is sample-continuous;
  - overlap: the next song starts on the OTHER channel and the two channel volumes follow an equal-power
    ramp (crossfade or smart, as planned by transitions.plan_transition).
Manual actions (play, stop, seek) cut with a short fade-out, never overlap.

Real pygame channels (measured on pygame 2.6.1) shape the cut: after fadeout() the QUEUED sound still starts
and plays in full, fadeout() does nothing at volume 0, and a fade doesn't progress while paused. So a cut
first replaces the queued chunk with a few ms of silence, then fades, or stops outright when paused or silent.

Everything outside is injected: a clock, two channel ports, a decoder factory and an event callback, so the
logic runs under unit tests with fakes. Events (emit(name, **data)):
  started(item, how)               how in 'cut' | 'gapless' | 'overlap'
  finished(item, reason, continued) reason in 'natural_end' | 'cut' | 'failure'; continued=True when the
                                   prepared next song is already playing (or its first audio is in place)
  error(item, message)             the decoder failed; the song ends where its audio runs out
"""

from collections import deque
from dataclasses import dataclass

from ffmpeg_cmd import BYTES_PER_FRAME, BYTES_PER_SEC
from transitions import equal_power_gains

CUT_FADE_MS = 20
SWAP_FADE_MS = 30
REFILTER_LEAD_SEC = 0.25
OPEN_LEAD_SEC = 10.0
SILENCE_SEC = 0.01
_MIN_AUDIBLE_VOLUME = 1.0 / 128   # pygame rounds volumes down to 1/128 steps
_EPS = 1e-9


@dataclass(eq=False)
class PlayItem:
    key: object
    path: str
    duration_sec: float
    filters: str = ''


class _DeadDecoder:
    """Stands in when the decoder couldn't even start (FFmpeg missing, bad path): a failed, empty song."""

    def __init__(self, message):
        self._message = message

    def read_chunk(self):
        return None

    def failed(self):
        return self._message

    def close(self):
        pass


class _Stream:
    """One decoder feeding one song from a start offset."""

    def __init__(self, item, decoder, start, how, plan=None):
        self.item = item
        self.decoder = decoder
        self.start = start
        self.how = how
        self.plan = plan
        self.ch = None          # channel index once assigned
        self.fed = 0.0          # seconds handed to the channel
        self.queued = 0         # segments of this stream still on a channel
        self.pending = None     # first chunk, read early (EQ swap)
        self.eof = False
        self.failed = None
        self.dead = False
        self.started = False
        self.finished = False
        self.fade_in_ms = 0
        self.overlap = None     # (start_pos, duration, end_pos) of the outgoing song when how == 'overlap'


class _Seg:
    __slots__ = ('stream', 'offset', 'length')

    def __init__(self, stream, offset, length):
        self.stream, self.offset, self.length = stream, offset, length


class _Chan:
    def __init__(self, port):
        self.port = port
        self.segs = deque()     # segs[0] is playing, segs[1] (if any) is queued
        self.head_started = None
        self.feeder = None
        self.volume = 1.0


class AudioEngine:
    def __init__(self, clock, channels, open_decoder, emit, *, bytes_per_sec=BYTES_PER_SEC,
                 frame_bytes=BYTES_PER_FRAME):
        self._clock = clock
        self._ch = [_Chan(channels[0]), _Chan(channels[1])]
        self._open_decoder = open_decoder
        self._emit_cb = emit
        self._bps = float(bytes_per_sec)
        # Read-only listener for the visualizer: called with each PCM chunk as it is queued. It must not block
        # or change anything; playback is the same with or without it.
        self.vis_tap = None
        self._frame = int(frame_bytes)
        frames = max(1, int(SILENCE_SEC * self._bps) // self._frame)
        self._silence = bytes(frames * self._frame)
        self._cur = None
        self._out = None
        self._next = None           # (item, plan) for the song after the current one
        self._next_stream = None
        self._pending_finish = None  # (stream, reason): ended, waiting for the next song's first audio
        self._swap = None           # (new stream, swap position, target stream) for an EQ/leveling change
        self._refilter_pending = None
        self._paused = False
        self._paused_at = 0.0
        self._master = 1.0
        self._last_ch = 1

    # -- public API ---------------------------------------------------------
    def play(self, item, start_sec=0.0):
        self._cut_all()
        if self._paused:
            self.resume()
        s = self._open(item, start_sec, 'cut')
        self._cur = s
        self._assign(s, self._pick_channel())
        self._set_vol(s.ch, self._master)
        self._feed(s.ch)

    def prepare_next(self, item, plan):
        if item is not None and plan is not None and self._next is not None \
                and self._next[0] is item and self._next[1] == plan:
            return
        self._drop_next_stream()
        self._next = (item, plan) if item is not None and plan is not None else None

    def pause(self):
        if self._paused:
            return
        self._paused = True
        self._paused_at = self._clock()
        for c in self._ch:
            c.port.pause()

    def resume(self):
        if not self._paused:
            return
        delta = self._clock() - self._paused_at
        self._paused = False
        for c in self._ch:
            if c.head_started is not None:
                c.head_started += delta
            c.port.resume()

    def stop(self):
        self._cut_all()
        self._cur = None

    def seek(self, sec):
        cur = self._cur
        if cur is None:
            return
        if self._out is not None:
            self._end_outgoing(reason='cut', continued=True)
        self._drop_next_stream()
        self._close_swap()
        old_ch = cur.ch
        self._kill(cur, CUT_FADE_MS)
        s = self._open(cur.item, sec, 'seek')
        s.started = True
        self._cur = s
        self._assign(s, 1 - old_ch if old_ch is not None else self._pick_channel())
        self._set_vol(s.ch, self._master)
        self._feed(s.ch)

    def refilter(self, filters_for):
        if self._next is not None:
            self._next[0].filters = filters_for(self._next[0])
            self._drop_next_stream()
        if self._cur is None:
            return
        if self._out is not None:
            self._refilter_pending = filters_for   # two channels are busy with the crossfade
            return
        self._start_swap(filters_for)

    def set_master_volume(self, v):
        self._master = max(0.0, float(v))
        self._apply_volumes()

    def position(self):
        return self._pos(self._cur) if self._cur is not None else 0.0

    def current(self):
        return self._cur.item if self._cur is not None else None

    def tick(self):
        if self._paused:
            return
        self._apply_volumes()
        for ci in (0, 1):
            self._advance(ci)
        self._preopen_next()
        self._attach_gapless()
        self._maybe_transition()
        self._maybe_swap()
        for ci in (0, 1):
            self._feed(ci)
        self._apply_volumes()

    # -- channels -------------------------------------------------------------
    def _emit(self, name, **data):
        self._emit_cb(name, **data)

    def _now(self):
        return self._paused_at if self._paused else self._clock()

    def _open(self, item, start, how, plan=None):
        try:
            decoder = self._open_decoder(item.path, start, item.filters)
        except Exception as e:  # FFmpeg missing or unusable: a failed song, never a crashed engine
            decoder = _DeadDecoder(f'could not start the decoder: {e}')
        return _Stream(item, decoder, start, how, plan)

    def _pick_channel(self):
        """A channel with no engine audio, preferring one that is fully idle (not still fading)."""
        for wanted in (lambda c: not c.segs and not c.port.busy(), lambda c: not c.segs):
            ok = [i for i in (0, 1) if wanted(self._ch[i])]
            if len(ok) == 1:
                return ok[0]
            if ok:
                return 1 - self._last_ch
        return 1 - self._last_ch

    def _assign(self, s, ci):
        c = self._ch[ci]
        if c.port.busy():
            c.port.stop()   # leftover fade or silence: never queue a new song behind it
        c.segs.clear()
        c.head_started = None
        c.feeder = s
        s.ch = ci
        self._last_ch = ci

    def _set_vol(self, ci, v):
        c = self._ch[ci]
        c.volume = v
        c.port.set_volume(v)

    def _hush(self, ci, fade_ms):
        """Silence a channel now: its queued chunk must never start, then fade (or stop if a fade can't run)."""
        c = self._ch[ci]
        port = c.port
        if port.busy():
            if not port.queue_free():
                port.queue(self._silence)
            if self._paused or c.volume < _MIN_AUDIBLE_VOLUME:
                port.stop()
            else:
                port.fadeout(fade_ms)
        c.segs.clear()
        c.head_started = None
        c.feeder = None

    def _pos(self, s):
        if s.ch is not None:
            c = self._ch[s.ch]
            if c.segs and c.segs[0].stream is s and c.head_started is not None:
                head = c.segs[0]
                return head.offset + min(max(0.0, self._now() - c.head_started), head.length)
        if s.eof and s.queued == 0:
            return s.start + s.fed
        return s.start

    def _feed(self, ci):
        c = self._ch[ci]
        while c.feeder is not None:
            replacing = len(c.segs) >= 2 and c.segs[-1].stream.dead
            if c.segs and not c.port.queue_free() and not replacing:
                return
            s = c.feeder
            if s.pending is not None:
                chunk, s.pending = s.pending, None
            else:
                chunk = None if s.eof else s.decoder.read_chunk()
            if chunk == b'':
                return
            if chunk is None:
                if not s.eof:
                    s.eof = True
                    msg = s.decoder.failed()
                    if msg:
                        s.failed = msg
                        self._emit('error', item=s.item, message=msg)
                nxt = self._gapless_successor(s)
                c.feeder = nxt
                if nxt is not None:
                    nxt.ch = ci
                if nxt is None and s.queued == 0 and not s.finished:
                    if s is self._next_stream:
                        # the prepared next song failed before playing: forget it for good
                        self._next = None
                        self._drop_next_stream()
                    elif s is self._cur or s is self._out:
                        self._on_drained(s)
                continue
            tap = self.vis_tap
            if tap is not None:
                try:
                    tap(chunk)
                except Exception:
                    pass   # the visualizer never gets to break playback
            seg = _Seg(s, s.start + s.fed, len(chunk) / self._bps)
            s.fed += seg.length
            s.queued += 1
            if not c.segs:
                c.port.play(chunk, s.fade_in_ms)
                s.fade_in_ms = 0
                if self._paused:
                    c.port.pause()
                c.segs.append(seg)
                c.head_started = self._now()
                self._on_head(ci)
            else:
                c.port.queue(chunk)
                if replacing:
                    c.segs[-1] = seg
                else:
                    c.segs.append(seg)

    def _advance(self, ci):
        c = self._ch[ci]
        while c.segs:
            if len(c.segs) >= 2 and c.port.queue_free():
                old = c.segs.popleft()
                c.head_started = c.head_started + old.length if c.head_started is not None else self._now()
                self._seg_done(old)
                if c.segs and c.segs[0].stream.dead:
                    c.segs.popleft()
                    c.port.stop()
                    c.head_started = None
                    continue
                if c.segs:
                    self._on_head(ci)
                continue
            if len(c.segs) == 1 and not c.port.busy():
                old = c.segs.popleft()
                c.head_started = None
                self._seg_done(old)
                continue
            break

    # -- song lifecycle ---------------------------------------------------------
    def _seg_done(self, seg):
        s = seg.stream
        s.queued -= 1
        if s.dead or s.queued > 0 or not s.eof:
            return
        if s.ch is not None and self._ch[s.ch].feeder is s:
            return
        self._on_drained(s)

    def _on_head(self, ci):
        s = self._ch[ci].segs[0].stream
        if s.started:
            return
        s.started = True
        prev = self._cur
        if s is self._next_stream:
            self._next_stream = None
            self._next = None
            self._resolve_pending(True)   # the outgoing song's 'finished' goes out first
        if s is not prev:
            if s.how == 'overlap' and prev is not None and not prev.finished:
                self._out = prev
            self._cur = s
            if self._swap is not None and self._swap[2] is not s:
                self._close_swap()
        self._emit('started', item=s.item, how=s.how)

    def _on_drained(self, s):
        if s.finished:
            return
        if s is self._out:
            self._end_outgoing(reason='natural_end', continued=True)
            return
        s.finished = True
        reason = 'failure' if s.failed else 'natural_end'
        nxt = self._next_stream
        attached = nxt is not None and nxt.ch is not None
        self._close(s)
        if attached and not (nxt.queued > 0 or nxt.started):
            self._pending_finish = (s, reason)   # wait for the next song's first audio, or its failure
            return
        self._emit('finished', item=s.item, reason=reason, continued=attached)
        if s is self._cur and not attached:
            self._cur = None

    def _resolve_pending(self, continued):
        pending, self._pending_finish = self._pending_finish, None
        if pending is None:
            return
        s, reason = pending
        self._emit('finished', item=s.item, reason=reason, continued=continued)
        if not continued and self._cur is s:
            self._cur = None

    def _effective_kind(self, plan):
        if plan.kind == 'overlap' and (self._cur.item.duration_sec <= 0 or plan.overlap_sec <= 0):
            return 'gapless'
        return plan.kind

    def _gapless_successor(self, s):
        if s is not self._cur or self._next is None or s.failed:
            return None
        item, plan = self._next
        if self._effective_kind(plan) != 'gapless':
            return None
        if self._next_stream is None:
            self._next_stream = self._open(item, 0.0, 'gapless', plan)
        return self._next_stream

    def _preopen_next(self):
        """Start the next song's decoder well before a gapless hand-off, so its first chunk is ready."""
        cur = self._cur
        if cur is None or self._next is None or self._next_stream is not None or not cur.started or cur.failed:
            return
        item, plan = self._next
        if self._effective_kind(plan) != 'gapless':
            return
        dur = cur.item.duration_sec
        if dur > 0 and self._pos(cur) >= dur - OPEN_LEAD_SEC:
            self._next_stream = self._open(item, 0.0, 'gapless', plan)

    def _attach_gapless(self):
        """A next song prepared (or replaced) after the current decoder already ended joins its channel here."""
        cur = self._cur
        if cur is None or not cur.eof or cur.finished or cur.ch is None or self._ch[cur.ch].feeder is not None:
            return
        nxt = self._gapless_successor(cur)
        if nxt is not None and nxt.ch is None:
            self._ch[cur.ch].feeder = nxt
            nxt.ch = cur.ch

    def _maybe_transition(self):
        out = self._cur
        if out is None or self._next is None or self._out is not None or not out.started or out.failed:
            return
        item, plan = self._next
        if self._effective_kind(plan) != 'overlap':
            return
        pos = self._pos(out)
        trigger = plan.cue_out_sec
        end = plan.outgoing_end_sec
        if out.eof:
            actual_end = out.start + out.fed
            trigger = min(trigger, actual_end - plan.overlap_sec)
            end = min(end, actual_end)
        ns = self._next_stream
        if ns is None and (pos >= trigger - OPEN_LEAD_SEC or out.eof):
            ns = self._open(item, plan.incoming_start_sec, 'overlap', plan)
            self._next_stream = ns
        if ns is None or ns.ch is not None or pos < trigger - _EPS:
            return
        ns.overlap = (pos, plan.overlap_sec, end)
        ci = 1 - out.ch
        self._assign(ns, ci)
        self._set_vol(ci, 0.0)

    def _apply_volumes(self):
        m = self._master
        if self._out is not None and self._cur is not None and self._cur.overlap is not None:
            st, dur, end = self._cur.overlap
            pos = self._pos(self._out)
            p = 1.0 if dur <= 0 else (pos - st) / dur
            if p >= 1.0 - _EPS or pos >= end - _EPS:
                self._end_outgoing(reason='natural_end', continued=True)
            else:
                g_out, g_in = equal_power_gains(p)
                self._set_vol(self._out.ch, g_out * m)
                self._set_vol(self._cur.ch, g_in * m)
                return
        if self._cur is not None and self._cur.ch is not None:
            self._set_vol(self._cur.ch, m)

    def _end_outgoing(self, reason, continued):
        out = self._out
        self._out = None
        if out is None:
            return
        if not out.finished:
            out.finished = True
            self._emit('finished', item=out.item, reason=reason, continued=continued)
        self._kill(out, CUT_FADE_MS)
        if self._cur is not None:
            self._cur.overlap = None
            if self._refilter_pending is not None:
                fn, self._refilter_pending = self._refilter_pending, None
                self._start_swap(fn)

    # -- EQ / leveling changes ---------------------------------------------------
    def _start_swap(self, filters_for):
        self._close_swap()
        cur = self._cur
        cur.item.filters = filters_for(cur.item)
        at = self._pos(cur) + REFILTER_LEAD_SEC
        s = self._open(cur.item, at, cur.how)
        s.started = True
        s.fade_in_ms = SWAP_FADE_MS
        self._swap = (s, at, cur)

    def _maybe_swap(self):
        if self._swap is None:
            return
        s, at, target = self._swap
        if self._cur is not target:
            self._close_swap()   # the song changed: this swap belongs to the old one
            return
        if self._out is not None:
            return
        if target.eof and at >= target.start + target.fed - _EPS:
            self._close_swap()   # the song ends before the swap point: nothing to swap
            return
        pos = self._pos(target)
        if pos < at - _EPS:
            return
        if s.pending is None:
            chunk = s.decoder.read_chunk()
            if chunk == b'':
                return               # new decoder not ready yet: keep the old audio playing
            if chunk is None:
                self._close_swap()   # new decoder failed: keep the old one
                return
            s.pending = chunk
        late = pos - at
        if late > _EPS:
            skip = int(late * self._bps) // self._frame * self._frame
            if skip >= len(s.pending):
                self._close_swap()
                self._start_swap(lambda it: it.filters)   # far behind: try again a little ahead
                return
            if skip:
                s.pending = s.pending[skip:]
                s.start = at + skip / self._bps
        self._swap = None
        old_ch = target.ch
        self._drop_next_stream()     # a gapless successor queued on the old channel re-attaches later
        self._kill(target, SWAP_FADE_MS)
        self._cur = s
        self._assign(s, 1 - old_ch)
        self._set_vol(s.ch, self._master)
        self._feed(s.ch)

    def _close_swap(self):
        if self._swap is not None:
            self._close(self._swap[0])
            self._swap = None

    # -- teardown -----------------------------------------------------------------
    def _drop_next_stream(self):
        ns = self._next_stream
        self._next_stream = None
        if ns is not None:
            ns.dead = True
            self._close(ns)
            if ns.ch is not None and self._ch[ns.ch].feeder is ns:
                self._ch[ns.ch].feeder = None
        self._resolve_pending(False)

    def _kill(self, s, fade_ms):
        """Stop a stream's audio now (short fade) and close its decoder."""
        s.dead = True
        if s.ch is not None:
            self._hush(s.ch, fade_ms)
        self._close(s)

    def _close(self, s):
        try:
            s.decoder.close()
        except Exception:
            pass

    def _cut_all(self):
        self._pending_finish = None   # the caller is moving on by itself
        self._close_swap()
        self._refilter_pending = None
        self._drop_next_stream()
        self._next = None
        for s in (self._out, self._cur):
            if s is None:
                continue
            if s.started and not s.finished:
                s.finished = True
                self._emit('finished', item=s.item, reason='cut', continued=False)
            self._kill(s, CUT_FADE_MS)
        self._out = None
        self._cur = None
