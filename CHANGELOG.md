# Änderungen

Versionen nach dem Schema **MAJOR.MINOR.PATCH**: PATCH = Fehlerbehebungen, MINOR = neue
Funktionen, MAJOR = inkompatible Änderungen (z. B. am Projektformat). Die laufende Version
steht im Fenstertitel, unter „Hilfe → Über“ und bei `cycle-overlay --version`.

## Unveröffentlicht

### Neu
- **Installer für macOS und Linux**: macOS-App als Disk-Image (`.dmg`, Apple Silicon), Linux als
  Debian/Ubuntu-Paket (`.deb`, mit Menüeintrag und ffmpeg als Abhängigkeit) und als `.tar.gz`.
  GitHub Actions baut sie zusammen mit dem Windows-Installer bei jedem Release; die Update-Prüfung
  wählt automatisch das passende Paket.
- **Vorschau-Qualität** (oberhalb der Vorschau, wie die Wiedergabeauflösung in Premiere):
  Automatisch (so scharf wie das Vorschaufenster, auf Retina doppelt), Voll, 1/2, 1/4. Daneben
  steht die aktuelle Auflösung des Videobilds. Die Einstellung wird gespeichert.
- Anzeigen in der Vorschau werden auf Retina-Displays in voller Auflösung gezeichnet und das
  Videobild wird beim Skalieren geglättet (vorher unscharf/pixelig).
- macOS: Doppelklick auf eine `.cyov`-Datei öffnet sie in CycleOverlay.

### Behoben
- **Überlappende Texte** in den Anzeigen, systematisch geprüft (jede Vorlage in mehreren Größen,
  allen Schriftstilen, mit extremen Schrifteinstellungen, Englisch/imperial):
  - Tacho mit Nadel: Zahl, Einheit und Beschriftung lagen übereinander und auf den Skalenzahlen.
    Der Text in der Mitte wird jetzt als Block gesetzt und verkleinert, bis er nichts berührt;
    beim geschlossenen Kreis (360°) fallen die erste und letzte Skalenzahl nicht mehr aufeinander.
  - Kursive Schrift: Die letzte Ziffer ragte in die Einheit.
  - Balken, Höhenprofil und Scope: Beschriftung, Zahl und Einheit werden bei Platzmangel gemeinsam
    kleiner statt sich zu überlappen oder über den Rand zu laufen.
  - Wert + Beschriftung: lange Beschriftung (z. B. GESAMTZEIT) in schmalen Widgets ragte heraus.
  - Anstiegs-Vorschau: Details liefen über den rechten Rand.
  - Minimap: Kompass, Maßstab und Quellenangabe weichen sich aus; „N“ wird am Bildrand nicht mehr
    abgeschnitten; die Quellenangabe passt in die runde Karte. Texte der Minimap folgen der
    eingestellten Schriftbreite.
- Widget-Vorschau zeigte an Pausenstellen „0.0“ – sie nimmt jetzt eine Stelle, an der man fährt.
- Export-Bereich: Die Felder für In/Out schnitten die Zeit ab (macOS).
- Fahrtinfo zeigte „2463 m hm“ – jetzt „▲ 2463 m“.
- Fenstertitel: Ungespeicherte Änderungen werden wie bei macOS üblich angezeigt.
- Neuer Test `test_text_layout.py` prüft alle Vorlagen automatisch auf Text-Kollisionen.

## 1.11.0

### Neu
- **Vorschau in „Widget hinzufügen“**: Zu jeder Vorlage erscheint rechts ein Vorschaubild mit echten
  Werten deiner Fahrt – an der aktuellen Stelle (am Anfang stattdessen mitten in der Fahrt) und, wenn
  ein Video geladen ist, über dem Videobild. Ein anderes Signal gewählt -> die Vorschau zeigt es sofort.

## 1.10.1

### Behoben
- Test zur Schriftbreite: Das Widget war zu schmal, die Zahl wurde deshalb automatisch verkleinert
  und der Test schlug fehl, obwohl die Breite richtig wirkte. Der Test nutzt jetzt ein breites Widget.

## 1.10.0

### Neu
- **„Widget hinzufügen“ mit Vorlagen**: links nach Gruppen (Werte, Instrumente, Strecke und Höhe),
  rechts Beschreibung und nur die passenden Signale. Doppelklick fügt sofort hinzu; Vorlagen ohne
  passendes Signal in der Fahrt sind ausgegraut.
- Neue Gauges (Auswahl aus der Telemetry-Overlay-Liste):
  - **Tacho mit Nadel** – spitzer Zeiger (Rundinstrument → Zeiger „spitz“) und Zahlen an der Skala.
  - **Route mit Richtungspfeil** – Streckenübersicht mit Navi-Pfeil statt Punkt.
  - **Distanz mit Fortschritt** – „84,2 / 222 km“ oder „noch 138 km“ mit Fortschrittsbalken
    (auch für Höhenmeter und Zeiten).
  - **Höhenmeter hoch und runter** – ▲ Anstieg und ▼ Abstieg in einem Widget (neues Signal
    „Höhenmeter abwärts“).
  - **Scope** – laufender Graph der letzten Sekunden (Zeitspanne einstellbar).
  - **Zonen-Balken** – jede Zone in ihrer Farbe („alle Abschnitte“); Knopf „5 Zonen verteilen“.
  - **Marker im Bereich** – eine Marke wandert zwischen Min und Max, Zonen als farbige Skala.
  - **Mini mit Symbol** – Symbol (Herz, Blitz, Tacho, Kurbel, Berg …) vor dem Wert.
  - **Profil-Ecke** – randloses Profil, Wert neben der Marke, wahlweise zusätzlich in m und ft.
  - **Freie Routenlinie** – nur weiße Linie mit Punkt, ohne Kasten.
- Alle Optionen auch einzeln im Widget-Tab: Zeigerform, Skalenzahlen, Balkenform, Zonen-Darstellung,
  Symbol, Positionsmarke, Wert an der Marke, zweite Einheit, Fortschrittsanzeige, Scope-Zeitspanne.

## 1.9.0

### Neu
- **Schrift-Abschnitt** im Widget-Tab (eigener Abschnitt „Schrift“, getrennt von „Farben & Effekte“):
  - **Schriftstile zum Anklicken** – acht Kacheln, jede zeigt „43 km/h“ in ihrer Schrift:
    Klassisch, Schmal, Lang und dünn, Plakat, Klein und fett, Rund, Technisch, Rennen kursiv.
  - Feineinstellung: Schriftart, Stärke der Zahl (dünn/normal/fett/extra fett), Stärke von
    Beschriftung und Einheit, Breite (60–140 %), Zeichenabstand, kursiv, Größe der Beschriftung und
    der Einheit (50–200 %), Beschriftung in Großbuchstaben.
  - „Schrift auf alle Widgets übertragen“ (mit Rückfrage).
- **Sechs mitgelieferte Schriften** (SIL Open Font License), die auf Mac und Windows gleich aussehen:
  Inter, Roboto Condensed, Anton, Fira Sans, Montserrat, Archivo Black. Sie stehen in der
  Schriftauswahl oben, darunter alle Schriften des Systems.
- **Farben & Effekte**: Schattenabstand, Konturstärke, Eckenrundung der Kachel (eckig bis Pille);
  „Farben und Effekte auf alle übertragen“.
- GitHub-Release-Ablauf auf aktuelle Action-Versionen (Node.js 24) umgestellt.

## 1.8.0

### Neu
- **Updates aus dem Internet**: Die App prüft beim Start (höchstens einmal am Tag) auf
  https://github.com/ncakeljic/CycleOverlay/releases, ob es eine neuere Version gibt, und zeigt
  dann die Änderungen. Unter Windows lädt „Herunterladen und installieren“ den neuen Installer,
  beendet CycleOverlay (vorher wird nach dem Speichern gefragt), aktualisiert und startet die App
  wieder. Projekte und Einstellungen bleiben erhalten.
  - Von Hand: Hilfe → „Nach Updates suchen …“.
  - „Diese Version überspringen“ und „Beim Start automatisch nach Updates suchen“ im Hinweisfenster.
  - Aus dem Quellcode gestartet (macOS mit uv): nur Hinweis und Link, Aktualisieren per `git pull`.
- **Automatische Releases**: Ein Git-Tag `vX.Y.Z` startet auf GitHub den Bau des Windows-Installers
  und veröffentlicht ein Release mit dem passenden CHANGELOG-Abschnitt (`.github/workflows/release.yml`).
- `.gitignore` für das Repository (ohne virtuelle Umgebung, Build-Ergebnisse und mitgeliefertes ffmpeg).

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
