"""Update-Prüfung in der Oberfläche: beim Start (täglich) und über Hilfe → „Nach Updates suchen …“."""

from __future__ import annotations

import logging
import tempfile
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from .. import __version__
from ..core import updates
from ..core.updates import UpdateCancelled, UpdateInfo

log = logging.getLogger(__name__)


class _Download(QObject):
    """Download im Daemon-Thread. Wie ``controller._Task``: interne Signale aus dem Thread gehen an
    Methoden dieses (im GUI-Thread lebenden) Objekts, erst dort werden die öffentlichen ausgelöst."""

    progress = Signal(int, int)
    done = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    _bg_progress = Signal(int, int)
    _bg_done = Signal(object)
    _bg_failed = Signal(str)
    _bg_cancelled = Signal()

    def __init__(self, info: UpdateInfo, dest: Path, parent=None):
        super().__init__(parent)
        self.info, self.dest = info, dest
        self.cancel_event = threading.Event()
        self._bg_progress.connect(self._relay_progress)
        self._bg_done.connect(self._relay_done)
        self._bg_failed.connect(self._relay_failed)
        self._bg_cancelled.connect(self._relay_cancelled)

    def _relay_progress(self, d: int, t: int) -> None:
        self.progress.emit(d, t)

    def _relay_done(self, path) -> None:
        self.done.emit(path)

    def _relay_failed(self, msg: str) -> None:
        self.failed.emit(msg)

    def _relay_cancelled(self) -> None:
        self.cancelled.emit()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="cycleoverlay-update").start()

    def _run(self) -> None:
        try:
            path = updates.download(self.info, self.dest, cancel=self.cancel_event,
                                    progress=lambda d, t: self._bg_progress.emit(int(d), int(t)))
        except UpdateCancelled:
            self._bg_cancelled.emit()
        except Exception as exc:  # noqa: BLE001
            log.exception("Update-Download fehlgeschlagen")
            self._bg_failed.emit(str(exc))
        else:
            self._bg_done.emit(path)


class _Check(QObject):
    done = Signal(object)  # UpdateInfo oder None
    failed = Signal(str)
    _bg_done = Signal(object)
    _bg_failed = Signal(str)

    def __init__(self, fetch=None, parent=None):
        super().__init__(parent)
        self._fetch = fetch or updates.fetch_latest
        self._bg_done.connect(self._relay_done)
        self._bg_failed.connect(self._relay_failed)

    def _relay_done(self, info) -> None:
        self.done.emit(info)

    def _relay_failed(self, msg: str) -> None:
        self.failed.emit(msg)

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="cycleoverlay-update-check").start()

    def _run(self) -> None:
        try:
            self._bg_done.emit(updates.check_for_update(__version__, fetch=self._fetch))
        except Exception as exc:  # noqa: BLE001
            self._bg_failed.emit(str(exc))


class UpdateDialog(QDialog):
    """Hinweis auf eine neue Version mit Änderungen und Knöpfen."""

    INSTALL, PAGE, SKIP, LATER = range(4)

    def __init__(self, info: UpdateInfo, auto_check: bool, can_install: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Update verfügbar")
        self.choice = self.LATER
        lay = QVBoxLayout(self)
        head = QLabel(f"<b>CycleOverlay {info.version}</b> ist verfügbar – installiert ist {__version__}."
                      + (f"<br><span style='color:#8a8a8a'>veröffentlicht am {info.published}</span>"
                         if info.published else ""))
        head.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(head)
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(info.notes or "_Keine Beschreibung._")
        notes.setMinimumSize(480, 220)
        lay.addWidget(notes, 1)
        if not can_install:
            hint = QLabel("Diese Installation kann sich nicht selbst aktualisieren (Start aus dem Quellcode: "
                          "neuen Stand mit <code>git pull</code> holen)." if info.downloadable else
                          "Für dieses Betriebssystem liegt dem Release kein Installer bei.")
            hint.setWordWrap(True)
            lay.addWidget(hint)
        self.auto = QCheckBox("Beim Start automatisch nach Updates suchen")
        self.auto.setChecked(auto_check)
        lay.addWidget(self.auto)
        row = QHBoxLayout()
        self.install_btn = QPushButton("Herunterladen und installieren")
        self.install_btn.setVisible(can_install and info.downloadable)
        self.page_btn = QPushButton("Release-Seite öffnen")
        self.skip_btn = QPushButton("Diese Version überspringen")
        self.later_btn = QPushButton("Später")
        for b, c in ((self.install_btn, self.INSTALL), (self.page_btn, self.PAGE), (self.skip_btn, self.SKIP),
                     (self.later_btn, self.LATER)):
            b.clicked.connect(lambda _=False, c=c: self._pick(c))
            row.addWidget(b)
        row.insertStretch(2)
        lay.addLayout(row)
        (self.install_btn if self.install_btn.isVisibleTo(self) else self.page_btn).setDefault(True)

    def _pick(self, choice: int) -> None:
        self.choice = choice
        self.accept()


class UpdateManager(QObject):
    """Verbindet die Update-Prüfung mit dem Hauptfenster."""

    def __init__(self, window, fetch=None):
        super().__init__(window)
        self.window = window
        self._fetch = fetch
        self._check: _Check | None = None
        self._download: _Download | None = None
        self._progress: QProgressDialog | None = None
        #: zuletzt gezeigter Hinweis (für Tests)
        self.last_info: UpdateInfo | None = None

    @property
    def settings(self):
        return self.window.settings

    def auto_check(self) -> None:
        """Beim Start: nur wenn eingeschaltet und fällig (einmal am Tag), ohne Meldung bei Fehlern."""
        s = self.settings
        if not s.check_updates or not updates.due(s.last_update_check):
            return
        s.last_update_check = time.time()
        self._save_settings()
        self.check(manual=False)

    def check(self, manual: bool = True) -> None:
        if self._check is not None:
            return
        self._check = _Check(self._fetch, self)
        self._check.done.connect(lambda info: self._on_checked(info, manual))
        self._check.failed.connect(lambda msg: self._on_check_failed(msg, manual))
        if manual:
            QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
        self._check.start()

    def _end_check(self, manual: bool) -> None:
        self._check = None
        if manual:
            QApplication.restoreOverrideCursor()

    def _on_check_failed(self, msg: str, manual: bool) -> None:
        self._end_check(manual)
        log.info("Update-Prüfung fehlgeschlagen: %s", msg)
        if manual:
            QMessageBox.warning(self.window, "Nach Updates suchen", f"Update-Prüfung nicht möglich:\n{msg}")

    def _on_checked(self, info: UpdateInfo | None, manual: bool) -> None:
        self._end_check(manual)
        self.last_info = info
        if info is None:
            if manual:
                QMessageBox.information(self.window, "Nach Updates suchen",
                                        f"CycleOverlay {__version__} ist aktuell.")
            return
        if not manual and self.settings.skipped_version == info.version:
            return
        self.show_dialog(info)

    def show_dialog(self, info: UpdateInfo) -> int:
        dlg = UpdateDialog(info, self.settings.check_updates, updates.can_self_update(), self.window)
        dlg.exec()
        self.settings.check_updates = dlg.auto.isChecked()
        if dlg.choice == UpdateDialog.SKIP:
            self.settings.skipped_version = info.version
        self._save_settings()
        if dlg.choice == UpdateDialog.PAGE:
            QDesktopServices.openUrl(QUrl(info.page_url))
        elif dlg.choice == UpdateDialog.INSTALL:
            self.start_download(info)
        return dlg.choice

    def _save_settings(self) -> None:
        try:
            self.settings.save()
        except OSError as exc:
            log.warning("Einstellungen nicht gespeichert: %s", exc)

    # ------------------------------------------------------------------ Download & Installation

    def start_download(self, info: UpdateInfo, dest: Path | None = None) -> None:
        if self._download is not None:
            return
        dest = dest or Path(tempfile.gettempdir()) / "CycleOverlay-Update"
        self._download = _Download(info, dest, self)
        self._progress = QProgressDialog(f"CycleOverlay {info.version} wird geladen …", "Abbrechen", 0, 0, self.window)
        self._progress.setWindowTitle("Update")
        self._progress.setMinimumDuration(0)
        self._progress.canceled.connect(self._download.cancel_event.set)
        self._download.progress.connect(self._on_progress)
        self._download.done.connect(self._on_downloaded)
        self._download.failed.connect(self._on_download_failed)
        self._download.cancelled.connect(self._end_download)
        self._download.start()

    def _on_progress(self, done: int, total: int) -> None:
        if self._progress is None:
            return
        if total > 0:
            self._progress.setMaximum(1000)
            self._progress.setValue(int(done / total * 1000))
        self._progress.setLabelText(f"Update wird geladen … {done / 1e6:.1f}"
                                    + (f" von {total / 1e6:.1f} MB" if total else " MB"))

    def _end_download(self) -> None:
        self._download = None
        if self._progress is not None:
            self._progress.reset()
            self._progress.deleteLater()
            self._progress = None

    def _on_download_failed(self, msg: str) -> None:
        self._end_download()
        QMessageBox.warning(self.window, "Update", f"Das Update konnte nicht geladen werden:\n{msg}")

    def _on_downloaded(self, path: Path) -> None:
        self._end_download()
        self.install(path)

    def install(self, path: Path) -> bool:
        """Installer starten und die App beenden (vorher ggf. speichern)."""
        w = self.window
        if w.export.running:
            QMessageBox.information(w, "Update", f"Der Installer liegt bereit:\n{path}\n\n"
                                    "Ein Export läuft noch – bitte danach CycleOverlay beenden und den Installer starten.")
            return False
        if not w._confirm_discard():
            return False
        try:
            updates.launch_installer(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(w, "Update", f"Installer konnte nicht gestartet werden:\n{exc}\n\nDatei: {path}")
            return False
        w.c.set_dirty(False)  # bereits gefragt
        w.close()
        return True
