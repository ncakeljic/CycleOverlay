"""Verbindung zwischen ProjectModel und den Qt-Panels.

Alle Panels lesen aus ``controller.model`` und melden Änderungen über den Controller;
der Controller verteilt sie per Signal an alle anderen.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from ..app_paths import analysis_cache_dir, clear_session_cache
from ..core.project import Project, ProjectModel
from ..core.sync_timeline import OffsetHistory, snap_to_frame
from ..core.video_motion import (
    MatchScore,
    SyncSuggestion,
    VideoAnalysis,
    analyze_video,
    match_at,
    suggest_offsets,
    track_turn_rate,
)
from ..core.video_frames import VideoFrameSource, VideoStream, preview_size

log = logging.getLogger(__name__)


class _Task(QObject):
    """Hintergrundaufgabe in einem Daemon-Thread; Ergebnis/Fehler kommen als Signal im GUI-Thread an.

    Bewusst kein QThread: Ein laufender QThread (z. B. ein Kartendownload, der auf den Server
    wartet) würde beim Beenden der App einen Absturz auslösen („QThread destroyed while running“).
    Daemon-Threads werden beim Beenden einfach mit beendet.
    """

    done = Signal(object)
    failed = Signal(str)
    finished = Signal()
    # interne Signale aus dem Hintergrund-Thread; ihre Empfänger sind Methoden dieses (im GUI-Thread
    # lebenden) Objekts -> Qt stellt sie in die Ereignisschleife des GUI-Threads. Erst dort werden
    # done/failed/finished ausgelöst, sodass alle angeschlossenen Funktionen im GUI-Thread laufen.
    _bg_done = Signal(object)
    _bg_failed = Signal(str)
    _bg_finished = Signal()

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._thread: threading.Thread | None = None
        self._bg_done.connect(self._relay_done)
        self._bg_failed.connect(self._relay_failed)
        self._bg_finished.connect(self._relay_finished)

    def _relay_done(self, result) -> None:
        self.done.emit(result)

    def _relay_failed(self, msg: str) -> None:
        self.failed.emit(msg)

    def _relay_finished(self) -> None:
        self.finished.emit()

    def start(self) -> None:
        self._thread = threading.Thread(target=self.run, daemon=True, name="cycleoverlay-task")
        self._thread.start()

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001 – Fehler an die Oberfläche melden
            log.exception("Hintergrundaufgabe fehlgeschlagen")
            self._bg_failed.emit(str(exc) or exc.__class__.__name__)
        else:
            self._bg_done.emit(result)
        finally:
            self._bg_finished.emit()

    def isRunning(self) -> bool:  # noqa: N802 (wie QThread)
        return self._thread is not None and self._thread.is_alive()

    def wait(self, ms: int = 0) -> bool:
        if self._thread is not None:
            self._thread.join(ms / 1000 if ms else None)
        return not self.isRunning()


class _FrameLoader(QObject):
    """Lädt Videobilder im Hintergrund (Daemon-Thread); nur die jeweils neueste Anfrage zählt."""

    loaded = Signal(float, object, object)  # Zeit, Bild, Quelle (um Bilder eines alten Videos zu verwerfen)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source: VideoFrameSource | None = None
        self._request: float | None = None
        self._event = threading.Event()
        self._stop = False
        self._thread: threading.Thread | None = None

    def set_source(self, source: VideoFrameSource | None) -> None:
        self._source = source

    def isRunning(self) -> bool:  # noqa: N802
        return self._thread is not None and self._thread.is_alive()

    def request(self, t: float) -> None:
        self._request = t
        self._event.set()
        if not self.isRunning() and not self._stop:
            self._thread = threading.Thread(target=self.run, daemon=True, name="cycleoverlay-frames")
            self._thread.start()

    def stop(self) -> None:
        self._stop = True
        self._event.set()
        if self._thread is not None:
            self._thread.join(3.0)

    def run(self) -> None:
        while not self._stop:
            if not self._event.wait(0.5):
                continue
            self._event.clear()
            t, self._request = self._request, None
            src = self._source
            if t is None or src is None or self._stop:
                continue
            try:
                frame = src.grab(t)
            except Exception:  # noqa: BLE001 – ein unlesbares Bild darf den Lader nicht beenden
                log.exception("Videobild bei %.3f s nicht lesbar", t)
                frame = None
            self.loaded.emit(t, frame, src)


class Controller(QObject):
    projectChanged = Signal()  # GPX/Video/Sync/Exporteinstellungen geändert -> alles neu lesen
    layoutChanged = Signal(object)  # Widgets geändert; Argument = Absender (oder None)
    selectionChanged = Signal(str)  # Widget-ID oder ""
    positionChanged = Signal(float)  # Timeline-Zeit
    backgroundChanged = Signal()  # neues Videobild
    playingChanged = Signal(bool)
    busyChanged = Signal(bool, str)
    message = Signal(str)
    error = Signal(str, str)  # Titel, Text
    dirtyChanged = Signal(bool)
    analysisChanged = Signal()  # Videoanalyse (Bewegung/Filmstreifen) Fortschritt oder Ergebnis
    suggestionChanged = Signal()  # Auto-Sync-Vorschlag neu/entfernt

    def __init__(self, model: ProjectModel | None = None, parent=None):
        super().__init__(parent)
        self.model = model or ProjectModel()
        self.project_path: Path | None = None
        self.position = 0.0
        self.selected = ""
        self.dirty = False
        self.background: np.ndarray | None = None  # aktuelles Videobild (RGB)
        self._tasks: list[_Task] = []
        self._frames: VideoFrameSource | None = None
        self._loader = _FrameLoader(self)
        self._loader.loaded.connect(self._on_frame_loaded)
        self._stream: VideoStream | None = None
        self._play_timer = QTimer(self)
        self._play_timer.timeout.connect(self._tick)
        self._play_clock = 0.0
        self._play_origin = 0.0
        # Sync
        self.sync_mode = False  # Sync-Tab aktiv: Pfeiltasten verschieben das Video
        self.offset_history = OffsetHistory()
        #: womit Auto-Sync vergleicht: auto | audio (Fahrtwind) | video (Bild) | all
        self.sync_signals = "auto"
        self._drag_before: float | None = None
        # Videoanalyse für die Sync-Zeitleiste
        self.analysis: VideoAnalysis | None = None
        self.analysis_progress: float | None = None  # None = läuft nicht
        self.analysis_error: str | None = None
        self.suggestions: list[SyncSuggestion] = []  # Auto-Sync, beste zuerst
        self.suggestion_index = 0
        self._probe_origin: float | None = None
        self._match_cache: tuple | None = None
        self._analysis_task: _Task | None = None
        self._analysis_cancel: threading.Event | None = None
        self._analysis_path: str | None = None
        self._analysis_timer = QTimer(self)
        self._analysis_timer.setInterval(250)
        self._analysis_timer.timeout.connect(self.analysisChanged.emit)

    # ------------------------------------------------------------------ Hilfen

    def _run(self, label: str, fn, on_done, on_error=None, block_ui: bool = True) -> None:
        if block_ui:
            self.busyChanged.emit(True, label)
        else:
            self.message.emit(f"{label} …")
        task = _Task(fn, self)
        self._tasks.append(task)

        def finished_ok(result):
            if block_ui:
                self.busyChanged.emit(False, "")
            on_done(result)

        def finished_err(msg):
            if block_ui:
                self.busyChanged.emit(False, "")
            if on_error is not None:
                on_error()
            if "abgebrochen" in msg.lower():
                self.message.emit(msg)
            else:
                self.error.emit(label, msg)

        task.done.connect(finished_ok)
        task.failed.connect(finished_err)
        task.finished.connect(lambda: self._tasks.remove(task) if task in self._tasks else None)
        task.start()

    def wait_idle(self, timeout_ms: int = 30000) -> None:
        """Für Tests: warten, bis Hintergrundaufgaben fertig sind."""
        from PySide6.QtWidgets import QApplication

        deadline = time.monotonic() + timeout_ms / 1000
        while self._tasks and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)
        QApplication.processEvents()

    def set_dirty(self, dirty: bool = True) -> None:
        if dirty != self.dirty:
            self.dirty = dirty
            self.dirtyChanged.emit(dirty)

    # ------------------------------------------------------------------ Laden

    def open_gpx(self, path: str | Path) -> None:
        self.stop()

        def done(_):
            self._after_data_change()
            self.message.emit(f"GPX geladen: {Path(path).name}")

        self._run("GPX-Datei laden", lambda: self.model.load_gpx(path), done)

    def open_video(self, path: str | Path) -> None:
        self.stop()

        def done(_):
            self._setup_frames()
            self.position = 0.0
            self._after_data_change()
            chk = self.model.sync_check()
            if self.model.sync_error:
                self.error.emit("Video-Synchronisation", self.model.sync_error)
            elif chk is not None and chk.status != "ok":
                self.message.emit(chk.message)
            else:
                self.message.emit(f"Video geladen: {Path(path).name}")

        self._run("Video analysieren", lambda: self.model.load_video(path), done)

    def relink_video(self, path: str | Path) -> None:
        self.stop()

        def done(_):
            self._setup_frames()
            self._after_data_change()
            self.message.emit(f"Video neu verknüpft: {Path(path).name}")

        self._run("Video neu verknüpfen", lambda: self.model.relink_video(path), done)

    mapDownloadRunning = Signal(bool)

    @property
    def map_download_running(self) -> bool:
        return getattr(self, "_map_cancel", None) is not None

    def cancel_map_download(self) -> None:
        ev = getattr(self, "_map_cancel", None)
        if ev is not None and not ev.is_set():
            ev.set()
            self.message.emit("Kartendownload wird abgebrochen …")

    def download_map_data(self) -> None:
        self._map_cancel = threading.Event()
        self.mapDownloadRunning.emit(True)
        model = self.model  # das Projekt, für das geladen wird (falls inzwischen ein anderes offen ist)
        cancel = self._map_cancel

        def finished() -> None:
            if self._map_cancel is cancel:
                self._map_cancel = None
            self.mapDownloadRunning.emit(False)

        def done(md):
            finished()
            if model is not self.model:
                return
            self.layoutChanged.emit(None)
            self.projectChanged.emit()
            self.set_dirty()
            self.message.emit(f"Kartendaten geladen: {self.model.map_status()}")

        def failed():
            finished()

        # Fortschritt aus dem Hintergrund-Thread über das (thread-sichere) Signal in die Statuszeile
        self._run("Kartendaten laden (OpenStreetMap)",
                  lambda c=self._map_cancel: model.download_map_data(progress=self.message.emit, cancel=c), done,
                  on_error=failed, block_ui=False)

    def clear_map_data(self) -> None:
        self.model.map_data = None
        self.layoutChanged.emit(None)
        self.projectChanged.emit()
        self.set_dirty()

    def remove_video(self) -> None:
        self.stop()
        self.model.clear_video()
        self._setup_frames()
        self._after_data_change()

    def close_project_data(self) -> None:
        """Daten des bisherigen Projekts verwerfen (heruntergeladene Kartendaten löschen)."""
        self.model.map_data = None
        clear_session_cache()

    def new_project(self) -> None:
        self.stop()
        self.cancel_map_download()
        self.close_project_data()
        self.model = ProjectModel(Project())
        self.project_path = None
        self._setup_frames()
        self.position = 0.0
        self.select("")
        self._after_data_change(dirty=False)

    def open_project(self, path: str | Path) -> None:
        self.stop()

        def done(model):
            # erst jetzt das bisherige Projekt schließen – schlägt das Öffnen fehl, bleibt es unverändert
            self.cancel_map_download()
            self.close_project_data()
            self.model = model
            self.project_path = Path(path)
            self._setup_frames()
            self.position = 0.0
            self.select("")
            self._after_data_change(dirty=False)
            missing = model.missing_media
            if missing:
                self.error.emit("Projekt öffnen",
                                "Folgende Medien sind offline:\n" + "\n".join(missing)
                                + "\n\nÜber „Video neu verknüpfen …“ (links) kannst du sie wieder zuordnen.")

        self._run("Projekt öffnen", lambda: ProjectModel.open(path), done)

    def save_project(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path else self.project_path
        if target is None:
            raise ValueError("Kein Speicherort angegeben.")
        self.project_path = self.model.save(target)
        self.set_dirty(False)
        self.message.emit(f"Projekt gespeichert: {self.project_path.name}")
        return self.project_path

    def _after_data_change(self, dirty: bool = True) -> None:
        self.position = min(max(self.position, 0.0), self.model.timeline_duration())
        if not self.model.has_video and self.model.ready:
            a, _ = self.model.export_range()
            self.position = a
        self.projectChanged.emit()
        self.layoutChanged.emit(None)
        self.positionChanged.emit(self.position)
        self._request_background()
        self.set_dirty(dirty)

    def project_edited(self) -> None:
        """Sync-/Export-/Einheiten-Einstellungen wurden im Modell geändert."""
        self.position = min(max(self.position, 0.0), self.model.timeline_duration())
        self.projectChanged.emit()
        self.positionChanged.emit(self.position)
        self.set_dirty()

    def reprocess(self) -> None:
        self.model.reprocess()
        self.data_reprocessed()

    def data_reprocessed(self) -> None:
        """Fahrt wurde mit neuen Filtern neu berechnet: alles neu zeichnen."""
        self._match_cache = None
        self.project_edited()
        self.layoutChanged.emit(None)

    # ------------------------------------------------------------------ Widgets

    def select(self, wid: str) -> None:
        if wid != self.selected:
            self.selected = wid
            self.selectionChanged.emit(wid)

    def layout_edited(self, sender=None) -> None:
        self.layoutChanged.emit(sender)
        self.set_dirty()

    def add_widget(self, channel: str, wtype: str) -> None:
        cfg = self.model.add_widget(channel, wtype)
        self.layout_edited()
        self.select(cfg.id)

    def remove_selected(self) -> None:
        if self.selected:
            self.model.remove_widget(self.selected)
            self.select("")
            self.layout_edited()

    def duplicate_selected(self) -> None:
        if self.selected:
            cfg = self.model.duplicate_widget(self.selected)
            if cfg:
                self.layout_edited()
                self.select(cfg.id)

    def set_layout(self, layout) -> None:
        # Die Sprache ist eine Einstellung des Projekts – ein geladenes Preset soll sie nicht umstellen
        layout.language = self.model.project.layout.language
        self.model.set_layout(layout)
        self.select("")
        self.layout_edited()

    def set_language(self, language: str) -> None:
        """Sprache der Overlay-Texte (Beschriftungen, Anstiegs-Vorschau)."""
        from ..rendering.i18n import LANGUAGES

        if language not in LANGUAGES:
            raise ValueError(f"Unbekannte Sprache: {language!r}")
        if self.model.project.layout.language != language:
            self.model.project.layout.language = language
            self.layout_edited()

    # ------------------------------------------------------------------ Position & Video

    def track_time(self) -> float:
        return self.model.track_time(self.position)

    def set_position(self, t: float) -> None:
        t = min(max(float(t), 0.0), self.model.timeline_duration())
        self.position = t
        self.positionChanged.emit(t)
        if not self.is_playing:
            self._request_background()

    def step_frames(self, n: int) -> None:
        self.set_position(self.position + n / float(self.model.fps()))

    def _setup_frames(self) -> None:
        self._start_analysis()
        self.background = None
        if self.model.has_video:
            v = self.model.video
            w, h = preview_size(v.width, v.height)
            self._frames = VideoFrameSource(v.path, w, h, v.fps)
        else:
            self._frames = None
        self._loader.set_source(self._frames)

    def _request_background(self) -> None:
        if self._frames is not None:
            self._loader.request(self.position)
        elif self.background is not None:
            self.background = None
            self.backgroundChanged.emit()

    def _on_frame_loaded(self, t: float, frame, source=None) -> None:
        if source is not None and source is not self._frames:
            return  # Bild eines inzwischen gewechselten Videos
        if frame is not None and not self.is_playing:
            self.background = frame
            self.backgroundChanged.emit()

    # ------------------------------------------------------------------ Wiedergabe

    @property
    def is_playing(self) -> bool:
        return self._play_timer.isActive()

    def play(self) -> None:
        if self.is_playing or not self.model.ready:
            return
        if self.position >= self.model.timeline_duration() - 1e-3:
            self.position = 0.0 if self.model.has_video else self.model.export_range()[0]
        if self._frames is not None:
            self._stream = self._frames.stream(self.position)
        self._play_origin = self.position
        self._play_clock = time.monotonic()
        self._play_timer.start(max(int(1000 / float(self.model.fps())), 10))
        self.playingChanged.emit(True)

    def stop(self) -> None:
        if self._play_timer.isActive():
            self._play_timer.stop()
            self.playingChanged.emit(False)
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        self._request_background()

    def toggle_play(self) -> None:
        self.stop() if self.is_playing else self.play()

    def _tick(self) -> None:
        dur = self.model.timeline_duration()
        if self._stream is not None:
            frame = self._stream.read(timeout=0.0)
            if frame is None:
                if self._stream.finished:
                    self.stop()
                return  # Dekodierung hinkt hinterher: Position erst mit dem nächsten Bild weiter
            self.background = frame
            self.position = min(self.position + 1 / float(self.model.fps()), dur)
            self.backgroundChanged.emit()
        else:
            self.position = min(self._play_origin + (time.monotonic() - self._play_clock), dur)
        self.positionChanged.emit(self.position)
        if self.position >= dur - 1e-6:
            self.stop()

    def shutdown(self) -> None:
        self.cancel_map_download()
        self._cancel_analysis(wait=True)
        self.stop()
        self.close_project_data()
        self._loader.stop()
        for t in list(self._tasks):
            t.wait(5000)

    # ------------------------------------------------------------------ Versatz (Sync-Zeitleiste)

    def snap_offset(self, value: float) -> float:
        return snap_to_frame(value, float(self.model.fps())) if self.model.has_video else float(value)

    def set_offset(self, value: float, record: bool = True) -> None:
        """Versatz setzen (auf Frames eingerastet); ``record`` = für Rückgängig merken."""
        if not self.model.has_video:
            return
        before = self.model.project.offset_s
        value = self.snap_offset(value)
        if abs(value - before) < 1e-9:
            return
        self.model.set_sync_options(offset_s=value)
        if record:
            self.offset_history.record(before, value)
        self.project_edited()

    def nudge_offset(self, seconds: float = 0.0, frames: int = 0) -> None:
        if not self.model.has_video:
            return
        step = seconds + frames / float(self.model.fps())
        self.set_offset(self.model.project.offset_s + step)

    def begin_offset_drag(self) -> None:
        self._drag_before = self.model.project.offset_s

    def end_offset_drag(self) -> None:
        if self._drag_before is not None:
            self.offset_history.record(self._drag_before, self.model.project.offset_s)
            self._drag_before = None
            self.projectChanged.emit()  # Rückgängig-Knöpfe aktualisieren

    def undo_offset(self) -> None:
        v = self.offset_history.undo(self.model.project.offset_s)
        if v is not None:
            self.model.set_sync_options(offset_s=v)
            self.project_edited()

    def redo_offset(self) -> None:
        v = self.offset_history.redo(self.model.project.offset_s)
        if v is not None:
            self.model.set_sync_options(offset_s=v)
            self.project_edited()

    def sync_point(self, track_t: float) -> float | None:
        """Aktuelles Videobild (Abspielkopf) auf Track-Zeit ``track_t`` legen."""
        if not self.model.has_video:
            return None
        before = self.model.project.offset_s
        off = self.model.set_sync_point(self.position, track_t)
        self.offset_history.record(before, off)
        self.project_edited()
        return off

    # ------------------------------------------------------------------ Videoanalyse

    def _cancel_analysis(self, wait: bool = False) -> None:
        if self._analysis_cancel is not None:
            self._analysis_cancel.set()
        task = self._analysis_task
        if wait and task is not None:
            task.wait(10000)

    def _start_analysis(self) -> None:
        """Bewegungskurve und Filmstreifen des aktuellen Videos im Hintergrund berechnen."""
        v = self.model.video if self.model.has_video else None
        path = str(v.path) if v is not None else None
        if path == self._analysis_path and (self.analysis is not None or self.analysis_progress is not None):
            return
        self._cancel_analysis()
        self.offset_history.clear()
        self._analysis_path = path
        self.analysis = None
        self.analysis_error = None
        self._probe_origin = None
        self.suggestions = []
        self.analysis_progress = None
        self.suggestionChanged.emit()
        if v is None:
            self._analysis_timer.stop()
            self.analysisChanged.emit()
            return
        cancel = threading.Event()
        self._analysis_cancel = cancel
        self.analysis_progress = 0.0

        def progress(f: float) -> None:
            if not cancel.is_set():
                self.analysis_progress = f

        def work():
            return analyze_video(v.path, v.width, v.height, v.duration, cache_dir=analysis_cache_dir(),
                                 progress=progress, cancel=cancel)

        task = _Task(work, self)
        self._analysis_task = task

        def ok(res):
            if cancel.is_set():
                return
            self.analysis = res
            self.analysis_progress = None
            self._analysis_timer.stop()
            self.analysisChanged.emit()

        def failed(msg):
            if cancel.is_set():
                return
            self.analysis_error = msg
            self.analysis_progress = None
            self._analysis_timer.stop()
            self.analysisChanged.emit()
            log.warning("Videoanalyse: %s", msg)

        task.done.connect(ok)
        task.failed.connect(failed)
        task.finished.connect(lambda: setattr(self, "_analysis_task", None) if self._analysis_task is task else None)
        task.start()
        self._analysis_timer.start()
        self.analysisChanged.emit()

    @property
    def suggestion(self) -> SyncSuggestion | None:
        """Der gerade gewählte Auto-Sync-Vorschlag."""
        if not self.suggestions:
            return None
        return self.suggestions[min(self.suggestion_index, len(self.suggestions) - 1)]

    def _sync_inputs(self) -> dict | None:
        """Gemeinsame Eingaben für Auto-Sync und Übereinstimmung (Drehrate wird zwischengespeichert)."""
        m = self.model
        if self.analysis is None or not m.has_video or m.telemetry is None:
            return None
        tel = m.telemetry
        ch = tel.channels
        turn = tel.cache.get("turn_rate")
        if turn is None and np.isfinite(ch["lat"]).any():
            turn = track_turn_rate(tel.t, ch["lat"], ch["lon"], ch["distance"], ch["speed"])
            tel.cache["turn_rate"] = turn
        return dict(motion_t=self.analysis.motion_t, motion=self.analysis.motion, track_t=tel.t,
                    speed=ch["speed"], yaw=self.analysis.yaw, turn_rate=turn,
                    track_moving=(np.nan_to_num(ch["speed"]) >= 1.0) & ~tel.pause_mask,
                    wind=self.analysis.wind, signals=self.sync_signals)

    def match_now(self) -> MatchScore | None:
        """Übereinstimmung von Video und Fahrt beim aktuellen Versatz."""
        inp = self._sync_inputs()
        if inp is None:
            return None
        key = (round(self.model.project.offset_s, 4), id(self.analysis), id(self.model.telemetry),
               round(self.model.sync.base_offset_s, 3), self.sync_signals)
        if self._match_cache is not None and self._match_cache[0] == key:
            return self._match_cache[1]
        start = float(self.model.sync.track_time(0.0))
        res = match_at(start, **inp)
        self._match_cache = (key, res)
        return res

    def compute_suggestions(self) -> list[SyncSuggestion]:
        """Auto-Sync: bis zu 3 Vorschläge; das Video wird gleich probeweise auf den besten gelegt.

        ◀/▶ legt es auf die anderen, „Übernehmen“ behält die Stelle, „Verwerfen“ stellt den alten
        Versatz wieder her.
        """
        m = self.model
        if not self.suggestions:
            self._probe_origin = m.project.offset_s if m.has_video else None
        self.suggestions = []
        self.suggestion_index = 0
        inp = self._sync_inputs()
        if inp is not None:
            self.suggestions = suggest_offsets(
                inp["motion_t"], inp["motion"], inp["track_t"], inp["speed"], m.sync.base_offset_s,
                float(m.video.fps), yaw=inp["yaw"], turn_rate=inp["turn_rate"], track_moving=inp["track_moving"],
                wind=inp["wind"], signals=inp["signals"])
        if self.suggestions:
            self._place_on_suggestion()
        self.suggestionChanged.emit()
        return self.suggestions

    def compute_suggestion(self) -> SyncSuggestion | None:
        self.compute_suggestions()
        return self.suggestion

    def _place_on_suggestion(self) -> None:
        s = self.suggestion
        if s is not None and self.model.has_video:
            self.set_offset(s.offset_s, record=False)

    def select_suggestion(self, step: int) -> None:
        if self.suggestions:
            self.suggestion_index = (self.suggestion_index + step) % len(self.suggestions)
            self._place_on_suggestion()
            self.suggestionChanged.emit()

    def show_suggestion(self, index: int) -> None:
        """Video direkt auf Vorschlag ``index`` legen (z. B. Klick auf den grünen Rahmen)."""
        if 0 <= index < len(self.suggestions):
            self.suggestion_index = index
            self._place_on_suggestion()
            self.suggestionChanged.emit()

    def clear_suggestions(self, restore: bool = True) -> None:
        """Vorschläge verwerfen; ``restore`` = Video zurück an die Stelle vor Auto-Sync."""
        origin = getattr(self, "_probe_origin", None)
        self.suggestions = []
        self.suggestion_index = 0
        self._probe_origin = None
        if restore and origin is not None and self.model.has_video:
            self.set_offset(origin, record=False)
        self.suggestionChanged.emit()

    def apply_suggestion(self) -> None:
        """Die aktuelle Lage behalten – also den Vorschlag samt eventueller Feinkorrektur (rückgängig machbar)."""
        if self.suggestion is None:
            return
        origin = getattr(self, "_probe_origin", None)
        if origin is not None:
            self.offset_history.record(origin, self.model.project.offset_s)
        self.clear_suggestions(restore=False)
        self.projectChanged.emit()
