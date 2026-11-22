"""Hauptfenster: 3-Spalten-Layout mit Timeline (PRD 3.5)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__
from ..core.presets import list_presets, load_preset, save_user_preset
from ..core.project import PROJECT_SUFFIX
from ..core.settings import AppSettings
from ..export.ffmpeg_locator import configure as configure_ffmpeg, install_hint, locate
from ..rendering.i18n import LANGUAGES
from ..rendering.layout import Layout
from .controller import Controller
from .export_panel import ExportPanel
from .filter_panel import FilterPanel
from .inspector_panel import InspectorPanel
from .preview_canvas import PreviewCanvas
from .signal_panel import SignalPanel
from .sync_panel import SyncPanel
from .sync_timeline import SyncTimeline
from .timeline_bar import TimelineBar

PROJECT_FILTER = f"CycleOverlay-Projekt (*{PROJECT_SUFFIX})"
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".insv", ".lrv"}


class MainWindow(QMainWindow):
    def __init__(self, controller: Controller | None = None):
        super().__init__()
        self.c = c = controller or Controller(parent=self)
        self.settings = AppSettings.load()
        configure_ffmpeg(self.settings.ffmpeg_path, self.settings.ffprobe_path)
        from .update_dialog import UpdateManager

        self.updates = UpdateManager(self)
        c.model.project.units = self.settings.units
        self.resize(1500, 900)
        self.setAcceptDrops(True)

        self.signals = SignalPanel(c)
        self.canvas = PreviewCanvas(c)
        self.timeline = TimelineBar(c)
        self.inspector = InspectorPanel(c)
        self.sync = SyncPanel(c)
        self.sync_timeline = SyncTimeline(c)
        self.sync_timeline.hide()
        self.export = ExportPanel(c)
        self.filters = FilterPanel(c)

        # Mitte: Ansichtsoptionen + Vorschau + Timeline
        self.opt_video = QCheckBox("Videobild")
        self.opt_video.setChecked(True)
        self.opt_video.toggled.connect(self._set_show_video)
        self.opt_safe = QCheckBox("Safe-Area")
        self.opt_safe.setChecked(True)
        self.opt_safe.toggled.connect(self._set_safe)
        self.opt_snap = QCheckBox("Raster (Alt = frei)")
        self.opt_snap.setChecked(True)
        self.opt_snap.toggled.connect(lambda v: setattr(self.canvas, "snap", v))
        opts = QHBoxLayout()
        opts.addWidget(QLabel("Vorschau:"))
        opts.addWidget(self.opt_video)
        opts.addWidget(self.opt_safe)
        opts.addWidget(self.opt_snap)
        opts.addStretch(1)
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addLayout(opts)
        cl.addWidget(self.canvas, 1)
        cl.addWidget(self.sync_timeline)
        cl.addWidget(self.timeline)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.inspector, "Widget")
        self.tabs.addTab(self.sync, "Sync")
        self.tabs.addTab(self.filters, "Filter")
        self.tabs.addTab(self.export, "Export")
        self.tabs.setMinimumWidth(340)
        self.tabs.currentChanged.connect(self._on_tab)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.signals)
        split.addWidget(center)
        split.addWidget(self.tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([300, 860, 360])
        self.setCentralWidget(split)

        self._build_menus()
        self.statusBar().showMessage("Bereit")
        c.message.connect(lambda m: self.statusBar().showMessage(m, 8000))
        c.error.connect(lambda title, text: QMessageBox.warning(self, title, text))
        c.busyChanged.connect(self._on_busy)
        c.dirtyChanged.connect(lambda _d: self._update_title())
        c.projectChanged.connect(self._update_title)
        c.selectionChanged.connect(lambda w: self.tabs.setCurrentWidget(self.inspector) if w else None)
        self._update_title()

    def _on_tab(self, _i: int) -> None:
        """Sync-Tab aktiv: Sync-Zeitleiste statt des Schiebereglers zeigen."""
        on = self.tabs.currentWidget() is self.sync
        self.c.sync_mode = on
        self.sync_timeline.setVisible(on)
        self.timeline.set_compact(on)
        if on:
            self.sync_timeline.refresh()
            self.sync_timeline.lanes.setFocus()

    # ------------------------------------------------------------------ Menüs

    def _action(self, menu, title: str, slot, shortcut=None) -> QAction:
        act = QAction(title, self)
        if shortcut is not None:
            act.setShortcut(shortcut if isinstance(shortcut, QKeySequence) else QKeySequence(shortcut))
        # Qt übergibt „checked“ (bool) an den Slot – Slots mit optionalen Parametern bekämen sonst
        # False statt ihres Standardwerts (so entstand der Fehler bei „Sync-Diagnose speichern“)
        act.triggered.connect(lambda _checked=False, f=slot: f())
        menu.addAction(act)
        return act

    def _build_menus(self) -> None:
        mb = self.menuBar()
        f = mb.addMenu("&Datei")
        self._action(f, "Neues Projekt", self.new_project, QKeySequence.StandardKey.New)
        self._action(f, "Projekt öffnen …", self.open_project_dialog, QKeySequence.StandardKey.Open)
        self._action(f, "Projekt speichern", self.save_project, QKeySequence.StandardKey.Save)
        self._action(f, "Projekt speichern unter …", self.save_project_as, QKeySequence.StandardKey.SaveAs)
        f.addSeparator()
        self._action(f, "Fahrt öffnen (GPX/FIT) …", self.signals.choose_gpx, "Ctrl+G")
        self._action(f, "Video öffnen …", self.signals.choose_video, "Ctrl+Shift+V")
        f.addSeparator()
        self._action(f, "Beenden", self.close, QKeySequence.StandardKey.Quit)

        lay = mb.addMenu("&Layout")
        self.preset_menu = lay.addMenu("Preset laden")
        self.preset_menu.aboutToShow.connect(self._fill_presets)
        self._action(lay, "Layout aus Datei laden …", self.load_layout_file)
        self._action(lay, "Layout als Preset speichern …", self.save_preset)
        self._action(lay, "Layout als Datei exportieren …", self.export_layout_file)
        lay.addSeparator()
        self._action(lay, "Widget hinzufügen …", self.signals.add_widget, "Ctrl+Shift+A")
        self._action(lay, "Ausgewähltes Widget duplizieren", self.c.duplicate_selected, "Ctrl+D")
        lay.addSeparator()
        self.language_menu = lay.addMenu("Sprache der Anzeige")
        self.language_group = QActionGroup(self)
        self.language_group.setExclusive(True)
        self.language_actions: dict[str, QAction] = {}
        for code, title in LANGUAGES.items():
            act = self.language_menu.addAction(title)
            act.setCheckable(True)
            act.triggered.connect(lambda _checked=False, c=code: self.c.set_language(c))
            self.language_group.addAction(act)
            self.language_actions[code] = act
        self.language_menu.aboutToShow.connect(self._sync_language_menu)
        self._sync_language_menu()

        play = mb.addMenu("&Wiedergabe")
        self._action(play, "Abspielen / Pause", self.c.toggle_play, "Space")
        self._action(play, "1 Frame vor", lambda: self.c.step_frames(1), "Ctrl+Right")
        self._action(play, "1 Frame zurück", lambda: self.c.step_frames(-1), "Ctrl+Left")

        h = mb.addMenu("&Hilfe")
        self._action(h, "Nach Updates suchen …", lambda: self.updates.check(manual=True))
        self._action(h, "ffmpeg prüfen …", self.check_ffmpeg)
        self._action(h, "Sync-Diagnose speichern …", self.save_sync_diagnostics)
        self._action(h, f"Über {APP_NAME}", self.about)

    def save_sync_diagnostics(self, path: str | None = None) -> Path | None:
        """Rohsignale von Video und Fahrt beim aktuellen (richtigen) Versatz speichern – zur Auswertung."""
        from ..core.video_motion import sync_diagnostics

        c, m = self.c, self.c.model
        if not m.has_video or m.telemetry is None or c.analysis is None:
            QMessageBox.information(self, "Sync-Diagnose",
                                    "Dafür müssen Fahrt und Video geladen und das Video analysiert sein.")
            return None
        if path is None:
            base = c.project_path.parent if c.project_path else Path.home()
            path, _ = QFileDialog.getSaveFileName(self, "Sync-Diagnose speichern", str(base / "syncdiag.npz"),
                                                  "Diagnose (*.npz)")
            if not path:
                return None
        tel = m.telemetry
        try:
            report = sync_diagnostics(path, c.analysis, tel.t, tel.channels, tel.pause_mask, m.sync.base_offset_s,
                                      float(m.video.fps), float(m.sync.track_time(0.0)))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Sync-Diagnose", f"Speichern fehlgeschlagen: {exc}")
            return None
        c.message.emit(f"Sync-Diagnose gespeichert: {Path(path).name}")
        if path is not None and not self.isHidden():
            QMessageBox.information(self, "Sync-Diagnose", report + f"\n\nGespeichert: {path}")
        return Path(path)

    def _fill_presets(self) -> None:
        self.preset_menu.clear()
        for name in list_presets():
            act = QAction(name, self)
            act.triggered.connect(lambda _c=False, n=name: self._apply_preset(n))
            self.preset_menu.addAction(act)

    def _apply_preset(self, name: str) -> None:
        try:
            self.c.set_layout(load_preset(name))
        except ValueError as exc:
            QMessageBox.warning(self, "Preset laden", str(exc))

    # ------------------------------------------------------------------ Projekt

    def _confirm_discard(self) -> bool:
        if not self.c.dirty:
            return True
        ans = QMessageBox.question(
            self, "Ungespeicherte Änderungen", "Änderungen am Projekt speichern?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        )
        if ans == QMessageBox.StandardButton.Save:
            return self.save_project()
        return ans == QMessageBox.StandardButton.Discard

    def new_project(self) -> None:
        if self._confirm_discard():
            self.c.new_project()
            self.c.model.project.units = self.settings.units

    def open_project_dialog(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Projekt öffnen", "", PROJECT_FILTER)
        if path:
            self.c.open_project(path)

    def save_project(self) -> bool:
        if self.c.project_path is None:
            return self.save_project_as()
        return self._save_to(None)

    def _save_to(self, path: str | None) -> bool:
        try:
            self.c.save_project(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Projekt speichern", f"Das Projekt konnte nicht gespeichert werden:\n{exc}")
            return False
        self._update_title()
        return True

    def save_project_as(self) -> bool:
        p = self.c.model.project
        start = str(Path(p.video_path or p.gpx_path or Path.home()).with_suffix(PROJECT_SUFFIX))
        path, _ = QFileDialog.getSaveFileName(self, "Projekt speichern", start, PROJECT_FILTER)
        if not path:
            return False
        return self._save_to(path)

    # ------------------------------------------------------------------ Layout-Dateien

    def load_layout_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Layout laden", "", "Layout (*.json)")
        if path:
            try:
                self.c.set_layout(Layout.load(path))
            except ValueError as exc:
                QMessageBox.warning(self, "Layout laden", str(exc))

    def export_layout_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Layout speichern", "layout.json", "Layout (*.json)")
        if path:
            try:
                self.c.model.project.layout.save(path)
            except OSError as exc:
                QMessageBox.warning(self, "Layout speichern", str(exc))

    def save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Als Preset speichern", "Name des Presets:",
                                        text=self.c.model.project.layout.name)
        if ok and name.strip():
            layout = Layout.from_dict(self.c.model.project.layout.to_dict())
            layout.name = name.strip()
            try:
                path = save_user_preset(layout, name.strip())
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Preset speichern", str(exc))
                return
            self.statusBar().showMessage(f"Preset gespeichert: {path}", 8000)

    # ------------------------------------------------------------------ Sonstiges

    def _set_show_video(self, on: bool) -> None:
        self.canvas.show_video = on
        self.canvas.update()

    def _set_safe(self, on: bool) -> None:
        self.canvas.show_safe_area = on
        self.canvas.update()

    def _on_busy(self, busy: bool, label: str) -> None:
        if busy:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            self.statusBar().showMessage(f"{label} …")
        else:
            QApplication.restoreOverrideCursor()

    def _update_title(self) -> None:
        name = self.c.project_path.stem if self.c.project_path else (
            Path(self.c.model.project.gpx_path).stem if self.c.model.project.gpx_path else "Neues Projekt")
        self.setWindowTitle(f"{name}{' •' if self.c.dirty else ''} – {APP_NAME} {__version__}")

    def _sync_language_menu(self) -> None:
        current = self.c.model.project.layout.language
        for code, act in self.language_actions.items():
            act.setChecked(code == current)

    def check_ffmpeg(self, silent_if_ok: bool = False) -> bool:
        st = locate(self.settings.ffmpeg_path, self.settings.ffprobe_path)
        if st.ok:
            if not silent_if_ok:
                QMessageBox.information(self, "ffmpeg", f"ffmpeg {st.version} gefunden:\n{st.ffmpeg}\n\nProRes-Export verfügbar.")
            return True
        msg = "\n".join(st.errors) + f"\n\n{install_hint()}\n\nffmpeg jetzt manuell auswählen?"
        ans = QMessageBox.warning(self, "ffmpeg fehlt", msg,
                                  QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ans == QMessageBox.StandardButton.Yes:
            path, _ = QFileDialog.getOpenFileName(self, "ffmpeg auswählen")
            if path:
                self.settings.ffmpeg_path = path
                self.settings.ffprobe_path = str(Path(path).with_name(Path(path).name.replace("ffmpeg", "ffprobe")))
                self.settings.save()
                configure_ffmpeg(self.settings.ffmpeg_path, self.settings.ffprobe_path)
                return self.check_ffmpeg()
        return False

    def about(self) -> None:
        QMessageBox.about(self, f"Über {APP_NAME}",
                          f"<b>{APP_NAME} {__version__}</b><br>GPX-Telemetrie als transparentes Video-Overlay.<br><br>"
                          "Schrift: Inter (SIL Open Font License)<br>Video: FFmpeg")

    def open_paths(self, paths: list[str]) -> None:
        """Dateien per Drag-and-Drop oder Kommandozeile öffnen."""
        gpx = [p for p in paths if Path(p).suffix.lower() in (".gpx", ".fit")]
        vids = [p for p in paths if Path(p).suffix.lower() in VIDEO_SUFFIXES]
        projs = [p for p in paths if Path(p).suffix.lower() == PROJECT_SUFFIX]
        if projs:
            self.c.open_project(projs[0])
            return
        if gpx:
            self.c.open_gpx(gpx[0])
            if vids:
                self.c.wait_idle()
        if vids and (self.c.model.ready or gpx):
            self.c.open_video(vids[0])
        elif vids:
            QMessageBox.information(self, "Video", "Bitte zuerst eine GPX-Datei öffnen.")

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        self.open_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def closeEvent(self, e) -> None:
        if self.export.running:
            ans = QMessageBox.question(self, "Export läuft", "Ein Export läuft noch. Abbrechen und beenden?")
            if ans != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
            self.export.cancel()
            self.export._finish()
        if not self._confirm_discard():
            e.ignore()
            return
        self.settings.units = self.c.model.project.units
        try:
            self.settings.save()
        except OSError:
            pass
        self.c.shutdown()
        e.accept()
