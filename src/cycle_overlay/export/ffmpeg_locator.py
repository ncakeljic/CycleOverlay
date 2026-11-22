"""Suche nach ffmpeg/ffprobe (PRD 2.4).

Reihenfolge:
1. Pfad aus den App-Einstellungen
2. im App-Bundle mitgelieferte Binärdatei
3. System-PATH
4. typische Installationsorte (wichtig auf macOS: aus dem Finder gestartete
   Apps erben den PATH der Shell nicht, Homebrew wird also nicht gefunden)
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..app_paths import bundled_bin_dir
from ..platform_utils import IS_MACOS, IS_WINDOWS, executable_name, is_blocked_error, run

#: in den Einstellungen gewählte Pfade (``configure``), gelten für alle Aufrufe ohne eigenen Pfad
_CONFIGURED: dict[str, str | None] = {"ffmpeg": None, "ffprobe": None}
#: Ergebnis des Probestarts je Pfad (nur Windows): True = startbar, False = vom System blockiert
_LAUNCHABLE: dict[str, bool] = {}
#: Kandidaten, die das System blockiert hat (für die Fehlermeldung)
BLOCKED: set[Path] = set()


def configure(ffmpeg_path: str | None = None, ffprobe_path: str | None = None) -> None:
    """Pfade aus den App-Einstellungen übernehmen (beim Start und nach manueller Auswahl)."""
    _CONFIGURED["ffmpeg"] = ffmpeg_path or None
    _CONFIGURED["ffprobe"] = ffprobe_path or None


def _launchable(path: Path) -> bool:
    """Unter Windows einmal probehalber starten: Smart App Control/WDAC sperrt erst beim Ausführen
    (WinError 4551) – die Datei existiert und ist „ausführbar“, lässt sich aber nicht starten."""
    if not IS_WINDOWS:
        return True
    key = str(path).lower()
    if key not in _LAUNCHABLE:
        try:
            run([path, "-hide_banner", "-version"], timeout=30)
            ok = True
        except OSError as exc:
            ok = not is_blocked_error(exc)
            if not ok:
                BLOCKED.add(path)
        except Exception:  # noqa: BLE001 – z. B. Zeitüberschreitung: nicht als gesperrt werten
            ok = True
        _LAUNCHABLE[key] = ok
    return _LAUNCHABLE[key]


def _common_dirs() -> list[Path]:
    if IS_MACOS:
        return [Path("/opt/homebrew/bin"), Path("/usr/local/bin"), Path("/opt/local/bin")]
    if IS_WINDOWS:
        dirs = [Path(r"C:\ffmpeg\bin")]
        for env in ("ProgramFiles", "ProgramFiles(x86)"):
            if os.environ.get(env):
                dirs.append(Path(os.environ[env]) / "ffmpeg" / "bin")
        if os.environ.get("LOCALAPPDATA"):
            dirs.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "WinGet" / "Links")
        dirs.append(Path.home() / "scoop" / "shims")
        dirs.append(Path(r"C:\ProgramData\chocolatey\bin"))
        return dirs
    return [Path("/usr/bin"), Path("/usr/local/bin")]


def find_executable(name: str, configured: str | os.PathLike | None = None) -> Path | None:
    """Findet ``ffmpeg`` oder ``ffprobe``; gibt None zurück, wenn nicht vorhanden."""
    exe = executable_name(name)
    candidates: list[Path] = []
    configured = configured or _CONFIGURED.get(name)
    if configured:
        p = Path(configured).expanduser()
        candidates.append(p / exe if p.is_dir() else p)
    candidates.append(bundled_bin_dir() / exe)
    found = shutil.which(exe)
    if found:
        candidates.append(Path(found))
    candidates += [d / exe for d in _common_dirs()]
    seen: set[str] = set()
    for c in candidates:
        key = str(c).lower()
        if key in seen:
            continue
        seen.add(key)
        if c.is_file() and os.access(c, os.X_OK) and _launchable(c):
            return c
    return None


@dataclass
class FFmpegStatus:
    ffmpeg: Path | None = None
    ffprobe: Path | None = None
    version: str | None = None
    has_prores_ks: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.ffmpeg is not None and self.ffprobe is not None and self.has_prores_ks


def locate(ffmpeg_path: str | None = None, ffprobe_path: str | None = None) -> FFmpegStatus:
    status = FFmpegStatus()
    status.ffmpeg = find_executable("ffmpeg", ffmpeg_path)
    status.ffprobe = find_executable("ffprobe", ffprobe_path)
    for name, found in (("ffmpeg", status.ffmpeg), ("ffprobe", status.ffprobe)):
        if found is not None:
            continue
        blocked = [b for b in BLOCKED if b.stem.lower() == name]
        if blocked:
            from ..platform_utils import blocked_message

            status.errors.append(blocked_message(blocked[0]))
        else:
            status.errors.append(f"{name} wurde nicht gefunden.")
    if status.ffmpeg is None:
        return status
    try:
        res = run([status.ffmpeg, "-hide_banner", "-version"], timeout=20)
        m = re.search(r"ffmpeg version (\S+)", res.stdout)
        status.version = m.group(1) if m else None
        enc = run([status.ffmpeg, "-hide_banner", "-encoders"], timeout=20)
        status.has_prores_ks = bool(re.search(r"^\s*\S+\s+prores_ks\s", enc.stdout, re.MULTILINE))
        if not status.has_prores_ks:
            status.errors.append("Der Encoder 'prores_ks' fehlt in dieser ffmpeg-Version.")
    except Exception as exc:  # noqa: BLE001 – jeden Ausführungsfehler melden
        status.errors.append(f"ffmpeg konnte nicht gestartet werden: {exc}")
    return status


def install_hint() -> str:
    if IS_MACOS:
        return "Installation unter macOS: 'brew install ffmpeg' (Homebrew: https://brew.sh)"
    if IS_WINDOWS:
        return "Installation unter Windows: 'winget install ffmpeg' in der Eingabeaufforderung"
    return "Installation unter Linux: z. B. 'sudo apt install ffmpeg'"
