"""
tests/test_audio_engine.py - AudioEngine scheduler against fake clock, channels and decoders.

No pygame, no FFmpeg: FakeChannel simulates a pygame channel (one sound playing, one queued) in clock time,
FakeDecoder yields 5 s chunks. Tests use 8 bytes per second so chunks stay tiny.

Run with:
    python -m unittest tests.test_audio_engine -v
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from audio_engine import AudioEngine, PlayItem
from transitions import TransitionPlan

BPS = 8  # bytes per second of audio in these tests


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeChannel:
    """A pygame Channel as measured on pygame 2.6.1 (SDL disk driver): one sound playing, one queued.

    Like the real thing: fadeout() lets the QUEUED sound start afterwards at full length and the pre-fade
    volume; fadeout() does nothing at volume 0; a fade doesn't progress while paused; set_volume() rounds
    down to 1/128 steps; stop() clears the queue; queue() on an idle channel starts it at once.
    """

    def __init__(self, clock):
        self.clock = clock
        self.cur = None          # [pcm, started_at]
        self.q = None
        self.volume = 1.0
        self.paused_at = None
        self.fade_end = None
        self.log = []
        self.played = []         # every pcm that started playing, in order

    def _sync(self):
        if self.paused_at is not None:
            return
        while self.cur is not None:
            pcm, st = self.cur
            end = st + len(pcm) / BPS
            faded = self.fade_end is not None and self.fade_end <= end
            if faded:
                end = self.fade_end
            if self.clock.t < end - 1e-9:
                break
            self.fade_end = None
            if self.q is not None:
                self.cur = [self.q, end]
                self.played.append(self.q)
                self.q = None
            else:
                self.cur = None

    def play(self, pcm, fade_ms=0):
        self._sync()
        self.cur = [pcm, self.clock.t]
        self.q = None
        self.fade_end = None
        self.played.append(pcm)
        self.log.append(('play', fade_ms))

    def queue(self, pcm):
        self._sync()
        if self.cur is None:
            self.play(pcm)
        else:
            self.q = pcm

    def queue_free(self):
        self._sync()
        return self.q is None

    def busy(self):
        self._sync()
        return self.cur is not None

    def set_volume(self, v):
        self.volume = math.floor(max(0.0, min(1.0, v)) * 128) / 128

    def fadeout(self, ms):
        self._sync()
        self.log.append(('fadeout', ms))
        if self.cur is None or self.volume <= 0.0:
            return
        if self.fade_end is None:
            self.fade_end = self.clock.t + ms / 1000.0

    def stop(self):
        self.cur = None
        self.q = None
        self.fade_end = None
        self.log.append(('stop',))

    def pause(self):
        self._sync()
        self.paused_at = self.clock.t

    def resume(self):
        if self.paused_at is not None:
            delta = self.clock.t - self.paused_at
            if self.cur is not None:
                self.cur[1] += delta
            if self.fade_end is not None:
                self.fade_end += delta
        self.paused_at = None

    def audible(self):
        """Is real (non-silent) audio coming out of this channel right now?"""
        self._sync()
        return (self.cur is not None and self.paused_at is None and self.volume > 0.0
                and bool(self.cur[0].strip(b'\x00')))

    def tags(self):
        return [p.split(b'.')[0].decode() for p in self.played if p.strip(b'\x00')]


class FakeDecoder:
    def __init__(self, path, start, filters, duration, fail_after=None, clock=None, delay=0.0):
        self.path, self.start, self.filters = path, start, filters
        self.remaining = max(0.0, duration - start)
        self.i = 0
        self.closed = False
        self.fail_after = fail_after
        self._failed = None
        self.clock = clock
        self.ready_at = (clock.t + delay) if clock is not None else 0.0

    def read_chunk(self):
        if self.closed:
            return None
        if self.clock is not None and self.clock.t < self.ready_at - 1e-9:
            return b''  # FFmpeg still starting up
        if self.fail_after is not None and self.i >= self.fail_after:
            self._failed = 'boom'
            return None
        if self.remaining <= 1e-9:
            return None
        sec = min(5.0, self.remaining)
        self.remaining -= sec
        n = int(round(sec * BPS))
        tag = f'{self.path}#{self.i}.'.encode()
        self.i += 1
        return (tag + b'.' * n)[:max(n, len(tag))]

    def failed(self):
        return self._failed

    def close(self):
        self.closed = True


def item(path, dur, filters=''):
    return PlayItem(key=path, path=path, duration_sec=dur, filters=filters)


GAPLESS = TransitionPlan('gapless', 0.0, 0.0, 0.0, 0.0)


class Rig:
    def __init__(self, durations, fail=None, delay=None, raise_on=()):
        self.clock = Clock()
        self.a = FakeChannel(self.clock)
        self.b = FakeChannel(self.clock)
        self.events = []
        self.decoders = []
        fail = fail or {}
        self.delay = dict(delay or {})

        def open_decoder(path, start, filters):
            if path in raise_on:
                raise OSError('ffmpeg could not start')
            d = FakeDecoder(path, start, filters, durations[path], fail.get(path), self.clock,
                            self.delay.get(path, 0.0))
            self.decoders.append(d)
            return d

        self.eng = AudioEngine(self.clock, (self.a, self.b), open_decoder,
                               lambda name, **d: self.events.append((name, d)), bytes_per_sec=BPS,
                               frame_bytes=1)

    def run_to(self, t, step=0.02):
        n = round(self.clock.t / step)
        while self.clock.t < t - 1e-9:
            n += 1
            self.clock.t = min(t, round(n * step, 9))
            self.eng.tick()

    def opened(self, path):
        return [d for d in self.decoders if d.path == path]

    def ev(self):
        out = []
        for name, d in self.events:
            detail = d.get('how') or d.get('reason') or d.get('message')
            out.append((name, d['item'].path, detail))
        return out

    def ev_at(self, name, path):
        return [d for n, d in self.events if n == name and d['item'].path == path]


def overlap(cue, ov, start=0.0, end=None):
    return TransitionPlan('overlap', cue, ov, start, cue + ov if end is None else end)


class AudioEngineTests(unittest.TestCase):
    def test_play_starts_first_chunk_and_queues_second(self):
        r = Rig({'/a': 20})
        r.eng.play(item('/a', 20))
        r.eng.tick()
        self.assertEqual(r.a.tags(), ['/a#0'])
        self.assertTrue(r.a.q.startswith(b'/a#1'))
        self.assertEqual(r.ev(), [('started', '/a', 'cut')])

    def test_refills_as_chunks_finish(self):
        r = Rig({'/a': 20})
        r.eng.play(item('/a', 20))
        r.run_to(16)
        self.assertEqual(r.a.tags(), ['/a#0', '/a#1', '/a#2', '/a#3'])
        self.assertAlmostEqual(r.eng.position(), 16.0, places=6)

    def test_natural_end_without_next(self):
        r = Rig({'/a': 12})
        r.eng.play(item('/a', 12))
        r.run_to(11.9)
        self.assertEqual(r.ev_at('finished', '/a'), [])
        r.run_to(13)
        fin = r.ev_at('finished', '/a')
        self.assertEqual(len(fin), 1)
        self.assertEqual((fin[0]['reason'], fin[0]['continued']), ('natural_end', False))
        self.assertIsNone(r.eng.current())

    def test_gapless_handoff_same_channel(self):
        r = Rig({'/a': 12, '/b': 10})
        r.eng.play(item('/a', 12))
        r.eng.prepare_next(item('/b', 10), GAPLESS)
        r.run_to(12.5)
        self.assertEqual(r.a.tags(), ['/a#0', '/a#1', '/a#2', '/b#0'])
        self.assertEqual(r.b.played, [])
        self.assertEqual(r.ev(), [('started', '/a', 'cut'), ('finished', '/a', 'natural_end'), ('started', '/b', 'gapless')])
        self.assertTrue(r.ev_at('finished', '/a')[0]['continued'])
        self.assertEqual(r.eng.current().path, '/b')
        self.assertAlmostEqual(r.eng.position(), 0.5, places=6)

    def test_crossfade_ramps_and_finishes(self):
        r = Rig({'/a': 200, '/b': 180})
        r.eng.play(item('/a', 200))
        r.eng.prepare_next(item('/b', 180), overlap(194.0, 6.0, end=200.0))
        r.run_to(194.05)
        self.assertEqual(r.b.tags(), ['/b#0'])
        st = r.ev_at('started', '/b')
        self.assertEqual(st[0]['how'], 'overlap')
        self.assertEqual(r.eng.current().path, '/b')
        r.run_to(197.0)
        self.assertAlmostEqual(r.a.volume, 0.7071, places=2)
        self.assertAlmostEqual(r.b.volume, 0.7071, places=2)
        r.run_to(200.1)
        self.assertFalse(r.a.audible())   # outgoing song is over and silent
        fin = r.ev_at('finished', '/a')
        self.assertEqual((fin[0]['reason'], fin[0]['continued']), ('natural_end', True))
        self.assertAlmostEqual(r.b.volume, 1.0, places=6)
        self.assertAlmostEqual(r.eng.position(), 6.1, places=6)

    def test_manual_play_during_overlap_cuts_both(self):
        r = Rig({'/a': 200, '/b': 180, '/c': 50})
        r.eng.play(item('/a', 200))
        r.eng.prepare_next(item('/b', 180), overlap(194.0, 6.0))
        r.run_to(196)
        r.eng.play(item('/c', 50))
        r.eng.tick()
        self.assertIn(('fadeout', 20), r.a.log)
        self.assertIn(('fadeout', 20), r.b.log)
        self.assertEqual(r.ev_at('finished', '/a')[0]['reason'], 'cut')
        self.assertEqual(r.ev_at('finished', '/b')[0]['reason'], 'cut')
        self.assertEqual(r.ev()[-1], ('started', '/c', 'cut'))
        self.assertEqual(r.eng.current().path, '/c')

    def test_pause_freezes_ramp(self):
        r = Rig({'/a': 200, '/b': 180})
        r.eng.play(item('/a', 200))
        r.eng.prepare_next(item('/b', 180), overlap(194.0, 6.0))
        r.run_to(196)
        r.eng.pause()
        va, vb = r.a.volume, r.b.volume
        r.run_to(206)
        self.assertEqual((r.a.volume, r.b.volume), (va, vb))
        self.assertEqual(r.ev_at('finished', '/a'), [])
        r.eng.resume()
        r.run_to(207)
        self.assertAlmostEqual(r.a.volume, 0.7071, places=2)

    def test_seek_restarts_decoder_at_offset(self):
        r = Rig({'/a': 200})
        r.eng.play(item('/a', 200, filters='F'))
        r.run_to(3)
        r.eng.seek(42.0)
        r.eng.tick()
        self.assertTrue(r.decoders[0].closed)
        self.assertEqual((r.decoders[-1].path, r.decoders[-1].start, r.decoders[-1].filters), ('/a', 42.0, 'F'))
        self.assertAlmostEqual(r.eng.position(), 42.0, places=6)
        r.run_to(4)
        self.assertAlmostEqual(r.eng.position(), 43.0, places=6)
        self.assertEqual(r.ev(), [('started', '/a', 'cut')])

    def test_seek_during_overlap_drops_outgoing(self):
        r = Rig({'/a': 200, '/b': 180})
        r.eng.play(item('/a', 200))
        r.eng.prepare_next(item('/b', 180), overlap(194.0, 6.0))
        r.run_to(196)
        r.eng.seek(10.0)
        r.eng.tick()
        self.assertEqual(r.ev_at('finished', '/a')[0]['reason'], 'cut')
        self.assertEqual((r.decoders[-1].path, r.decoders[-1].start), ('/b', 10.0))
        self.assertEqual(r.eng.current().path, '/b')
        self.assertAlmostEqual(r.eng.position(), 10.0, places=6)

    def test_refilter_swaps_with_short_fade(self):
        r = Rig({'/a': 200})
        r.eng.play(item('/a', 200, filters='old'))
        r.run_to(10)
        r.eng.refilter(lambda it: 'EQ')
        r.run_to(10.5)
        d = r.decoders[-1]
        self.assertEqual((d.path, d.filters), ('/a', 'EQ'))
        self.assertAlmostEqual(d.start, 10.25, places=6)
        self.assertTrue(r.decoders[0].closed)
        self.assertIn(('fadeout', 30), r.a.log)
        self.assertIn(('play', 30), r.b.log)
        self.assertAlmostEqual(r.eng.position(), 10.5, delta=0.06)
        self.assertEqual(r.ev(), [('started', '/a', 'cut')])

    def test_prepare_next_replaced_closes_old_decoder(self):
        r = Rig({'/a': 12, '/b': 10, '/c': 10})
        r.eng.play(item('/a', 12))
        r.eng.prepare_next(item('/b', 10), GAPLESS)
        r.run_to(11)
        r.eng.prepare_next(item('/c', 10), GAPLESS)
        r.run_to(12.5)
        self.assertTrue(all(d.closed for d in r.decoders if d.path == '/b'))
        self.assertNotIn('/b#0', r.a.tags())
        self.assertEqual(r.a.tags()[-1], '/c#0')
        self.assertEqual(r.eng.current().path, '/c')

    def test_decoder_failure_mid_song(self):
        r = Rig({'/a': 30}, fail={'/a': 2})
        r.eng.play(item('/a', 30))
        r.run_to(5.5)
        self.assertEqual(r.ev_at('error', '/a')[0]['message'], 'boom')
        self.assertEqual(r.ev_at('finished', '/a'), [])
        r.run_to(10.5)
        fin = r.ev_at('finished', '/a')
        self.assertEqual((fin[0]['reason'], fin[0]['continued']), ('failure', False))

    def test_unplayable_file_finishes_with_failure(self):
        r = Rig({'/a': 30}, fail={'/a': 0})
        r.eng.play(item('/a', 30))
        r.eng.tick()
        self.assertEqual(r.ev(), [('error', '/a', 'boom'), ('finished', '/a', 'failure')])
        self.assertFalse(r.ev_at('finished', '/a')[0]['continued'])
        self.assertIsNone(r.eng.current())

    def test_failed_successor_does_not_block(self):
        r = Rig({'/a': 12, '/b': 10}, fail={'/b': 0})
        r.eng.play(item('/a', 12))
        r.eng.prepare_next(item('/b', 10), GAPLESS)
        r.run_to(13)
        fin = r.ev_at('finished', '/a')
        self.assertEqual((fin[0]['reason'], fin[0]['continued']), ('natural_end', False))
        self.assertEqual(r.ev_at('started', '/b'), [])
        self.assertIsNone(r.eng.current())

    def test_zero_duration_item_plays_to_eof(self):
        r = Rig({'/a': 7, '/b': 10})
        r.eng.play(item('/a', 0))
        r.eng.prepare_next(item('/b', 10), overlap(-6.0, 6.0, end=0.0))
        r.run_to(7.5)
        self.assertEqual(r.b.played, [])
        self.assertEqual(r.a.tags(), ['/a#0', '/a#1', '/b#0'])
        self.assertEqual(r.ev_at('started', '/b')[0]['how'], 'gapless')

    def test_master_volume_applied(self):
        r = Rig({'/a': 200, '/b': 180})
        r.eng.play(item('/a', 200))
        r.eng.set_master_volume(0.5)
        r.eng.tick()
        self.assertAlmostEqual(r.a.volume, 0.5, places=6)
        r.eng.prepare_next(item('/b', 180), overlap(194.0, 6.0))
        r.run_to(197.0)
        self.assertAlmostEqual(r.a.volume, 0.3536, places=2)
        self.assertAlmostEqual(r.b.volume, 0.3536, places=2)

    def test_stop_finishes_with_cut(self):
        r = Rig({'/a': 200})
        r.eng.play(item('/a', 200))
        r.run_to(3)
        r.eng.stop()
        self.assertIn(('fadeout', 20), r.a.log)
        self.assertEqual(r.ev()[-1], ('finished', '/a', 'cut'))
        self.assertIsNone(r.eng.current())
        self.assertTrue(r.decoders[0].closed)


    # --- review findings: pygame-realistic channel, slow and failing decoders ---------------------------
    def _audible(self, r):
        return sum(ch.audible() for ch in (r.a, r.b))

    def test_cut_silences_old_song_promptly(self):
        r = Rig({'/a': 200, '/c': 50})
        r.eng.play(item('/a', 200))
        r.run_to(3)
        r.eng.play(item('/c', 50))
        r.run_to(3.1)
        self.assertEqual(self._audible(r), 1)
        r.run_to(8.5)
        self.assertEqual(self._audible(r), 1)
        self.assertNotIn('/a#1', r.a.tags() + r.b.tags())

    def test_stop_silences_promptly(self):
        r = Rig({'/a': 200})
        r.eng.play(item('/a', 200))
        r.run_to(3)
        r.eng.stop()
        r.run_to(3.1)
        self.assertEqual(self._audible(r), 0)
        r.run_to(9)
        self.assertNotIn('/a#1', r.a.tags() + r.b.tags())

    def test_next_twice_within_a_chunk_keeps_working(self):
        r = Rig({'/a': 200, '/b': 200, '/c': 200})
        r.eng.play(item('/a', 200))
        r.run_to(1)
        r.eng.play(item('/b', 200))
        r.run_to(2)
        r.eng.play(item('/c', 200))
        r.run_to(8)
        self.assertEqual(r.ev()[-1], ('started', '/c', 'cut'))
        self.assertAlmostEqual(r.eng.position(), 6.0, delta=0.05)
        self.assertEqual(self._audible(r), 1)

    def test_seek_during_overlap_position_advances(self):
        r = Rig({'/a': 200, '/b': 180})
        r.eng.play(item('/a', 200))
        r.eng.prepare_next(item('/b', 180), overlap(194.0, 6.0))
        r.run_to(196)
        r.eng.seek(10.0)
        r.run_to(197)
        self.assertAlmostEqual(r.eng.position(), 11.0, delta=0.05)
        self.assertEqual(self._audible(r), 1)

    def test_paused_cut_does_not_leak_old_song(self):
        r = Rig({'/a': 200, '/c': 50})
        r.eng.play(item('/a', 200))
        r.run_to(3)
        r.eng.pause()
        r.run_to(4)
        r.eng.play(item('/c', 50))
        r.run_to(9)
        self.assertNotIn('/a#1', r.a.tags() + r.b.tags())
        self.assertEqual(self._audible(r), 1)

    def test_crossfade_end_leaves_outgoing_silent(self):
        r = Rig({'/a': 200, '/b': 180})
        r.eng.play(item('/a', 200))
        r.eng.prepare_next(item('/b', 180), overlap(196.0, 4.0, end=200.0))
        r.run_to(200.3)
        self.assertEqual(self._audible(r), 1)
        self.assertEqual(r.eng.current().path, '/b')

    def _slow_fail(self, plan):
        r = Rig({'/a': 12, '/b': 10}, fail={'/b': 0}, delay={'/b': 11.0})
        r.eng.play(item('/a', 12))
        r.eng.prepare_next(item('/b', 10), plan)
        r.run_to(16)
        self.assertEqual(len(r.opened('/b')), 1)
        self.assertEqual(len(r.ev_at('error', '/b')), 1)
        fin = r.ev_at('finished', '/a')
        self.assertEqual([(f['reason'], f['continued']) for f in fin], [('natural_end', False)])
        self.assertIsNone(r.eng.current())

    def test_slow_failing_successor_gapless(self):
        self._slow_fail(GAPLESS)

    def test_slow_failing_successor_overlap(self):
        self._slow_fail(overlap(9.0, 3.0, end=12.0))

    def test_slow_successor_still_continues(self):
        r = Rig({'/a': 12, '/b': 10}, delay={'/b': 11.0})
        r.eng.play(item('/a', 12))
        r.eng.prepare_next(item('/b', 10), GAPLESS)
        r.run_to(12.5)
        self.assertEqual(r.ev_at('finished', '/a'), [])   # waiting for b's first audio, not "nothing follows"
        r.run_to(13.5)
        self.assertEqual(r.ev()[-2:], [('finished', '/a', 'natural_end'), ('started', '/b', 'gapless')])
        self.assertTrue(r.ev_at('finished', '/a')[0]['continued'])
        self.assertEqual(r.eng.current().path, '/b')

    def test_prepare_none_during_pending_window(self):
        r = Rig({'/a': 12, '/b': 10}, delay={'/b': 11.0})
        r.eng.play(item('/a', 12))
        r.eng.prepare_next(item('/b', 10), GAPLESS)
        r.run_to(12.5)
        r.eng.prepare_next(None, None)
        r.eng.tick()
        fin = r.ev_at('finished', '/a')
        self.assertEqual([(f['reason'], f['continued']) for f in fin], [('natural_end', False)])
        self.assertIsNone(r.eng.current())

    def test_refilter_near_end_does_not_cut_next(self):
        r = Rig({'/a': 12, '/b': 10})
        r.eng.play(item('/a', 12))
        r.eng.prepare_next(item('/b', 10), GAPLESS)
        r.run_to(11.9)
        r.eng.refilter(lambda it: 'EQ')
        r.run_to(16)
        self.assertEqual(len(r.ev_at('finished', '/a')), 1)
        self.assertEqual(r.ev_at('finished', '/b'), [])
        self.assertEqual(r.eng.current().path, '/b')
        self.assertAlmostEqual(r.eng.position(), 4.0, delta=0.05)

    def test_refilter_waits_for_slow_decoder(self):
        r = Rig({'/a': 200})
        r.eng.play(item('/a', 200))
        r.run_to(10)
        r.delay['/a'] = 0.4
        r.eng.refilter(lambda it: 'EQ')
        r.run_to(10.3)
        self.assertEqual(self._audible(r), 1)                # old audio keeps playing, no gap
        self.assertAlmostEqual(r.eng.position(), 10.3, delta=0.05)
        r.run_to(10.8)
        self.assertEqual(r.decoders[-1].filters, 'EQ')
        self.assertTrue(r.decoders[0].closed)
        self.assertAlmostEqual(r.eng.position(), 10.8, delta=0.15)
        self.assertEqual(self._audible(r), 1)

    def test_gapless_next_opened_early(self):
        r = Rig({'/a': 20, '/b': 10})
        r.eng.play(item('/a', 20))
        r.eng.prepare_next(item('/b', 10), GAPLESS)
        r.run_to(10.5)
        self.assertEqual(len(r.opened('/b')), 1)

    def test_open_decoder_error_is_a_failure(self):
        r = Rig({'/a': 10}, raise_on={'/a'})
        r.eng.play(item('/a', 10))
        r.eng.tick()
        self.assertEqual([n for n, _ in r.events], ['error', 'finished'])
        self.assertEqual(r.ev_at('finished', '/a')[0]['reason'], 'failure')
        self.assertIsNone(r.eng.current())

    def test_failed_successor_opens_one_decoder(self):
        r = Rig({'/a': 12, '/b': 10}, fail={'/b': 0})
        r.eng.play(item('/a', 12))
        r.eng.prepare_next(item('/b', 10), GAPLESS)
        r.run_to(13)
        self.assertEqual(len(r.opened('/b')), 1)
        self.assertEqual(len(r.ev_at('error', '/b')), 1)

if __name__ == '__main__':
    unittest.main()
