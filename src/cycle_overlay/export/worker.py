"""Export im Hintergrund-Thread mit Fortschritts-Signalen (PRD 3.5)."""

from __future__ import annotations

import logging
import threading
import time
from fractions import Fraction
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal, Slot

from ..core.models import Telemetry
from ..rendering.layout import Layout
from .ffmpeg_exporter import ExportCancelled, ExportError, Progress, run_export
from .profiles import ExportProfile

log = logging.getLogger(__name__)


class ExportWorker(QObject):
    progress = Signal(int, int, float, float)  # Frame, Gesamt, FPS, Restzeit (s)
    succeeded = Signal(object)  # ExportResult
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, layout: Layout, telemetry: Telemetry, track_times: np.ndarray, fps: Fraction,
                 width: int, height: int, profile: ExportProfile, target: Path, units: str, tz: str | None,
                 map_data=None, count_numbers: bool = True, background=None, quality: str = "standard"):
        super().__init__()
        # Kopie, damit Änderungen in der Oberfläche den laufenden Export nicht beeinflussen
        self.layout = Layout.from_dict(layout.to_dict())
        self.telemetry = telemetry
        self.track_times = np.asarray(track_times, dtype=float)
        self.fps = fps
        self.width, self.height = width, height
        self.profile = profile
        self.target = target
        self.units, self.tz = units, tz
        self.map_data = map_data
        self.count_numbers = count_numbers
        #: Video unter den Anzeigen (nur bei Profilen mit ``with_video``)
        self.background = background
        self.quality = quality
        self._cancel = threading.Event()
        self._last = 0.0

    def cancel(self) -> None:
        self._cancel.set()

    def _on_progress(self, pr: Progress) -> None:
        now = time.monotonic()
        if now - self._last >= 0.1 or pr.frame == pr.total:
            self._last = now
            self.progress.emit(pr.frame, pr.total, pr.fps, pr.eta_s)

    @Slot()
    def run(self) -> None:
        from ..rendering.canvas_renderer import CanvasRenderer
        from ..rendering.values import FrameValues

        try:
            values = FrameValues(self.telemetry, self.track_times, units=self.units, tz=self.tz, map_data=self.map_data,
                                 count_numbers=self.count_numbers)
            renderer = CanvasRenderer(self.layout, values, self.width, self.height)
            res = run_export(renderer.frame_bytes, int(self.track_times.size), self.width, self.height, self.fps,
                             self.profile, self.target, progress=self._on_progress, cancel=self._cancel,
                             background=self.background, quality=self.quality)
        except ExportCancelled:
            self.cancelled.emit()
        except ExportError as exc:
            self.failed.emit(str(exc))
        except OSError as exc:
            log.exception("Export fehlgeschlagen")
            from ..platform_utils import blocked_message, is_blocked_error

            if is_blocked_error(exc):
                self.failed.emit(str(exc) if hasattr(exc, "exe") else blocked_message(getattr(exc, "filename", None) or "ffmpeg"))
            else:
                self.failed.emit(f"Datei- oder Systemfehler: {exc}")
        except Exception as exc:  # noqa: BLE001
            log.exception("Export fehlgeschlagen")
            self.failed.emit(f"Unerwarteter Fehler: {exc}")
        else:
            self.succeeded.emit(res)


def start_export(worker: ExportWorker, parent: QObject | None = None) -> QThread:
    thread = QThread(parent)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    for sig in (worker.succeeded, worker.failed, worker.cancelled):
        sig.connect(thread.quit)
    thread.start()
    return thread
