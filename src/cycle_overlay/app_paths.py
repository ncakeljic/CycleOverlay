"""Zentrale Pfadverwaltung.

Regeln (PRD 5.2):
- Ressourcen (Schriften, Presets, Icons) werden nur über ``resource_path()`` geladen.
  Das funktioniert im Quellcode und in einer späteren PyInstaller-Version.
- Es wird nie in den Programmordner geschrieben. Einstellungen, Benutzer-Presets
  und Logs liegen in den plattformüblichen Benutzerordnern.
- Für Tests kann mit der Umgebungsvariable ``CYCLE_OVERLAY_HOME`` ein eigener
  Basisordner für alle Benutzerdaten gesetzt werden.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
import uuid
from pathlib import Path

from platformdirs import PlatformDirs

from . import APP_NAME

_ENV_HOME = "CYCLE_OVERLAY_HOME"


def is_frozen() -> bool:
    """True, wenn die App als gebündelte Anwendung (PyInstaller) läuft."""
    return bool(getattr(sys, "frozen", False))


def package_dir() -> Path:
    """Ordner des Pakets ``cycle_overlay`` (im Bundle: innerhalb von _MEIPASS)."""
    if is_frozen() and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "cycle_overlay"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


def resource_path(*parts: str) -> Path:
    """Pfad zu einer mitgelieferten Ressource, z. B. ``resource_path('fonts', 'x.ttf')``."""
    return package_dir().joinpath("resources", *parts)


def bundled_bin_dir() -> Path:
    """Ordner für mitgelieferte Programme (z. B. ffmpeg) im späteren App-Bundle."""
    if is_frozen() and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "bin"  # type: ignore[attr-defined]
    return resource_path("bin")


def _dirs() -> PlatformDirs:
    return PlatformDirs(APP_NAME, appauthor=False, roaming=True)


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _override(sub: str) -> Path | None:
    base = os.environ.get(_ENV_HOME)
    return Path(base) / sub if base else None


def user_config_dir() -> Path:
    return _ensure(_override("config") or Path(_dirs().user_config_dir))


def user_data_dir() -> Path:
    return _ensure(_override("data") or Path(_dirs().user_data_dir))


def user_log_dir() -> Path:
    return _ensure(_override("logs") or Path(_dirs().user_log_dir))


def user_presets_dir() -> Path:
    return _ensure(user_data_dir() / "presets")


def analysis_cache_dir(keep: int = 40) -> Path:
    """Zwischenspeicher für Videoanalysen (Bewegungskurve, Filmstreifen); nur die neuesten bleiben."""
    d = _ensure(_override("cache") or Path(_dirs().user_cache_dir) / "analysis")
    try:
        files = sorted(d.glob("*.npz"), key=lambda f: f.stat().st_mtime, reverse=True)
        for old in files[keep:]:
            old.unlink(missing_ok=True)
    except OSError:
        pass
    return d


# --------------------------------------------------------------------------- Sitzungsdaten
#
# Heruntergeladene Daten (z. B. OpenStreetMap) liegen nur in einem Ordner dieser Sitzung.
# Er wird gelöscht, wenn das Projekt geschlossen wird; gespeicherte Projekte tragen ihre
# Daten im Projektpaket. Reste abgestürzter Sitzungen werden beim Start entfernt.

_SESSION_DIR: Path | None = None


def _sessions_root() -> Path:
    return user_data_dir() / "session"


def session_cache_dir() -> Path:
    global _SESSION_DIR
    if _SESSION_DIR is None or not _SESSION_DIR.exists():
        _SESSION_DIR = _ensure(_sessions_root() / f"{os.getpid()}-{uuid.uuid4().hex[:8]}")
    return _SESSION_DIR


def clear_session_cache() -> None:
    """Alle in dieser Sitzung heruntergeladenen Daten löschen."""
    global _SESSION_DIR
    if _SESSION_DIR is not None:
        shutil.rmtree(_SESSION_DIR, ignore_errors=True)
    _SESSION_DIR = None


def cleanup_stale_sessions(max_age_hours: float = 12.0) -> int:
    """Reste alter Sitzungen und den früheren dauerhaften Kartenspeicher entfernen."""
    removed = 0
    legacy = user_data_dir() / "mapcache"
    if legacy.exists():
        shutil.rmtree(legacy, ignore_errors=True)
        removed += 1
    root = _sessions_root()
    if root.exists():
        limit = time.time() - max_age_hours * 3600
        for d in root.iterdir():
            if d == _SESSION_DIR:
                continue
            try:
                if d.stat().st_mtime < limit:
                    shutil.rmtree(d, ignore_errors=True)
                    removed += 1
            except OSError:
                pass
    return removed
