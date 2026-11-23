# Installer bauen

## Windows

Den Projektordner `cycle-overlay` (ohne `.venv`) auf den Windows-PC kopieren, dort PowerShell öffnen und
im Projektordner ausführen:

```
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
```

Das Skript installiert bei Bedarf **uv** und **Inno Setup** (per winget), lädt **ffmpeg** (LGPL-Build von
BtbN) zum Mitliefern, führt die Tests aus, bündelt die App mit **PyInstaller**, prüft sie mit
`CycleOverlay.exe selftest` und baut den Installer:

```
packaging\windows\Output\CycleOverlay-Setup-<Version>.exe
```

Der Installer legt CycleOverlay unter „Programme“ ab (oder nur für den aktuellen Benutzer), mit
Startmenü-Eintrag, optional Desktop-Symbol und Zuordnung der Projektdateien `.cyov`. Ein neuerer
Installer ersetzt die alte Version; Einstellungen, Presets und Protokolle (im Benutzerordner unter
`%APPDATA%\CycleOverlay`) bleiben erhalten.

Optionen: `-SkipTests` (schneller), `-SkipFfmpeg` (ffmpeg nicht mitliefern, die App sucht es dann im System).

## Dateien

- `launcher.py` – Startpunkt der gebündelten App
- `cycleoverlay.spec` – PyInstaller-Konfiguration (Ordner-Modus, ohne Konsole; Windows und macOS)
- `windows/cycleoverlay.iss` – Inno-Setup-Skript
- `windows/cycleoverlay.ico` – Programmsymbol
- `windows/build.ps1` – baut alles in einem Schritt

## Hinweise

- Ein Windows-Installer lässt sich nur unter Windows bauen (PyInstaller kann nicht über Kreuz bauen).
- Ohne Code-Signatur zeigt Windows SmartScreen beim ersten Start „Unbekannter Herausgeber“ →
  „Weitere Informationen“ → „Trotzdem ausführen“.
- ffmpeg steht unter LGPL; die Lizenz liegt der App bei (`_internal\bin`). Für eigene Nutzung unkritisch,
  bei Weitergabe die LGPL-Bedingungen beachten.
- Die App prüft sich nach der Installation jederzeit mit: `"C:\Program Files\CycleOverlay\CycleOverlay.exe" selftest`
  (Ergebnis in `%TEMP%\cycleoverlay_selftest.txt`).


## Neue Version veröffentlichen (GitHub)

1. `__version__` in `src/cycle_overlay/__init__.py` erhöhen und oben in `CHANGELOG.md` einen
   Abschnitt `## X.Y.Z` schreiben.
2. Committen und pushen, dann den Tag setzen:

   ```bash
   git tag vX.Y.Z
   git push --tags
   ```

3. GitHub Actions (`.github/workflows/release.yml`) prüft, ob Tag und Version zusammenpassen, baut
   den Windows-Installer und veröffentlicht das Release mit dem CHANGELOG-Abschnitt als Text.
4. Installierte Apps finden das Update beim nächsten Start (bzw. über Hilfe → „Nach Updates suchen …“).

Das Repository muss **öffentlich** sein, damit die Apps die Releases ohne Anmeldung abrufen können.
Der Installer ist nicht signiert; Windows (SmartScreen/Smart App Control) kann deshalb warnen.


## macOS und Linux

| System | Befehl (im Projektordner) | Ergebnis in `dist/` |
|---|---|---|
| macOS (Apple Silicon) | `bash packaging/macos/build.sh` | `CycleOverlay-<Version>-macOS-arm64.dmg` |
| Linux (Debian/Ubuntu) | `bash packaging/linux/build.sh` | `cycleoverlay_<Version>_amd64.deb`, `CycleOverlay-<Version>-linux-x86_64.tar.gz` |

Beide brauchen `uv`; der Selbsttest braucht ffmpeg (`brew install ffmpeg` bzw. `sudo apt install ffmpeg`).
GitHub Actions baut alle drei Systeme automatisch bei jedem Tag `vX.Y.Z`.

- **macOS**: Die App ist nur ad-hoc signiert (nicht notarisiert). Beim ersten Start Rechtsklick →
  „Öffnen“. ffmpeg kommt von Homebrew; wer es mitliefern will, legt `ffmpeg` und `ffprobe` nach
  `packaging/macos/ffmpeg/`.
- **Linux**: Das `.deb` installiert nach `/opt/cycleoverlay`, legt `cycleoverlay` als Befehl und einen
  Menüeintrag an und zieht ffmpeg und die Qt-Systembibliotheken als Abhängigkeiten mit:
  `sudo apt install ./cycleoverlay_<Version>_amd64.deb`.
