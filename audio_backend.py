"""
audio_backend.py - runtime adapters for the sound engine: FFmpeg decoder, pygame channel, loudness analysis.

FfmpegDecoder runs one FFmpeg process per song (never through a shell) and a reader thread that cuts its
f32le stdout into chunks. It stops reading while `read_ahead_sec` of audio is buffered, so FFmpeg blocks on the
pipe instead of filling memory. PygameChannel wraps a pygame.mixer.Channel for audio_engine. analyze_file runs
FFmpeg's ebur128 filter and parses the result with loudness.parse_ebur128.
"""

import os
import subprocess
import threading
from collections import deque

from ffmpeg_cmd import BYTES_PER_FRAME, BYTES_PER_SEC, analyze_args, decode_args, seconds_to_bytes
from loudness import parse_ebur128

_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
_BELOW_NORMAL = getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0)
_ERR_TAIL = 4096


class FfmpegDecoder:
    """DecoderPort: read_chunk() -> bytes (b'' = not ready yet, None = end), failed(), close()."""

    def __init__(self, ffmpeg, path, start_sec, filters, *, chunk_sec=5.0, read_ahead_sec=14.0):
        self._chunk_bytes = max(BYTES_PER_FRAME, seconds_to_bytes(chunk_sec))
        self._max_buffered = max(self._chunk_bytes, seconds_to_bytes(read_ahead_sec))
        self._chunks = deque()
        self._buffered = 0
        self._done = False
        self._closed = False
        self._returncode = None
        self._err = bytearray()
        self._cond = threading.Condition()
        self._proc = subprocess.Popen(
            decode_args(ffmpeg, path, start_sec=start_sec, filters=filters),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=_NO_WINDOW)
        self._err_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._err_thread.start()
        self._thread.start()

    def _drain_stderr(self):
        try:
            for line in iter(lambda: self._proc.stderr.read1(4096), b''):
                self._err += line
                if len(self._err) > _ERR_TAIL:
                    del self._err[:-_ERR_TAIL]
        except (OSError, ValueError):
            pass

    def _push(self, chunk):
        with self._cond:
            self._chunks.append(chunk)
            self._buffered += len(chunk)

    def _read(self):
        buf = bytearray()
        try:
            while True:
                with self._cond:
                    while self._buffered >= self._max_buffered and not self._closed:
                        self._cond.wait(0.1)
                    if self._closed:
                        return
                data = self._proc.stdout.read1(65536)
                if not data:
                    break
                buf += data
                while len(buf) >= self._chunk_bytes:
                    self._push(bytes(buf[:self._chunk_bytes]))
                    del buf[:self._chunk_bytes]
            whole = len(buf) - len(buf) % BYTES_PER_FRAME
            if whole:
                self._push(bytes(buf[:whole]))
            try:
                self._returncode = self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._returncode = -1
        except (OSError, ValueError):
            if not self._closed:
                self._returncode = -1
        finally:
            with self._cond:
                self._done = True
                self._cond.notify_all()

    def read_chunk(self):
        with self._cond:
            if self._closed:
                return None
            if self._chunks:
                chunk = self._chunks.popleft()
                self._buffered -= len(chunk)
                self._cond.notify_all()
                return chunk
            return None if self._done else b''

    def failed(self):
        if self._closed or not self._done or self._returncode in (0, None):
            return None
        self._err_thread.join(timeout=0.5)
        msg = self._err.decode('utf-8', 'replace').strip()
        return msg or f'ffmpeg exited with code {self._returncode}'

    def buffered_seconds(self):
        with self._cond:
            return self._buffered / BYTES_PER_SEC

    def process_alive(self):
        return self._proc.poll() is None

    def close(self):
        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._chunks.clear()
            self._buffered = 0
            self._cond.notify_all()
        if self._proc.poll() is None:
            try:
                self._proc.kill()
            except OSError:
                pass
        for stream in (self._proc.stdout, self._proc.stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                pass
        self._thread.join(timeout=1.0)
        try:
            self._proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass


class PygameChannel:
    """ChannelPort over a pygame.mixer.Channel (mixer must be float32 48 kHz stereo)."""

    def __init__(self, channel):
        import pygame
        self._pg = pygame
        self._ch = channel

    def play(self, pcm, fade_ms=0):
        self._ch.play(self._pg.mixer.Sound(buffer=pcm), fade_ms=fade_ms)

    def queue(self, pcm):
        self._ch.queue(self._pg.mixer.Sound(buffer=pcm))

    def queue_free(self):
        return self._ch.get_queue() is None

    def busy(self):
        return bool(self._ch.get_busy())

    def set_volume(self, v):
        self._ch.set_volume(max(0.0, min(1.0, v)))

    def fadeout(self, ms):
        self._ch.fadeout(ms)

    def pause(self):
        self._ch.pause()

    def resume(self):
        self._ch.unpause()

    def stop(self):
        self._ch.stop()


def analyze_file(ffmpeg, path, timeout=60.0, *, low_priority=False):
    """Measure a file with FFmpeg's ebur128 filter. Raises RuntimeError on any failure."""
    flags = _NO_WINDOW | (_BELOW_NORMAL if low_priority else 0)
    try:
        r = subprocess.run(analyze_args(ffmpeg, path), stdin=subprocess.DEVNULL, capture_output=True,
                           timeout=timeout, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f'loudness analysis failed: {e}') from e
    text = r.stderr.decode('utf-8', 'replace')
    if r.returncode != 0:
        raise RuntimeError(f'ffmpeg exited with code {r.returncode}: {text[-_ERR_TAIL:].strip()}')
    try:
        return parse_ebur128(text)
    except ValueError as e:
        raise RuntimeError(f'loudness analysis failed: {e}') from e
