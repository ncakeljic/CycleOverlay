#!/usr/bin/env bash
# CycleOverlay für Linux bauen: Debian/Ubuntu-Paket (.deb) und Archiv (.tar.gz).
#
#   cd cycle-overlay && bash packaging/linux/build.sh
#
# Ergebnis: dist/cycleoverlay_<Version>_amd64.deb und dist/CycleOverlay-<Version>-linux-x86_64.tar.gz
# Das .deb installiert nach /opt/cycleoverlay, legt den Befehl „cycleoverlay“ und einen Eintrag im
# Anwendungsmenü an und zieht ffmpeg sowie die nötigen Qt-Systembibliotheken als Abhängigkeiten mit.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
VERSION="$(python3 packaging/release_notes.py --version)"
ARCH_DEB="$(dpkg --print-architecture 2>/dev/null || echo amd64)"
ARCH="$(uname -m)"

echo "== CycleOverlay ${VERSION} für Linux (${ARCH}) =="
command -v uv >/dev/null || { echo "uv fehlt: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }

echo "-- Abhängigkeiten"
uv sync --quiet

echo "-- PyInstaller"
rm -rf build/CycleOverlay dist/CycleOverlay
uv run --with pyinstaller pyinstaller --noconfirm --clean packaging/cycleoverlay.spec
[ -x dist/CycleOverlay/CycleOverlay ] || { echo "Fehler: dist/CycleOverlay/CycleOverlay fehlt"; exit 1; }

echo "-- Selbsttest"
QT_QPA_PLATFORM=offscreen dist/CycleOverlay/CycleOverlay selftest --out dist/selftest-linux.txt \
    || { echo "Selbsttest fehlgeschlagen – siehe dist/selftest-linux.txt"; exit 1; }

echo "-- Archiv"
TAR="dist/CycleOverlay-${VERSION}-linux-${ARCH}.tar.gz"
cp packaging/linux/cycleoverlay.png dist/CycleOverlay/
tar -C dist -czf "$TAR" CycleOverlay

echo "-- Debian-Paket"
PKG="$(mktemp -d)/cycleoverlay"
mkdir -p "$PKG/DEBIAN" "$PKG/opt" "$PKG/usr/bin" "$PKG/usr/share/applications" \
         "$PKG/usr/share/icons/hicolor/256x256/apps" "$PKG/usr/share/mime/packages"
cp -R dist/CycleOverlay "$PKG/opt/cycleoverlay"
ln -s /opt/cycleoverlay/CycleOverlay "$PKG/usr/bin/cycleoverlay"
cp packaging/linux/cycleoverlay.png "$PKG/usr/share/icons/hicolor/256x256/apps/cycleoverlay.png"
cp packaging/linux/cycleoverlay.desktop "$PKG/usr/share/applications/"
cp packaging/linux/cycleoverlay-mime.xml "$PKG/usr/share/mime/packages/cycleoverlay.xml"
SIZE_KB="$(du -sk "$PKG" | cut -f1)"
cat > "$PKG/DEBIAN/control" <<CONTROL
Package: cycleoverlay
Version: ${VERSION}
Section: video
Priority: optional
Architecture: ${ARCH_DEB}
Installed-Size: ${SIZE_KB}
Maintainer: CycleOverlay <noreply@example.invalid>
Depends: ffmpeg, libegl1, libgl1, libfontconfig1, libdbus-1-3, libxkbcommon-x11-0, libxcb-cursor0, libxcb-icccm4, libxcb-keysyms1, libxcb-image0, libxcb-render-util0, libxcb-shape0, libxcb-xinerama0, libxcb-randr0
Homepage: https://github.com/ncakeljic/CycleOverlay
Description: Telemetrie-Overlays aus GPX/FIT für Videos
 CycleOverlay legt Tempo, Puls, Leistung, Höhenprofil und Karte als
 transparente Ebene oder direkt eingebrannt über Action-Cam-Videos.
CONTROL
cat > "$PKG/DEBIAN/postinst" <<'POST'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null && update-desktop-database -q /usr/share/applications || true
command -v update-mime-database >/dev/null && update-mime-database /usr/share/mime || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q /usr/share/icons/hicolor || true
POST
chmod 755 "$PKG/DEBIAN/postinst"
DEB="dist/cycleoverlay_${VERSION}_${ARCH_DEB}.deb"
dpkg-deb --root-owner-group --build "$PKG" "$DEB" >/dev/null
rm -rf "$(dirname "$PKG")"

echo "Fertig: $DEB"
echo "        $TAR"
