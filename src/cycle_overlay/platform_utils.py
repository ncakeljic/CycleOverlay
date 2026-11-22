"""Alles Plattformspezifische an einer Stelle (PRD 2.2)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Sequence

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"


def subprocess_kwargs() -> dict:
    """Zusätzliche Argumente für subprocess, damit unter Windows kein Konsolenfenster aufgeht."""
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NO_WINDOW}  # type: ignore[attr-defined]
    return {}


#: Windows-Fehlercodes, wenn Smart App Control, WDAC/AppLocker, eine Gruppenrichtlinie oder der
#: Virenschutz das Starten einer Datei verhindert (4551 = „Eine Anwendungssteuerungsrichtlinie hat
#: diese Datei blockiert“).
BLOCKED_WINERRORS = frozenset({225, 1260, 4551, 4556, 4557, 4558, 4559})


class ExecutableBlocked(OSError):
    """Das Betriebssystem hat das Starten eines Hilfsprogramms (ffmpeg/ffprobe) verhindert."""

    def __init__(self, exe: str | Path, cause: OSError):
        self.exe = Path(str(exe))
        self.cause = cause
        super().__init__(getattr(cause, "errno", None), blocked_message(self.exe))

    def __str__(self) -> str:
        return blocked_message(self.exe)


def is_blocked_error(exc: BaseException) -> bool:
    if isinstance(exc, ExecutableBlocked):
        return True
    return isinstance(exc, OSError) and getattr(exc, "winerror", None) in BLOCKED_WINERRORS


def blocked_message(exe: str | Path) -> str:
    name = Path(str(exe)).name
    return (
        f"Windows hat das Starten von {name} blockiert (Smart App Control bzw. eine Anwendungssteuerungs-"
        f"Richtlinie). Datei: {exe}\n"
        "Abhilfe: ffmpeg separat installieren ('winget install Gyan.FFmpeg' in der Eingabeaufforderung) "
        "und unter Hilfe → „ffmpeg prüfen …“ auswählen – CycleOverlay weicht dann automatisch darauf aus. "
        "Alternativ die Datei in der Windows-Sicherheit unter „App- & Browsersteuerung“ zulassen."
    )


def _exe_of(cmd: Sequence[str | Path]) -> str:
    return str(cmd[0]) if len(cmd) else "?"


def popen(cmd: Sequence[str | Path], **kwargs) -> subprocess.Popen:
    """``subprocess.Popen`` ohne Konsolenfenster; eine Sperre durch Windows wird zu ``ExecutableBlocked``."""
    args = [str(c) for c in cmd]
    try:
        return subprocess.Popen(args, **{**subprocess_kwargs(), **kwargs})
    except OSError as exc:
        if is_blocked_error(exc):
            raise ExecutableBlocked(args[0], exc) from exc
        raise


def run_bytes(cmd: Sequence[str | Path], timeout: float | None = 60) -> subprocess.CompletedProcess:
    """Wie ``run``, aber mit Bytes-Ausgabe (für Rohbilder)."""
    args = [str(c) for c in cmd]
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout, check=False, **subprocess_kwargs())
    except OSError as exc:
        if is_blocked_error(exc):
            raise ExecutableBlocked(args[0], exc) from exc
        raise


def run(cmd: Sequence[str | Path], timeout: float | None = 60) -> subprocess.CompletedProcess:
    """Programm ausführen und Ausgabe als Text zurückgeben (wirft keine Exception bei Exit-Code != 0).

    Verhindert Windows den Start (Smart App Control o. Ä.), kommt ``ExecutableBlocked`` mit einer
    verständlichen Meldung statt des rohen ``WinError 4551``.
    """
    args = [str(c) for c in cmd]
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            **subprocess_kwargs(),
        )
    except OSError as exc:
        if is_blocked_error(exc):
            raise ExecutableBlocked(_exe_of(args), exc) from exc
        raise


def executable_name(name: str) -> str:
    """'ffmpeg' -> 'ffmpeg.exe' unter Windows."""
    return f"{name}.exe" if IS_WINDOWS and not name.lower().endswith(".exe") else name


def open_in_file_manager(path: Path) -> None:
    """Datei im Finder/Explorer anzeigen (bzw. Ordner öffnen)."""
    path = Path(path)
    if IS_MACOS:
        subprocess.Popen(["open", "-R", str(path)] if path.is_file() else ["open", str(path)])
    elif IS_WINDOWS:
        if path.is_file():
            subprocess.Popen(["explorer", f"/select,{path}"])
        else:
            subprocess.Popen(["explorer", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent if path.is_file() else path)])
