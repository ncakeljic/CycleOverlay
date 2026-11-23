#!/usr/bin/env bash
# CycleOverlay als macOS-App (.app) und Disk-Image (.dmg) bauen.
#
#   cd cycle-overlay && bash packaging/macos/build.sh
#
# Ergebnis: dist/CycleOverlay-<Version>-macOS-<arm64|x86_64>.dmg
# Die App ist nicht von Apple signiert/notarisiert: Beim ersten Start Rechtsklick → „Öffnen“.
# ffmpeg wird mitgeliefert, wenn ffmpeg und ffprobe in packaging/macos/ffmpeg/ liegen; sonst nutzt die
# App das System-ffmpeg (brew install ffmpeg).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
VERSION="$(python3 packaging/release_notes.py --version)"
ARCH="$(uname -m)"
DMG="dist/CycleOverlay-${VERSION}-macOS-${ARCH}.dmg"

echo "== CycleOverlay ${VERSION} für macOS (${ARCH}) =="
command -v uv >/dev/null || { echo "uv fehlt: https://docs.astral.sh/uv/ (brew install uv)"; exit 1; }

echo "-- Abhängigkeiten"
uv sync --quiet

echo "-- PyInstaller"
rm -rf build/CycleOverlay dist/CycleOverlay dist/CycleOverlay.app
uv run --with pyinstaller pyinstaller --noconfirm --clean packaging/cycleoverlay.spec

APP="dist/CycleOverlay.app"
[ -d "$APP" ] || { echo "Fehler: $APP wurde nicht erzeugt"; exit 1; }

echo "-- Ad-hoc-Signatur (nötig, damit die App auf Apple Silicon startet)"
codesign --force --deep --sign - "$APP"

echo "-- Selbsttest"
QT_QPA_PLATFORM=offscreen "$APP/Contents/MacOS/CycleOverlay" selftest --out dist/selftest-macos.txt \
    || { echo "Selbsttest fehlgeschlagen – siehe dist/selftest-macos.txt"; exit 1; }

echo "-- Disk-Image"
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Programme"
cp packaging/macos/LIESMICH.txt "$STAGE/" 2>/dev/null || true
rm -f "$DMG"
hdiutil create -volname "CycleOverlay ${VERSION}" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"

echo "Fertig: $DMG"
