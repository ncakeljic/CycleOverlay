# CycleOverlay

GPX-Telemetrie (Geschwindigkeit, Leistung, Herzfrequenz, Steigung …) als Video mit
transparentem Hintergrund – zum Darüberlegen auf Action-Cam-Aufnahmen in
Final Cut Pro oder DaVinci Resolve. Läuft auf macOS und Windows.

**Stand: Phase 4** – grafische Oberfläche, GPX- und FIT-Import, Video-Synchronisation,
Widgets (Wert, Rundinstrument, Balken, Navigations-Minimap, Streckenübersicht, Profil),
eigenständige Projektpakete, Stapel-Export mehrerer Clips und
Export mit Alpha-Kanal (ProRes 4444, HEVC mit Alpha auf dem Mac, QuickTime Animation, PNG-Sequenz).

## Einrichtung

Voraussetzungen:

| | macOS | Windows |
|---|---|---|
| uv (Python-Umgebung) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| ffmpeg | `brew install ffmpeg` | `winget install ffmpeg` |

Dann im Projektordner:

```bash
uv sync          # installiert Python 3.11+ und alle Abhängigkeiten in .venv
uv run pytest    # alle Tests ausführen
```

## Oberfläche starten

```bash
uv run cycle-overlay                       # leeres Projekt
uv run cycle-overlay fahrt.gpx GX010123.MP4  # direkt mit Dateien (auch per Drag-and-Drop ins Fenster)
```

Ablauf:

1. **GPX/FIT öffnen** (links) – die erkannten Signale erscheinen in der Tabelle.
   FIT-Dateien direkt vom Garmin/Wahoo liefern meist genauere Geschwindigkeit und Distanz
   (Radsensor) und funktionieren auch für Indoor-Fahrten ohne GPS.
2. Optional **Video öffnen** – es wird automatisch über die Aufnahmezeit synchronisiert.
   Im Tab **Sync** Zeitzone/Kamera prüfen, Offset feinjustieren oder einen *Sync-Punkt* setzen
   (Moment im Video einstellen → „Sync-Punkt setzen“ → im Geschwindigkeitsdiagramm anklicken).
   Ohne Video wählst du den Export-Bereich unten mit **In/Out**.
3. Widgets in der Vorschau anklicken und **ziehen** (Raster, Alt = frei; Pfeiltasten = fein,
   Shift = grob, Entf = löschen). Eigenschaften im Tab **Widget**; neue mit **+ Widget**.
4. Tab **Export** → **Exportieren**. Das Overlay landet standardmäßig neben dem Video.
   **Mehrere Clips exportieren …** erzeugt für viele Videos derselben Fahrt (z. B. GoPro-Kapitel)
   je ein passendes Overlay – Sync-Einstellungen und Layout gelten für alle.
5. **Datei → Projekt speichern** (`.cyov`) erzeugt ein eigenständiges Projektpaket – wie bei
   Final Cut/Resolve: Fahrtdatei, Kartendaten, Layout, Sync, Export-Einstellungen und In/Out stecken
   darin. Videos werden (wie in Schnittprogrammen) nur verknüpft; fehlen sie, hilft
   „Video neu verknüpfen …“. **Layout → Als Preset speichern** macht ein Layout wiederverwendbar.

### Minimap (Navi/GTA-Stil)

Widget „Minimap (Navi)“ hinzufügen (oder Preset **rennrad_navi**). Einstellungen im Tab **Widget**:
Fahrtrichtung oben oder Norden oben · von oben oder 3D-gekippt (Neigung wählbar) · Straßen aus
OpenStreetMap oder nur Route · dunkles (GTA) oder helles (Navi) Thema · rund oder abgerundet ·
Sichtweite und Auto-Zoom · Pfeil-Stil, -Größe und -Farbe · Kompass, Maßstab, OSM-Hinweis.

Höhe sichtbar machen: **Route einfärben → nach Steigung** (blau Abfahrt · grün flach · gelb · orange · rot ·
dunkelrot; Stufen einstellbar), **Steigung neben dem Pfeil**, und in der gekippten Ansicht
**Route als 3D-Höhenband** (Überhöhung einstellbar, optional mit „Vorhang“ bis zum Boden).

Abfahrten: **Farbschema „Anstiege und Abfahrten abgestuft“** (hellblau ab −2 % · blau ab −5 % ·
dunkelblau ab −8 % · violett ab −12 %), im 3D-Höhenband wahlweise **Höhenbezug „tiefster Punkt im
Umkreis“** (alles über dem Boden, Abfahrt als Rampe ins Tal) statt „meine Position“.

Widget **Anstiegs-Vorschau** (wie „ClimbPro“, wahlweise Anstiege, Abfahrten oder beides): Profil der nächsten Kilometer nach Steigung eingefärbt,
dazu „IN 0.8 km · 1.5 km · 75 hm · Ø 5.0 %“ bzw. „ANSTIEG · noch 750 m · 37 hm“. Was als Anstieg zählt
(Mindestlänge, -höhenmeter, -steigung), ist einstellbar.

Straßen: links **Kartendaten laden** (einmal Internet, ein Korridor von 1 km je Seite um die Strecke;
Gehsteige, Einfahrten und Privatwege werden weggelassen;
die Daten werden im Projekt und im Zwischenspeicher abgelegt). Kartendaten
© OpenStreetMap-Mitwirkende (ODbL) – der Hinweis im Video ist standardmäßig an.

Tastatur: Leertaste = Abspielen/Pause, Strg+←/→ = 1 Frame.

## Kommandozeile

```bash
uv run cycle-overlay check                                   # ffmpeg & Ordner prüfen
uv run cycle-overlay info  FAHRT.gpx                         # Zusammenfassung & verfügbare Signale
uv run cycle-overlay values FAHRT.gpx --at 0:42:30           # Werte zu einer Track-Zeit
uv run cycle-overlay sync  FAHRT.gpx CLIP.MP4                # Video synchronisieren & prüfen
uv run cycle-overlay frames FAHRT.gpx --video CLIP.MP4 --out werte.csv   # Werte für jedes Frame

uv run cycle-overlay preview FAHRT.gpx --video CLIP.MP4 --video-at 0:00:10 --out vorschau.png
uv run cycle-overlay render  FAHRT.gpx --video CLIP.MP4          # -> CLIP_overlay.mov neben dem Video
uv run cycle-overlay render  FAHRT.gpx --start 1:05:00 --duration 30 --fps 29.97 --size 3840x2160 --out abfahrt.mov
uv run cycle-overlay render  FAHRT.fit --video GX01*.MP4 --out overlays   # mehrere Clips auf einmal
uv run cycle-overlay presets                                     # verfügbare Layouts (rennrad_minimal, rennrad_navi, pro_cycling_cockpit)
uv run cycle-overlay mapdata FAHRT.gpx                           # Kartendaten probeweise laden (Diagnose)
```

Weitere Optionen für `render`/`preview`:

- `--preset NAME|datei.json` – Layout (Standard: `rennrad_minimal`)
- `--profile prores4444|hevc_alpha|qtrle|png_sequence` – Exportformat (Standard: ProRes 4444;
  `hevc_alpha` nur auf dem Mac, deutlich kleinere Dateien)
- `--video A.MP4 B.MP4 …` – Stapel-Export; `--out` ist dann ein Ordner
- `--size 3840x2160` – Auflösung (Standard: wie Video bzw. 1920x1080)
- `--overwrite` – vorhandene Datei überschreiben
- `--transparent` (nur `preview`) – PNG mit echtem transparentem Hintergrund

Abbrechen mit Ctrl+C – unvollständige Dateien werden entfernt.

### Overlay im Schnittprogramm verwenden

- **Final Cut Pro:** `CLIP_overlay.mov` importieren und als verbundenen Clip über den Originalclip legen.
  Die Transparenz wird automatisch erkannt.
- **DaVinci Resolve:** Overlay auf eine Spur über dem Originalclip legen. Falls der Hintergrund schwarz
  erscheint: Clip-Attribute → Alpha-Modus „Straight“ bzw. „Premultiplied“ prüfen (Straight ist richtig).
- Beide Clips am selben Startpunkt ausrichten – Länge und Framerate sind identisch.

Nützliche Optionen:

- `--tz Europe/Berlin` – Zeitzone der Kamerauhr bzw. für die Uhrzeit-Anzeige (Standard: Rechner)
- `--camera gopro|dji|insta360|apple|generic` – Kamera-Profil (Standard: automatisch erkannt)
- `--offset -2.5` – manuelle Sync-Korrektur in Sekunden
- `--units imperial` – mph, Meilen, Fuß, °F
- `--pause-mode hold` – in Pausen letzten Wert halten statt 0

Ohne Video: `frames FAHRT.gpx --start 1:05:00 --duration 30 --fps 29.97 --out werte.csv`

## Testdaten

`tests/fixtures/sample_ride.gpx` ist eine synthetische 20-Minuten-Fahrt mit bekannten
Sollwerten (9,00 km, 75 Höhenmeter, Anstieg 5 %, Abfahrt −6 %, Stillstand, Auto-Pause,
HF-Dropouts). Neu erzeugen: `uv run python tests/fixtures/make_sample_ride.py`.

## Projektstruktur

```
src/cycle_overlay/
  core/       GPX-Parser, Datenaufbereitung, Video-Metadaten, Sync, Einheiten, Einstellungen
  gui/        Oberfläche: Hauptfenster, Vorschau, Timeline, Panels (Logik in core/project.py)
  rendering/  Layout (JSON), Werte pro Frame, Qt-Renderer und Widgets (Text, Rundinstrument, Balken)
  export/     ffmpeg-Suche, Exportprofile, Export per Pipe
  resources/  mitgelieferte Schrift (Inter, OFL-Lizenz) und Presets
  cli.py, cli_render.py   Kommandozeile
packaging/    spätere Installer (Phase 4)
tests/        pytest
```
