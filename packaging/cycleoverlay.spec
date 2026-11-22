# PyInstaller-Konfiguration für CycleOverlay (Ordner-Modus, ohne Konsolenfenster).
#
# Aufruf (aus dem Projektordner):  uv run --with pyinstaller pyinstaller --noconfirm packaging/cycleoverlay.spec
# Ergebnis:                        dist/CycleOverlay/CycleOverlay.exe (Windows) bzw. dist/CycleOverlay.app (macOS)
#
# ffmpeg/ffprobe werden mitgeliefert, wenn sie in packaging/windows/ffmpeg/ bzw. packaging/macos/ffmpeg/
# liegen (das Windows-Build-Skript lädt sie automatisch). Sie landen im Ordner „bin“ der App, wo
# CycleOverlay sie zuerst sucht (app_paths.bundled_bin_dir).

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 – SPECPATH setzt PyInstaller
SRC = ROOT / "src"
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

datas = [(str(SRC / "cycle_overlay" / "resources"), "cycle_overlay/resources")]
try:  # IANA-Zeitzonen (unter Windows nicht im System)
    datas += collect_data_files("tzdata")
except Exception:  # noqa: BLE001
    pass

binaries = []
ff_dir = ROOT / "packaging" / ("windows" if IS_WIN else "macos") / "ffmpeg"
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

icon = ROOT / "packaging" / "windows" / "cycleoverlay.ico" if IS_WIN else None
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
        bundle_identifier="de.nikola.cycleoverlay",
        info_plist={"NSHighResolutionCapable": True},
    )
