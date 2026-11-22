# Änderungen

Versionen nach dem Schema **MAJOR.MINOR.PATCH**: PATCH = Fehlerbehebungen, MINOR = neue
Funktionen, MAJOR = inkompatible Änderungen (z. B. am Projektformat). Die laufende Version
steht im Fenstertitel, unter „Hilfe → Über“ und bei `cycle-overlay --version`.

## 1.7.0

### Neu
- **Export mit Video**: Im Export-Tab unter Format → „Inhalt“ zwischen „Nur Anzeigen“ (transparente
  Ebene wie bisher) und „Video mit Anzeigen“ wählen. Dann werden die Anzeigen ins geladene Video
  eingebrannt, der Ton wird übernommen – ohne Transparenz, fertig zum Teilen.
  - Formate: MP4 H.264, MP4 H.265/HEVC (Qualität Standard/Hoch) und ProRes 422 HQ zum Weiterschneiden.
  - Encoder werden automatisch gewählt und vorher kurz getestet: zuerst Hardware (Apple VideoToolbox,
    NVIDIA NVENC, AMD AMF, Intel Quick Sync), sonst Software (x264/x265, Windows Media Foundation).
    Welcher benutzt wurde, steht nach dem Export in der Statuszeile.
  - Dateiname: `<Video>_mit_daten.mp4`; das Originalvideo wird nie überschrieben.
  - Gilt auch für den Stapel-Export und `cycle-overlay render --profile mp4_h264 --video …`.
  - Die Anzeigen werden mit BT.709 eingemischt (Standard bei Action-Cams in SDR). HDR-Videos
    (HLG/PQ, z. B. iPhone) werden noch nicht gesondert behandelt.
- Ohne geladenes Video ist nur „Nur Anzeigen“ wählbar; beide Formatwahlen bleiben getrennt gespeichert.

## 1.6.0

### Behoben
- Windows: „Unerwarteter Fehler: WinError 4551 – Eine Anwendungssteuerungsrichtlinie hat diese Datei
  blockiert“. Sperrt Smart App Control (oder WDAC/AppLocker) ffmpeg bzw. ffprobe, sucht CycleOverlay
  jetzt automatisch ein anderes startbares ffmpeg (eingestellter Pfad, System-PATH, winget, Scoop …)
  und meldet sonst verständlich, welche Datei blockiert ist und was hilft.
- Lässt sich nach dem Export nur ffprobe nicht starten, gilt der Export trotzdem als erfolgreich
  (die Prüfung der Datei wird übersprungen und im Log vermerkt).
- Ein unter Hilfe → „ffmpeg prüfen …“ ausgewähltes ffmpeg gilt jetzt auch für Export, Vorschau und
  Analyse (vorher nur für die Prüfung selbst).
- Build-Skript entfernt die Internet-Markierung der heruntergeladenen ffmpeg-Dateien.

### Neu
- Sprache der Overlay-Texte: Layout → „Sprache der Anzeige“ (Deutsch/English). Alle Standard-
  Beschriftungen (TEMPO/SPEED, PULS/HR …) und die Anstiegs-Vorschau folgen einheitlich der Auswahl;
  eigene Beschriftungen bleiben unverändert. Die Sprache wird im Projekt gespeichert, ein geladenes
  Preset ändert sie nicht. Standard ist Deutsch.
- Auflösung 2560×1440 (1440p QHD).
- Neue Exportformate: ProRes 4444 XQ, GoPro CineForm mit Alpha, QuickTime PNG, WebM VP9 mit Alpha,
  TIFF-Bildsequenz. Die Formatwahl ist jetzt eine Liste mit einem kurzen Hinweis zum gewählten Format.
- Sync-Zeitleiste zeigt Kanäle mit deutschem Namen.

## 1.5.0 – 22.11.2026

### Neu
- **Windows-Installer**: `packaging\windows\build.ps1` baut auf einem Windows-PC in einem Schritt
  `CycleOverlay-Setup-<Version>.exe` (PyInstaller + Inno Setup, ffmpeg mitgeliefert, Startmenü, Desktop-Symbol,
  Dateizuordnung `.cyov`, Update ersetzt die alte Version). Anleitung in `packaging/README.md`.
- **Programmsymbol** (Fenster, Installer, Projektdateien).
- Befehl **`selftest`**: prüft eine Installation ohne Fenster (Qt, Schriften, Presets, ffmpeg, Zeichnen).

## 1.4.0 – 22.11.2026

### Neu
- **Zahlen zählen durch** (Tab „Filter“ → Zahlenanzeige, Standard an): Zwischen zwei Aktualisierungen läuft
  die Zahl durch alle Zwischenwerte, statt zu springen – wirst du in 1 s um 10 km/h schneller, erscheint jede
  Zahl dazwischen. Der Takt „Zahl aktualisieren“ je Widget bleibt erhalten; „Springen“ stellt das alte Verhalten
  her. Gilt für Vorschau und Export, wird im Projekt gespeichert. Uhrzeit und Zeitdauern zählen nicht künstlich.

## 1.3.0 – 22.11.2026

### Neu
- **Tab „Filter“**: Glättung je Signal einstellbar – Geschwindigkeit, Leistung, Herzfrequenz, Trittfrequenz,
  Höhe, Steigung, Temperatur – jeweils **Aus / niedrig / mittel / stark** (Tooltip zeigt die genauen Fenster).
  Dazu „Werte in Pausen: auf 0 setzen / letzten Wert halten“ und „Alle auf Standard“. Wird im Projekt gespeichert;
  Standard entspricht dem bisherigen Verhalten.

### Behoben
- Sync-Zeitleiste: Knöpfe und Texte überlappten bei schmalen wie breiten Fenstern. Die Werkzeugleiste ist jetzt
  zweizeilig (Ansicht · Auto-Sync); lange Texte werden mit „…“ gekürzt (voller Text im Tooltip).
- „&“ in Abschnittstiteln und Knöpfen wurde von Qt als Tastenkürzel verschluckt („Kamera  Zeit“).
- Nach neuer Filterung springt die Ansicht der Sync-Zeitleiste nicht mehr zurück.

## 1.2.0 – 22.11.2026

### Neu
- **Auto-Sync über den Fahrtwind im Ton**: Das Windgeräusch im Mikrofon wird mit dem Tempo lauter und
  wird mit der Geschwindigkeit verglichen. Funktioniert auch bei stabilisierten Kameras (Insta360 FlowState),
  bei denen Bewegung und Drehung im Bild nichts über Tempo und Kurven verraten. Am echten Testclip
  (Hügeltour 26) liegt der beste Vorschlag jetzt 0,5 s neben der richtigen Stelle (vorher nicht gefunden).
- Auswahl **„Vergleich: Auto / Ton / Bild / Alle“** in der Sync-Zeitleiste; Auto = Ton, wenn vorhanden.
- Fahrtwind als **orange Kurve** in der Sync-Zeitleiste – zum Anlegen von Hand an die Geschwindigkeit.
- Bis zu **5 Vorschläge** statt 3; weitere Vorschläge heißen „Alternative“.

### Geändert
- Übereinstimmung in % nutzt dieselbe Signalwahl wie Auto-Sync.
- Videoanalyse wird einmal neu berechnet (enthält jetzt den Ton).

## 1.1.0 – 22.11.2026

### Neu
- **Sync-Zeitleiste** im Sync-Tab: Fahrt und Clip wie im Schnittprogramm, Clip ziehen (Shift = fein),
  ←/→ 1 Frame, ↑/↓ 1 s (Shift ×10), Zoom bis auf Frames, Übersicht, Rückgängig, Sync-Punkt, „Clip hierher“.
- **Videoanalyse**: Bewegungskurve, Drehung (Kurven) und Filmstreifen in einem Durchlauf, zwischengespeichert.
- **Auto-Sync**: bis zu 3 Vorschläge aus Tempo und Kurven, Video wird probeweise daraufgelegt
  (◀/▶, Klick auf den Rahmen, Behalten/Verwerfen); Stellen mit zu viel Stillstand scheiden aus.
- **Übereinstimmung in %** zwischen Video und Fahrt, live beim Verschieben.
- **Zeitbezug** (Automatisch / Aufnahmezeit / Mit Track): falsch gestellte Kamera-Uhren blockieren nicht mehr.
- **Versatz** auch als H:MM:SS(.ms), Bereich ±10 Jahre.
- **Rechtes Menü neu**: aufklappbare Abschnitte mit Kurzinfo, Knopfreihen für ≤ 4 Möglichkeiten,
  3×3-Ankerraster, Regler; Sync- und Export-Tab gegliedert.
- **Minimap**: Routen-Sichtweite (Vielfaches der Sichtweite), Route wird zum Rand hin weich ausgeblendet.
- **Kartendaten**: eine Abfrage für die ganze Strecke, automatisches Nachladen bei Lücken,
  langsame Server werden übersprungen, Diagnose `cycle-overlay mapdata --dump`.
- **Sync-Diagnose** (Hilfe-Menü bzw. `cycle-overlay syncdiag`) zum Auswerten echter Clips.

### Behoben (Gesamtprüfung)
- Menübefehle mit optionalen Parametern bekamen von Qt `False` übergeben – „Sync-Diagnose speichern“
  schlug deshalb fehl. Alle Menübefehle werden jetzt ohne Argument aufgerufen.
- Unerwartete Fehler werden protokolliert und als Meldung angezeigt statt still zu verschwinden.
- Hintergrundaufgaben (Laden, Kartendownload, Videobilder) laufen in Daemon-Threads: Beenden der App
  während eines Downloads führt nicht mehr zum Absturz; Ergebnisse kommen sicher im GUI-Thread an.
- Kartendownload: Ergebnis landet nicht mehr in einem inzwischen geöffneten anderen Projekt; Knopf bleibt
  während des Downloads gesperrt; neues/anderes Projekt bricht den Download ab.
- „Projekt öffnen“: Schlägt das Öffnen fehl, bleibt das bisherige Projekt samt Kartendaten erhalten.
- Projekt speichern / Layout / Preset: Schreibfehler werden gemeldet statt zu einem Absturz zu führen.
- Ungültige Zeitzone wird abgelehnt (vorher Fehler in Vorschau und Zeitanzeige); fehlerhafte Zeitzonen in
  Projektdateien werden ignoriert. Sync-Einstellungen werden nur ganz oder gar nicht übernommen.
- Unbekannte Zeitzone wurde bei „Automatisch“ still übergangen – wird jetzt gemeldet.
- Dateinamen mit Punkt (z. B. `Tour.2026_overlay`) wurden beim Export abgeschnitten.
- PNG-Sequenz: Bilder eines früheren Exports in denselben Ordner werden vorher entfernt.
- HEVC-Export: ungerade Bildmaße werden mit klarer Meldung abgelehnt.
- Variable Bildrate (Handys): statt eines Zeitbasis-Werts (z. B. 90000 FPS) wird die mittlere Bildrate genutzt.
- Zeitanzeige bei negativen Track-Zeiten (Video vor Trackbeginn) war falsch gerundet.
- Videobilder eines gerade gewechselten Videos werden verworfen.
- Sichtbarkeit in der Widget-Liste wird im Inspector nachgeführt.
- Export-Panel: Attribut `size` überdeckte die Qt-Methode `size()` (umbenannt).
- Puls/Trittfrequenz-Glättung als 3 s angegeben (wirkte bereits so).

## 1.0.0 – 22.11.2026

Erste vollständige Version: GPX/FIT-Import, Verarbeitung (Glättung, Ausreißer, Pausen), Sync über
Kamera-Profile, Widgets (Wert, Rundinstrument, Balken, Streckenübersicht, Profil, Minimap mit OSM,
Anstiegs-/Abfahrts-Vorschau, Steigungsfarben, 3D-Höhenband), Export ProRes 4444 / QuickTime Animation /
PNG-Folge / HEVC mit Alpha, Stapel-Export, Projektpaket `.cyov`, Kommandozeile.
