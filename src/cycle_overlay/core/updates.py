"""Update-Prüfung über GitHub Releases – ohne Qt.

Neue Versionen werden als Release mit dem Tag ``vX.Y.Z`` auf
https://github.com/ncakeljic/CycleOverlay veröffentlicht; am Release hängt der Windows-Installer
``CycleOverlay-Setup-X.Y.Z.exe`` (und später ein ``.dmg`` für macOS). Die App fragt die
öffentliche GitHub-API ab (ohne Anmeldung, das Repository muss öffentlich sein), vergleicht die
Version und lädt auf Wunsch den passenden Installer herunter.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .. import __version__

log = logging.getLogger(__name__)

REPO = "ncakeljic/CycleOverlay"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
USER_AGENT = f"CycleOverlay/{__version__} (Update-Pruefung)"
#: höchstens so oft automatisch prüfen (Sekunden)
CHECK_INTERVAL_S = 24 * 3600


class UpdateError(RuntimeError):
    pass


class UpdateCancelled(UpdateError):
    pass


def parse_version(text: str) -> tuple[int, int, int] | None:
    """``"v1.7.0"`` / ``"1.7"`` -> (1, 7, 0); None, wenn keine Versionsnummer erkennbar ist."""
    m = re.match(r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(text or ""))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0)


def is_newer(candidate: str, current: str = __version__) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    return a is not None and b is not None and a > b


@dataclass
class UpdateInfo:
    version: str  # "1.8.0"
    tag: str  # "v1.8.0"
    notes: str  # Release-Text (Markdown)
    page_url: str
    published: str = ""
    asset_name: str | None = None
    asset_url: str | None = None
    asset_size: int = 0

    @property
    def downloadable(self) -> bool:
        return bool(self.asset_url)


def platform_key() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return "other"


def pick_asset(assets: list[dict], platform: str | None = None) -> dict | None:
    """Passenden Installer aus den Release-Dateien wählen."""
    platform = platform or platform_key()
    named = [(str(a.get("name", "")), a) for a in assets or [] if a.get("browser_download_url")]
    if platform == "windows":
        exes = [(n, a) for n, a in named if n.lower().endswith(".exe")]
        setup = [x for x in exes if "setup" in x[0].lower()]
        chosen = (setup or exes or [None])[0]
    elif platform == "macos":
        dmgs = [(n, a) for n, a in named if n.lower().endswith((".dmg", ".pkg"))]
        # Apple Silicon und Intel getrennt gebaut: passende Architektur bevorzugen
        arch = "arm64" if _machine() in ("arm64", "aarch64") else "x86_64"
        fitting = [x for x in dmgs if arch in x[0].lower()]
        chosen = (fitting or [x for x in dmgs if not any(a in x[0].lower() for a in ("arm64", "x86_64"))]
                  or [None])[0]
    elif platform == "linux":
        debs = [(n, a) for n, a in named if n.lower().endswith(".deb")]
        tars = [(n, a) for n, a in named if n.lower().endswith((".tar.gz", ".appimage"))]
        chosen = (debs or tars or [None])[0]
    else:
        chosen = None
    return chosen[1] if chosen else None


def parse_release(data: dict, platform: str | None = None) -> UpdateInfo:
    tag = str(data.get("tag_name") or "")
    ver = parse_version(tag)
    if ver is None:
        raise UpdateError(f"Release ohne gültige Versionsnummer: {tag!r}")
    asset = pick_asset(data.get("assets") or [], platform)
    return UpdateInfo(
        version=".".join(map(str, ver)),
        tag=tag,
        notes=str(data.get("body") or "").strip(),
        page_url=str(data.get("html_url") or RELEASES_PAGE),
        published=str(data.get("published_at") or "")[:10],
        asset_name=asset.get("name") if asset else None,
        asset_url=asset.get("browser_download_url") if asset else None,
        asset_size=int(asset.get("size") or 0) if asset else 0,
    )


def _open(url: str, timeout: float, accept: str):
    from .osm import ssl_context

    ctx, _ = ssl_context()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    return urllib.request.urlopen(req, timeout=timeout, context=ctx)  # noqa: S310 – feste https-URL


def fetch_latest(timeout: float = 15) -> dict:
    try:
        with _open(API_LATEST, timeout, "application/vnd.github+json") as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("Noch keine Version veröffentlicht (oder das Repository ist nicht öffentlich).") from exc
        if exc.code in (403, 429):
            raise UpdateError("GitHub hat die Anfrage vorübergehend begrenzt – später erneut versuchen.") from exc
        raise UpdateError(f"GitHub antwortet mit Fehler {exc.code}.") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        raise UpdateError(f"Keine Verbindung zu GitHub: {reason}") from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise UpdateError(f"Ungültige Antwort von GitHub: {exc}") from exc


def check_for_update(current: str = __version__, fetch: Callable[[], dict] = fetch_latest,
                     platform: str | None = None) -> UpdateInfo | None:
    """Neuere Version als ``current`` oder None (aktuell)."""
    info = parse_release(fetch(), platform)
    return info if is_newer(info.version, current) else None


def due(last_check: float, now: float | None = None) -> bool:
    """Ist die tägliche automatische Prüfung fällig?"""
    now = time.time() if now is None else now
    return not last_check or now - float(last_check) >= CHECK_INTERVAL_S or now < float(last_check)


def download(info: UpdateInfo, dest_dir: str | os.PathLike, *, progress: Callable[[int, int], None] | None = None,
             cancel: threading.Event | None = None, opener=None, timeout: float = 30) -> Path:
    """Installer nach ``dest_dir`` laden (erst als ``.part``, Größe wird geprüft)."""
    if not info.asset_url or not info.asset_name:
        raise UpdateError("Für dieses Betriebssystem liegt dem Release kein Installer bei.")
    name = Path(info.asset_name).name  # keine Pfadangaben aus der Antwort übernehmen
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / name
    tmp = dest / (name + ".part")
    done = 0
    try:
        with (opener or _open)(info.asset_url, timeout, "application/octet-stream") as resp, open(tmp, "wb") as fh:
            total = int(resp.headers.get("Content-Length") or info.asset_size or 0)
            while True:
                if cancel is not None and cancel.is_set():
                    raise UpdateCancelled("Download abgebrochen.")
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except UpdateError:
        tmp.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        tmp.unlink(missing_ok=True)
        raise UpdateError(f"Download fehlgeschlagen: {getattr(exc, 'reason', exc)}") from exc
    expected = info.asset_size
    if expected and done != expected:
        tmp.unlink(missing_ok=True)
        raise UpdateError(f"Download unvollständig ({done} von {expected} Bytes).")
    os.replace(tmp, target)
    return target


def _machine() -> str:
    import platform

    return platform.machine().lower()


def can_self_update() -> bool:
    """Nur die installierte App kann sich (mit Hilfe des Installers) selbst ersetzen."""
    from ..app_paths import is_frozen

    return is_frozen() and platform_key() in ("windows", "macos", "linux")


def launch_installer(path: str | os.PathLike) -> None:
    """Installer starten; die App beendet sich danach selbst (der Installer schließt sie sonst)."""
    path = Path(path)
    if platform_key() == "windows":
        # /SILENT: nur Fortschrittsfenster; der Installer schließt CycleOverlay und startet es danach neu
        subprocess.Popen([str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/CLOSEAPPLICATIONS", "/NORESTART"],
                         close_fds=True)
    elif platform_key() == "macos":
        subprocess.Popen(["open", str(path)])  # öffnet das Disk-Image – App in „Programme“ ziehen
    elif platform_key() == "linux":
        subprocess.Popen(["xdg-open", str(path)])  # .deb öffnet die Software-Verwaltung
    else:
        raise UpdateError("Automatische Installation wird auf diesem System nicht unterstützt.")
