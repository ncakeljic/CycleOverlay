"""Rechtes Panel, Tab „Export“."""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.project import EXPORT_CONTENTS, FPS_PRESETS, SIZE_PRESETS
from ..core.units import format_duration
from ..export.profiles import available_profiles, format_bytes
from ..export.video_encoders import QUALITY_TITLES
from ..export.worker import ExportWorker, start_export
from ..platform_utils import open_in_file_manager
from .controller import Controller
from .form_kit import CollapsibleSection, SegmentedControl, choice_control, hint_label


class ExportPanel(QWidget):
    def __init__(self, controller: Controller, parent=None):
        super().__init__(parent)
        self.c = controller
        self._updating = False
        self._worker: ExportWorker | None = None
        self._thread = None
        self.last_result = None
        self._queue: list = []  # Stapel-Export: noch offene Aufträge
        self._batch: dict | None = None

        self.size_combo = QComboBox()
        self.size_combo.currentIndexChanged.connect(self._on_settings)
        self.w = QSpinBox()
        self.h = QSpinBox()
        for box in (self.w, self.h):
            box.setRange(16, 8192)
            box.setSingleStep(2)
            box.setKeyboardTracking(False)
            box.valueChanged.connect(self._on_settings)
        size_row = QHBoxLayout()
        size_row.addWidget(self.w)
        size_row.addWidget(QLabel("×"))
        size_row.addWidget(self.h)

        self.fps = QComboBox()
        self.fps.currentIndexChanged.connect(self._on_settings)
        # Was wird exportiert: nur die Anzeigen (transparent) oder das Video mit eingebrannten Anzeigen
        self.content = SegmentedControl(list(EXPORT_CONTENTS.items()))
        self.content.changed.connect(self._on_settings)
        self.content_hint = hint_label("", "#8a8a8a")
        # Format: bis 4 Möglichkeiten als Knopfreihe, sonst Auswahlliste mit Hinweis darunter
        self.profile = choice_control([(key, prof.title) for key, prof in available_profiles().items()])
        self.profile.changed.connect(self._on_settings)
        self.video_profile = choice_control([(key, prof.title)
                                             for key, prof in available_profiles(with_video=True).items()])
        self.video_profile.changed.connect(self._on_settings)
        self.quality = SegmentedControl(list(QUALITY_TITLES.items()))
        self.quality.changed.connect(self._on_settings)
        self.profile_note = hint_label("", "#8a8a8a")

        self.folder = QLineEdit()
        self.folder.setReadOnly(True)
        self.folder_btn = QPushButton("…")
        self.folder_btn.setMaximumWidth(36)
        self.folder_btn.clicked.connect(self._choose_folder)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(self.folder_btn)
        self.filename = QLineEdit()
        self.filename.setPlaceholderText("automatisch")
        self.filename.editingFinished.connect(self._on_settings)

        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.export_btn = QPushButton("Exportieren")
        self.export_btn.setMinimumHeight(34)
        self.export_btn.clicked.connect(self.start)
        self.batch_btn = QPushButton("Mehrere Clips exportieren …")
        self.batch_btn.setToolTip("Mehrere Videos derselben Fahrt (z. B. GoPro-Kapitel) nacheinander exportieren.\n"
                                  "Kamera, Zeitzone, Offset und Layout gelten für alle Clips.")
        self.batch_btn.clicked.connect(lambda: self.start_batch())
        self.cancel_btn = QPushButton("Abbrechen")
        self.cancel_btn.clicked.connect(self.cancel)
        self.cancel_btn.setVisible(False)
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.show_btn = QPushButton("Im Finder/Explorer zeigen")
        self.show_btn.setVisible(False)
        self.show_btn.clicked.connect(lambda: self.last_result and open_in_file_manager(self.last_result.path))

        fmt = CollapsibleSection("Format")
        fmt.form.addRow("Inhalt", self.content)
        fmt.form.addRow(self.content_hint)
        fmt.form.addRow(self.profile)
        fmt.form.addRow(self.video_profile)
        fmt.form.addRow("Qualität", self.quality)
        fmt.form.addRow(self.profile_note)
        self.fmt_form = fmt.form
        img = CollapsibleSection("Bild")
        img.form.addRow("Auflösung", self.size_combo)
        img.form.addRow("", size_row)
        img.form.addRow("Framerate", self.fps)
        dest = CollapsibleSection("Ziel")
        dest.form.addRow("Ordner", folder_row)
        dest.form.addRow("Dateiname", self.filename)
        self.sections = {"format": fmt, "image": img, "target": dest}
        lay = QVBoxLayout(self)
        for sec in (fmt, img, dest):
            lay.addWidget(sec)
        lay.addSpacing(6)
        lay.addWidget(self.info)
        lay.addWidget(self.export_btn)
        lay.addWidget(self.batch_btn)
        lay.addWidget(self.bar)
        lay.addWidget(self.status)
        lay.addWidget(self.cancel_btn)
        lay.addWidget(self.show_btn)
        lay.addStretch(1)

        controller.projectChanged.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------------ Einstellungen

    def refresh(self) -> None:
        m = self.c.model
        e = m.project.export
        self._updating = True
        self.size_combo.clear()
        if m.has_video:
            self.size_combo.addItem(f"Wie Video ({m.video.width}x{m.video.height})", "video")
        for title, w, h in SIZE_PRESETS:
            self.size_combo.addItem(title, (w, h))
        self.size_combo.addItem("Benutzerdefiniert", "custom")
        if m.has_video and e.size_from_video:
            self.size_combo.setCurrentIndex(0)
        else:
            idx = self.size_combo.findData((e.width, e.height))
            self.size_combo.setCurrentIndex(idx if idx >= 0 else self.size_combo.count() - 1)
        w, h = m.frame_size()
        self.w.setValue(w)
        self.h.setValue(h)
        custom = self.size_combo.currentData() == "custom"
        self.w.setEnabled(custom)
        self.h.setEnabled(custom)

        self.fps.clear()
        if m.has_video:
            self.fps.addItem(f"Wie Video ({float(m.video.fps):.3f})", "video")
        for f in FPS_PRESETS:
            self.fps.addItem(f, f)
        if m.has_video and e.fps_from_video:
            self.fps.setCurrentIndex(0)
        else:
            idx = self.fps.findData(e.fps)
            self.fps.setCurrentIndex(idx if idx >= 0 else self.fps.findData("30"))
        self.profile.set_value(e.profile)
        self.video_profile.set_value(e.video_profile)
        self.quality.set_value(e.quality)
        self.content.set_value("video" if m.export_with_video else "overlay")
        self.content.buttons[1].setEnabled(m.has_video)
        self._update_note()
        self.filename.setText(e.filename or "")
        self._updating = False
        self._update_info()

    def _update_note(self) -> None:
        m = self.c.model
        video = m.export_with_video
        prof = m.active_profile()
        self.profile_note.setText(prof.note)
        form = self.fmt_form
        form.setRowVisible(self.profile, not video)
        form.setRowVisible(self.video_profile, video)
        form.setRowVisible(self.quality, video and prof.codec_family is not None)
        if not m.has_video:
            self.content_hint.setText("Für „Video mit Anzeigen“ zuerst ein Video laden.")
        elif video:
            self.content_hint.setText("Die Anzeigen werden ins Video eingebrannt, der Ton wird übernommen – "
                                      "ohne Transparenz, fertig zum Teilen.")
        else:
            self.content_hint.setText("Transparente Ebene zum Darüberlegen im Schnittprogramm.")

    def _update_info(self) -> None:
        m = self.c.model
        if not m.ready:
            self.info.setText("Zuerst eine GPX-Datei öffnen.")
            self.export_btn.setEnabled(False)
            self.batch_btn.setEnabled(False)
            self.folder.setText("")
            return
        target = m.default_output()
        self.folder.setText(str(target.parent))
        n = m.n_frames()
        w, h = m.frame_size()
        est = m.estimated_size()
        txt = (f"<b>{target.name}</b><br>{w}x{h} · {float(m.fps()):.3f} FPS · {n} Frames "
               f"({format_duration(n / float(m.fps()))})<br>Größe ca. bis {format_bytes(est)}")
        try:
            free = shutil.disk_usage(target.parent).free
            if free < est:
                txt += f"<br><span style='color:#d03030'>Nur {format_bytes(free)} frei!</span>"
        except OSError:
            pass
        if est > 20e9:
            txt += "<br><span style='color:#d08a00'>Sehr große Datei – evtl. kürzeren Bereich wählen.</span>"
        self.info.setText(txt)
        self.export_btn.setEnabled(n > 0 and self._worker is None)
        self.batch_btn.setEnabled(self._worker is None)

    def _on_settings(self, *_a) -> None:
        if self._updating:
            return
        m = self.c.model
        e = m.project.export
        data = self.size_combo.currentData()
        e.size_from_video = data == "video"
        if isinstance(data, tuple):
            e.width, e.height = data
        elif data == "custom":
            e.width, e.height = self.w.value(), self.h.value()
        f = self.fps.currentData()
        e.fps_from_video = f == "video"
        if f and f != "video":
            e.fps = f
        e.profile = self.profile.value() or e.profile
        e.video_profile = self.video_profile.value() or e.video_profile
        e.quality = self.quality.value() or e.quality
        content = self.content.value() or e.content
        if content == "video" and not m.has_video:
            content = "overlay"
        e.content = content
        self._update_note()
        e.filename = self.filename.text().strip() or None
        self.c.project_edited()

    def _choose_folder(self) -> None:
        start = self.folder.text() or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Export-Ordner wählen", start)
        if path:
            self.c.model.project.export.out_dir = path
            self.c.project_edited()

    # ------------------------------------------------------------------ Export

    def start(self) -> None:
        m = self.c.model
        if not m.ready or self._worker is not None:
            return
        target = m.default_output()
        profile = m.active_profile()
        if target.exists() and not profile.is_sequence:
            ans = QMessageBox.question(self, "Datei existiert", f"{target.name} existiert bereits. Überschreiben?")
            if ans != QMessageBox.StandardButton.Yes:
                return
        if m.has_video:
            chk = m.sync_check()
            if chk is not None and chk.status != "ok":
                ans = QMessageBox.question(self, "Synchronisation prüfen",
                                           f"{chk.message}\n\nTrotzdem exportieren?")
                if ans != QMessageBox.StandardButton.Yes:
                    return
        w, h = m.frame_size()
        if profile.even_size and (w % 2 or h % 2):
            self.c.error.emit("Export", f"{profile.title} braucht eine gerade Breite und Höhe (eingestellt: {w}×{h}).")
            return
        bg = m.project.video_path if profile.with_video else None
        self._launch(m.export_track_times(), m.fps(), w, h, profile, target, background=bg)

    def _launch(self, track_times, fps, w, h, profile, target, label: str = "", background=None) -> None:
        m = self.c.model
        self.c.stop()
        self._worker = ExportWorker(m.project.layout, m.telemetry, track_times, fps, w, h,
                                    profile, target, m.project.units, m.project.timezone, map_data=m.map_data,
                                    count_numbers=m.project.count_numbers, background=background,
                                    quality=m.project.export.quality)
        self._worker.progress.connect(self._on_progress)
        self._worker.succeeded.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self._label = label
        self.bar.setRange(0, len(track_times))
        self.bar.setValue(0)
        self.bar.setVisible(True)
        self.cancel_btn.setVisible(True)
        self.show_btn.setVisible(False)
        self.export_btn.setEnabled(False)
        self.batch_btn.setEnabled(False)
        self.status.setText(f"{label}Export läuft …")
        self._thread = start_export(self._worker, self)

    # ------------------------------------------------------------------ Stapel-Export

    def start_batch(self, paths: list[str] | None = None) -> None:
        m = self.c.model
        if not m.ready or self._worker is not None:
            return
        if paths is None:
            from .signal_panel import VIDEO_FILTER

            start = str(Path(m.project.video_path).parent) if m.project.video_path else ""
            paths, _ = QFileDialog.getOpenFileNames(self, "Clips derselben Fahrt wählen", start, VIDEO_FILTER)
        if not paths:
            return
        jobs = m.batch_jobs(paths)
        good = [j for j in jobs if j.ok]
        bad = [j for j in jobs if not j.ok]
        if bad:
            lines = [f"• {j.video_path.name}: {j.error or j.check.message}" for j in bad]
            if not good:
                self.c.error.emit("Stapel-Export", "Keiner der Clips passt zur Fahrt:\n" + "\n".join(lines))
                return
            ans = QMessageBox.question(self, "Stapel-Export",
                                       "Diese Clips werden übersprungen:\n" + "\n".join(lines)
                                       + f"\n\nDie übrigen {len(good)} Clips exportieren?")
            if ans != QMessageBox.StandardButton.Yes:
                return
        existing = [j.target.name for j in good if j.target.exists()]
        if existing:
            ans = QMessageBox.question(self, "Dateien existieren",
                                       f"{len(existing)} Overlay-Datei(en) existieren bereits und werden überschrieben:\n"
                                       + "\n".join(existing[:8]) + "\n\nFortfahren?")
            if ans != QMessageBox.StandardButton.Yes:
                return
        self._queue = list(good)
        self._batch = {"total": len(good), "done": [], "failed": []}
        self._next_batch()

    def _next_batch(self) -> None:
        b = self._batch
        if b is None:
            return
        if not self._queue:
            self._batch = None
            msg = f"<b>Stapel-Export fertig:</b> {len(b['done'])} von {b['total']} Clips"
            if b["failed"]:
                msg += "<br><span style='color:#d03030'>Fehler: " + "; ".join(b["failed"]) + "</span>"
            self.status.setText(msg)
            self.show_btn.setVisible(bool(b["done"]))
            self.c.message.emit(f"Stapel-Export fertig: {len(b['done'])}/{b['total']}")
            return
        job = self._queue.pop(0)
        k = b["total"] - len(self._queue)
        profile = self.c.model.active_profile()
        self._launch(job.track_times, job.fps, job.size[0], job.size[1], profile, job.target,
                     background=job.video_path if profile.with_video else None,
                     label=f"Clip {k}/{b['total']} ({job.video_path.name}): ")

    @property
    def batch_running(self) -> bool:
        return self._batch is not None

    def cancel(self) -> None:
        self._queue = []
        if self._worker is not None:
            self._worker.cancel()
            self.status.setText("Wird abgebrochen …")

    @property
    def running(self) -> bool:
        return self._worker is not None or self._batch is not None

    def _on_progress(self, frame: int, total: int, fps: float, eta: float) -> None:
        self.bar.setValue(frame)
        self.status.setText(f"{getattr(self, '_label', '')}Frame {frame}/{total} · {fps:.1f} FPS · "
                            f"noch {format_duration(eta)}")

    def _finish(self) -> None:
        if self._thread is not None:
            self._thread.quit()  # Ereignisschleife des Threads beenden (Worker ist fertig)
            self._thread.wait(10000)
        self._worker = None
        self._thread = None
        self.cancel_btn.setVisible(False)
        self.bar.setVisible(False)
        self._update_info()

    def _on_done(self, res) -> None:
        self.last_result = res
        self._finish()
        if self._batch is not None:
            self._batch["done"].append(res.path.name)
            self._next_batch()
            return
        if getattr(res, "encoder", None) and res.encoder not in ("prores_ks",):
            from ..export.video_encoders import encoder_title

            detail = f"Video mit Anzeigen · {encoder_title(res.encoder)}"
        elif self.c.model.active_profile().with_video:
            detail = f"Video mit Anzeigen · {res.codec}"
        else:
            detail = f"{res.codec}, {res.pix_fmt} (mit Alpha)"
        self.status.setText(f"<b>Fertig</b> in {format_duration(res.seconds)}: {res.path.name}<br>"
                            f"{detail}, {format_bytes(res.size_bytes)}")
        self.show_btn.setVisible(True)
        self.c.message.emit(f"Export fertig: {res.path}")

    def _on_failed(self, msg: str) -> None:
        self._finish()
        if self._batch is not None:
            self._batch["failed"].append(msg[:120])
            self._next_batch()
            return
        self.status.setText(f"<span style='color:#d03030'>Fehler: {msg}</span>")
        self.c.error.emit("Export fehlgeschlagen", msg)

    def _on_cancelled(self) -> None:
        self._queue = []
        self._batch = None
        self._finish()
        self.status.setText("Export abgebrochen – unvollständige Dateien wurden entfernt.")
