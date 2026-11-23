# PyInstaller-Konfiguration für CycleOverlay (Ordner-Modus, ohne Konsolenfenster).
#
# Aufruf (aus dem Projektordner):  uv run --with pyinstaller pyinstaller --noconfirm packaging/cycleoverlay.spec
# Ergebnis:                        dist/CycleOverlay/CycleOverlay(.exe) (Windows/Linux) bzw. dist/CycleOverlay.app (macOS)
#
# ffmpeg/ffprobe werden mitgeliefert, wenn sie in packaging/<windows|macos|linux>/ffmpeg/ liegen (das
# Windows-Build-Skript lädt sie automatisch). Sie landen im Ordner „bin“ der App, wo CycleOverlay sie
# zuerst sucht (app_paths.bundled_bin_dir). Unter macOS und Linux wird sonst das System-ffmpeg benutzt
# (Homebrew bzw. Paketverwaltung).

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 – SPECPATH setzt PyInstaller
SRC = ROOT / "src"
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
PLATFORM_DIR = "windows" if IS_WIN else ("macos" if IS_MAC else "linux")


def _version() -> str:
    import re

    text = (SRC / "cycle_overlay" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)


VERSION = _version()

datas = [(str(SRC / "cycle_overlay" / "resources"), "cycle_overlay/resources")]
try:  # IANA-Zeitzonen (unter Windows nicht im System)
    datas += collect_data_files("tzdata")
except Exception:  # noqa: BLE001
    pass

binaries = []
ff_dir = ROOT / "packaging" / PLATFORM_DIR / "ffmpeg"
for name in ("ffmpeg", "ffprobe"):
    exe = ff_dir / (name + (".exe" if IS_WIN else ""))
    if exe.exists():
        binaries.append((str(exe), "bin"))
if (ff_dir / "FFMPEG-LICENSE.txt").exists():
    datas.append((str(ff_dir / "FFMPEG-LICENSE.txt"), "bin"))

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["truststore", "tzdata", "cycle_overlay.gui.app", "cycle_overlay.cli_render"],
    excludes=[
        "tkinter", "matplotlib", "scipy", "pandas", "IPython", "pytest", "mypy",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick",
        "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

icon = {"windows": ROOT / "packaging" / "windows" / "cycleoverlay.ico",
        "macos": ROOT / "packaging" / "macos" / "cycleoverlay.icns"}.get(PLATFORM_DIR)
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CycleOverlay",
    console=False,
    icon=str(icon) if icon and icon.exists() else None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="CycleOverlay", upx=False)  # noqa: F821

if IS_MAC:
    app = BUNDLE(  # noqa: F821
        coll,
        name="CycleOverlay.app",
        icon=str(icon) if icon and icon.exists() else None,
        bundle_identifier="de.nikola.cycleoverlay",
        version=VERSION,
        info_plist={
            "CFBundleName": "CycleOverlay",
            "CFBundleDisplayName": "CycleOverlay",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
            "NSHumanReadableCopyright": "CycleOverlay",
            # Projektdateien (.cyov) mit CycleOverlay öffnen
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "CycleOverlay-Projekt",
                "CFBundleTypeRole": "Editor",
                "LSHandlerRank": "Owner",
                "CFBundleTypeExtensions": ["cyov"],
            }],
        },
    )
