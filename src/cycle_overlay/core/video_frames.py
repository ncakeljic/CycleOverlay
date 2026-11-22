"""Videobilder für die Vorschau per ffmpeg dekodieren (ohne Qt).

- ``VideoFrameSource.grab(t)`` liefert ein einzelnes Bild (zum Scrubben), mit kleinem Cache.
- ``VideoStream`` liest fortlaufend Bilder ab einer Startzeit (für die Wiedergabe).

Die Vorschau wird in reduzierter Auflösung dekodiert; der Export ist davon unabhängig.
"""

from __future__ import annotations

import collections
import logging
import subprocess
import threading
from fractions import Fraction
from os import PathLike
from pathlib import Path

import numpy as np

from ..export.ffmpeg_locator import find_executable
from ..platform_utils import popen, run, run_bytes

log = logging.getLogger(__name__)


def preview_size(src_w: int, src_h: int, max_w: int = 960) -> tuple[int, int]:
    """Vorschaugröße mit gleichem Seitenverhältnis, gerade Maße."""
    if src_w <= max_w:
        w, h = src_w, src_h
    else:
        w, h = max_w, round(src_h * max_w / src_w)
    return max(2, w - w % 2), max(2, h - h % 2)


class VideoFrameSource:
    def __init__(self, path: str | PathLike, width: int, height: int, fps: Fraction | float = 30,
                 ffmpeg: str | PathLike | None = None, cache_size: int = 48):
        self.path = Path(path)
        self.width = int(width)
        self.height = int(height)
        self.fps = float(fps)
        self.ffmpeg = str(ffmpeg or find_executable("ffmpeg") or "ffmpeg")
        self._cache: collections.OrderedDict[int, np.ndarray] = collections.OrderedDict()
        self._cache_size = cache_size
        self._lock = threading.Lock()

    @property
    def frame_bytes(self) -> int:
        return self.width * self.height * 3

    def _key(self, t: float) -> int:
        return int(round(max(t, 0.0) * self.fps))

    def grab(self, t: float) -> np.ndarray | None:
        """Bild zur Zeit ``t`` (Sekunden im Video) als RGB-Array (H, W, 3); None bei Fehler."""
        key = self._key(t)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        res = run_bytes(
            [self.ffmpeg, "-v", "error", "-ss", f"{key / self.fps:.4f}", "-i", str(self.path),
             "-frames:v", "1", "-vf", f"scale={self.width}:{self.height}:flags=bilinear",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            timeout=60,
        )
        if res.returncode != 0 or len(res.stdout) < self.frame_bytes:
            log.debug("Videobild bei %.3f s nicht lesbar: %s", t, res.stderr[-300:])
            return None
        frame = np.frombuffer(res.stdout[: self.frame_bytes], np.uint8).reshape(self.height, self.width, 3)
        with self._lock:
            self._cache[key] = frame
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return frame

    def stream(self, start: float) -> "VideoStream":
        return VideoStream(self, start)


class VideoStream:
    """Fortlaufende Wiedergabe ab ``start``; Bilder werden in einem Thread vorgelesen."""

    def __init__(self, source: VideoFrameSource, start: float, buffer: int = 8):
        self.source = source
        self.start = start
        self._queue: collections.deque[np.ndarray] = collections.deque()
        self._buffer = buffer
        self._cond = threading.Condition()
        self._stop = False
        self.finished = False
        self.proc = popen(
            [source.ffmpeg, "-v", "error", "-ss", f"{max(start, 0.0):.4f}", "-i", str(source.path),
             "-vf", f"scale={source.width}:{source.height}:flags=bilinear",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        )
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def _reader(self) -> None:
        n = self.source.frame_bytes
        assert self.proc.stdout
        try:
            while not self._stop:
                data = self.proc.stdout.read(n)
                if not data or len(data) < n:
                    break
                frame = np.frombuffer(data, np.uint8).reshape(self.source.height, self.source.width, 3)
                with self._cond:
                    while len(self._queue) >= self._buffer and not self._stop:
                        self._cond.wait(0.2)
                    self._queue.append(frame)
                    self._cond.notify_all()
        finally:
            with self._cond:
                self.finished = True
                self._cond.notify_all()

    def read(self, timeout: float = 1.0) -> np.ndarray | None:
        """Nächstes Bild oder None (Ende/Zeitüberschreitung)."""
        with self._cond:
            if not self._queue and not self.finished:
                self._cond.wait(timeout)
            if self._queue:
                frame = self._queue.popleft()
                self._cond.notify_all()
                return frame
            return None

    def close(self) -> None:
        self._stop = True
        with self._cond:
            self._cond.notify_all()
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self._thread.join(timeout=3)

    def __enter__(self) -> "VideoStream":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def has_ffmpeg() -> bool:
    exe = find_executable("ffmpeg")
    try:
        return exe is not None and run([exe, "-version"], timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
