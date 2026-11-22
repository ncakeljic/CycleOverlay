"""Start der grafischen Oberfläche."""

from __future__ import annotations

import os
import logging
import sys
import traceback

from .. import APP_ID, APP_NAME, __version__

_IN_HOOK = False


def install_excepthook(log_path, parent=None) -> None:
    """Unerwartete Fehler nicht still verschlucken: protokollieren und als Meldung zeigen."""
    from PySide6.QtWidgets import QMessageBox

    def hook(exc_type, exc, tb) -> None:
        global _IN_HOOK
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        logging.getLogger("cycle_overlay").error("Unerwarteter Fehler", exc_info=(exc_type, exc, tb))
        if _IN_HOOK:
            return
        _IN_HOOK = True
        try:
            where = traceback.extract_tb(tb)[-1] if tb else None
            loc = f"\n({os.path.basename(where.filename)}:{where.lineno})" if where else ""
            QMessageBox.critical(parent, "Unerwarteter Fehler",
                                 f"{exc_type.__name__}: {exc}{loc}\n\nDie App läuft weiter. "
                                 f"Details stehen im Protokoll:\n{log_path}")
        except Exception:  # noqa: BLE001
            pass
        finally:
            _IN_HOOK = False

    sys.excepthook = hook


def run_gui(paths: list[str] | None = None) -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("Fehler: PySide6 ist nicht installiert. Bitte im Projektordner 'uv sync' ausführen.", file=sys.stderr)
        return 1
    from ..logging_setup import setup_logging
    from ..rendering.qt_support import ensure_app, load_bundled_fonts
    from .main_window import MainWindow

    from ..app_paths import cleanup_stale_sessions

    log_path = setup_logging()
    logging.getLogger(__name__).info("%s %s startet", APP_NAME, __version__)
    cleanup_stale_sessions()
    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationDomain(APP_ID)
    QApplication.setApplicationVersion(__version__)
    app = ensure_app()
    load_bundled_fonts()
    from PySide6.QtGui import QIcon

    from ..app_paths import resource_path

    icon = resource_path("icons", "cycleoverlay.png")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    win = MainWindow()
    install_excepthook(log_path, win)
    win.show()
    win.check_ffmpeg(silent_if_ok=True)
    if paths:
        win.open_paths(paths)
    from PySide6.QtCore import QTimer

    QTimer.singleShot(4000, win.updates.auto_check)  # einmal täglich, still bei Fehlern
    return app.exec()
