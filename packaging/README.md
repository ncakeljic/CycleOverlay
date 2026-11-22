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
