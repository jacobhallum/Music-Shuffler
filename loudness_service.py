"""
loudness_service.py - per-song loudness analysis with a persistent cache and a low-priority background scan.

The sound engine needs each song's integrated loudness and true peak (leveling) and its start/end loudness
envelope (smart transitions). FFmpeg measures a song in ~0.2 s; results are cached in loudness_cache.json keyed
by path + size + mtime, so a changed file is measured again. The cache loads on the worker thread, so the GUI
never waits for it: get() simply returns None until it is ready.
"""

import os
import threading
from collections import deque

from audio_backend import analyze_file
from loudness import cache_key, from_record, load_cache, save_cache, to_record

FLUSH_EVERY = 50
LOAD_WAIT_SEC = 10.0   # analyze_now gives up (None) if the cache hasn't loaded by then


class LoudnessService:
    def __init__(self, ffmpeg, cache_path, *, analyze=None):
        self._cache_path = cache_path
        self._analyze = analyze or (lambda path: analyze_file(ffmpeg, path, low_priority=True))
        self._lock = threading.Lock()
        self._records = None
        self._bad = set()
        self._inflight = {}                # cache key -> Event set when its analysis ends
        self._save_lock = threading.Lock()  # save_cache writes one fixed .tmp file
        self._dirty = 0
        self._queue = deque()
        self._total = 0
        self._done = 0
        self._loaded = threading.Event()
        self._run = threading.Event()      # set = background scan may run
        self._run.set()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True, name='loudness-scan')
        self._thread.start()

    # -- public -----------------------------------------------------------------
    def ready(self):
        return self._loaded.is_set()

    def get(self, path):
        """Cached analysis for `path`, or None (not analysed, changed since, or cache not loaded yet)."""
        key = self._key(path)
        if key is None or not self._loaded.is_set():
            return None
        with self._lock:
            rec = self._records.get(key)
        return from_record(rec) if rec is not None else None

    def known_bad(self, path):
        key = self._key(path)
        with self._lock:
            return key is not None and key in self._bad

    def analyze_now(self, path):
        """Analyse `path` now (blocking, ~0.2 s) unless cached; None if it can't be analysed.

        A second caller for a song already being analysed waits for that run instead of starting FFmpeg again.
        """
        if not self._loaded.wait(LOAD_WAIT_SEC):
            return None
        key = self._key(path)
        if key is None:
            return None
        while True:
            with self._lock:
                rec = self._records.get(key)
                if rec is not None:
                    return from_record(rec)
                if key in self._bad:
                    return None
                busy = self._inflight.get(key)
                if busy is None:
                    busy = self._inflight[key] = threading.Event()
                    break
            busy.wait()
        try:
            try:
                result = self._analyze(path)
            except Exception:
                with self._lock:
                    self._bad.add(key)
                return None
            with self._lock:
                self._records[key] = to_record(result)
                self._dirty += 1
                flush = self._dirty >= FLUSH_EVERY
        finally:
            with self._lock:
                self._inflight.pop(key, None)
            busy.set()
        if flush:
            self.flush()
        return result

    def forget_failures(self):
        """Let songs that failed to analyse be tried again (e.g. FFmpeg was missing and is now set)."""
        with self._lock:
            self._bad.clear()

    def start_background(self, paths):
        with self._lock:
            self._queue = deque(paths)
            self._total = len(self._queue)
            self._done = 0
        self._wake.set()

    def pause_background(self):
        self._run.clear()

    def resume_background(self):
        self._run.set()
        self._wake.set()

    def status(self):
        with self._lock:
            return self._done, self._total

    def flush(self):
        with self._save_lock:
            with self._lock:
                if not self._dirty or self._records is None:
                    return
                snapshot = dict(self._records)
                self._dirty = 0
            try:
                save_cache(self._cache_path, snapshot)
            except OSError as e:
                print(f"⚠ Could not save loudness cache: {e}")

    def stop(self):
        self._stop.set()
        self._run.set()
        self._wake.set()
        self._thread.join(timeout=2.0)
        self.flush()

    # -- internals ----------------------------------------------------------------
    @staticmethod
    def _key(path):
        try:
            st = os.stat(path)
        except (OSError, TypeError, ValueError):
            return None
        return cache_key(path, st.st_size, st.st_mtime)

    def _worker(self):
        records = load_cache(self._cache_path)
        with self._lock:
            self._records = records
        self._loaded.set()
        while not self._stop.is_set():
            if not self._run.is_set():
                self._run.wait(0.5)
                continue
            with self._lock:
                path = self._queue.popleft() if self._queue else None
            if path is None:
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            self.analyze_now(path)
            with self._lock:
                self._done += 1
                finished = not self._queue
            if finished:
                self.flush()
