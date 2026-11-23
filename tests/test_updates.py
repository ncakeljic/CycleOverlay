"""Update-Prüfung über GitHub Releases (ohne Qt, ohne Netz)."""

from __future__ import annotations

import io
import threading

import pytest

from cycle_overlay import __version__
from cycle_overlay.core import updates as up

RELEASE = {
    "tag_name": "v9.1.0",
    "html_url": "https://github.com/ncakeljic/CycleOverlay/releases/tag/v9.1.0",
    "published_at": "2026-12-01T10:00:00Z",
    "body": "## Neu\n- Etwas Tolles",
    "assets": [
        {"name": "CycleOverlay-9.1.0.dmg", "size": 20, "browser_download_url": "https://x/mac.dmg"},
        {"name": "CycleOverlay-Setup-9.1.0.exe", "size": 10, "browser_download_url": "https://x/setup.exe"},
        {"name": "SHA256SUMS.txt", "size": 1, "browser_download_url": "https://x/sums"},
    ],
}


def test_versions():
    assert up.parse_version("v1.7.0") == (1, 7, 0)
    assert up.parse_version("1.8") == (1, 8, 0)
    assert up.parse_version("v2.0.0-beta1") == (2, 0, 0)
    assert up.parse_version("latest") is None
    assert up.is_newer("v1.10.0", "1.9.9") and not up.is_newer("1.7.0", "1.7.0")
    assert not up.is_newer("quatsch", "1.0.0")


def test_pick_asset_per_platform():
    assert up.pick_asset(RELEASE["assets"], "windows")["name"].endswith("Setup-9.1.0.exe")
    assert up.pick_asset(RELEASE["assets"], "macos")["name"].endswith(".dmg")
    assert up.pick_asset(RELEASE["assets"], "other") is None
    assert up.pick_asset([], "windows") is None


def test_check_for_update():
    info = up.check_for_update("1.7.0", fetch=lambda: RELEASE, platform="windows")
    assert info.version == "9.1.0" and info.asset_size == 10 and info.downloadable
    assert info.published == "2026-12-01" and "Etwas Tolles" in info.notes
    assert up.check_for_update("9.1.0", fetch=lambda: RELEASE) is None
    with pytest.raises(up.UpdateError):
        up.check_for_update(fetch=lambda: {"tag_name": "nightly"})


def test_repo_is_the_users():
    assert up.REPO == "ncakeljic/CycleOverlay"
    assert up.API_LATEST == "https://api.github.com/repos/ncakeljic/CycleOverlay/releases/latest"


def test_daily_check_due():
    assert up.due(0, now=1000)
    assert not up.due(1000, now=1000 + 3600)
    assert up.due(1000, now=1000 + 25 * 3600)
    assert up.due(5000, now=1000)  # Uhr zurückgestellt


class _Resp(io.BytesIO):
    def __init__(self, data: bytes, length: int | None = None):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data) if length is None else length)}


def _info(size=10, name="CycleOverlay-Setup-9.1.0.exe"):
    return up.UpdateInfo("9.1.0", "v9.1.0", "", "https://x", asset_name=name, asset_url="https://x/s.exe",
                         asset_size=size)


def test_download(tmp_path):
    seen = []
    path = up.download(_info(), tmp_path, opener=lambda url, t, a: _Resp(b"0123456789"),
                       progress=lambda d, t: seen.append((d, t)))
    assert path == tmp_path / "CycleOverlay-Setup-9.1.0.exe" and path.read_bytes() == b"0123456789"
    assert seen[-1] == (10, 10) and not list(tmp_path.glob("*.part"))


def test_download_checks_size_and_sanitizes_name(tmp_path):
    with pytest.raises(up.UpdateError, match="unvollständig"):
        up.download(_info(size=99), tmp_path, opener=lambda *a: _Resp(b"kurz"))
    assert not list(tmp_path.iterdir())
    path = up.download(_info(size=0, name="../../böse.exe"), tmp_path, opener=lambda *a: _Resp(b"x"))
    assert path.parent == tmp_path


def test_download_cancel(tmp_path):
    ev = threading.Event()
    ev.set()
    with pytest.raises(up.UpdateCancelled):
        up.download(_info(), tmp_path, opener=lambda *a: _Resp(b"0123456789"), cancel=ev)
    assert not list(tmp_path.iterdir())


def test_no_asset_no_download(tmp_path):
    with pytest.raises(up.UpdateError):
        up.download(up.UpdateInfo("9.1.0", "v9.1.0", "", "https://x"), tmp_path)


def test_source_checkout_cannot_self_update():
    assert not up.can_self_update()  # Tests laufen nie als gebündelte App
    assert up.USER_AGENT.endswith("(Update-Pruefung)") and __version__ in up.USER_AGENT


def test_pick_asset_macos_arch_and_linux(monkeypatch):
    assets = [
        {"name": "CycleOverlay-Setup-9.1.0.exe", "browser_download_url": "u1"},
        {"name": "CycleOverlay-9.1.0-macOS-arm64.dmg", "browser_download_url": "u2"},
        {"name": "CycleOverlay-9.1.0-macOS-x86_64.dmg", "browser_download_url": "u3"},
        {"name": "cycleoverlay_9.1.0_amd64.deb", "browser_download_url": "u4"},
        {"name": "CycleOverlay-9.1.0-linux-x86_64.tar.gz", "browser_download_url": "u5"},
    ]
    monkeypatch.setattr(up, "_machine", lambda: "arm64")
    assert up.pick_asset(assets, "macos")["browser_download_url"] == "u2"
    monkeypatch.setattr(up, "_machine", lambda: "x86_64")
    assert up.pick_asset(assets, "macos")["browser_download_url"] == "u3"
    assert up.pick_asset(assets, "linux")["browser_download_url"] == "u4"
    assert up.pick_asset(assets[4:], "linux")["browser_download_url"] == "u5"
    assert up.pick_asset(assets[:1], "macos") is None
