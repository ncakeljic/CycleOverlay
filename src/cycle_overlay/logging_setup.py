"""Logging in eine Datei im Benutzer-Log-Ordner (PRD 5.1)."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .app_paths import user_log_dir

LOG_FILE_NAME = "cycleoverlay.log"


def setup_logging(level: int = logging.INFO) -> Path:
    log_path = user_log_dir() / LOG_FILE_NAME
    root = logging.getLogger()
    if any(getattr(h, "_cycle_overlay", False) for h in root.handlers):
        return log_path
    root.setLevel(level)

    file_handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    file_handler._cycle_overlay = True  # type: ignore[attr-defined]
    root.addHandler(file_handler)
    return log_path
