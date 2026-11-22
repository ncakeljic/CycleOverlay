"""Layout- und Widget-Konfiguration (ohne Qt, als JSON speicherbar).

Positionen sind auflösungsunabhängig (PRD 5.1):
- ``anchor``: Bezugspunkt im Bild (9 Positionen) – das Widget wird mit seinem
  gleichnamigen Punkt daran ausgerichtet.
- ``x``/``y``: Abstand vom Bezugspunkt nach innen, als Anteil der Bildbreite bzw. -höhe.
- ``size``: Widget-Höhe als Anteil der Bildhöhe; die Breite ergibt sich aus dem Seitenverhältnis.
Alle Strich- und Schriftgrößen werden relativ zur Widget-Höhe gezeichnet.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, fields
from os import PathLike
from pathlib import Path

ANCHORS: dict[str, tuple[float, float]] = {
    "top_left": (0.0, 0.0),
    "top_center": (0.5, 0.0),
    "top_right": (1.0, 0.0),
    "center_left": (0.0, 0.5),
    "center": (0.5, 0.5),
    "center_right": (1.0, 0.5),
    "bottom_left": (0.0, 1.0),
    "bottom_center": (0.5, 1.0),
    "bottom_right": (1.0, 1.0),
}

WIDGET_TYPES = ("text", "arc", "bar", "map", "profile", "minimap", "climb", "progress", "gainloss", "scope")
#: Signale, für die eine Fortschrittsanzeige (gefahren / gesamt) sinnvoll ist – kumulierte Größen
PROGRESS_CHANNELS = ("distance", "elevation_gain", "elevation_loss", "elapsed_time", "moving_time")
#: Signale, die nicht als laufender Graph (Scope) taugen
NO_SCOPE_CHANNELS = ("time_of_day", "position")
POSITION_TYPES = ("map", "minimap")
SCOPES = ("full", "clip")
MAP_CHANNEL = "position"
LABEL_POSITIONS = ("top", "bottom", "left", "none")

_HEX = re.compile(r"^#([0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def parse_color(value: str) -> tuple[int, int, int, int]:
    """'#RRGGBB' oder '#RRGGBBAA' -> (r, g, b, a)."""
    if not isinstance(value, str) or not _HEX.match(value):
        raise ValueError(f"Ungültige Farbe: {value!r} (erwartet #RRGGBB oder #RRGGBBAA)")
    h = value[1:]
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    a = int(h[6:8], 16) if len(h) == 8 else 255
    return r, g, b, a


@dataclass
class Style:
    font_family: str = "Inter"
    text_color: str = "#FFFFFF"
    label_color: str = "#FFFFFFB8"
    accent_color: str = "#FF6A1F"
    track_color: str = "#FFFFFF38"
    badge: bool = False
    badge_color: str = "#00000080"
    shadow: bool = True
    shadow_color: str = "#0000009A"
    outline: bool = False
    outline_color: str = "#000000C0"
    # --- Schrift (siehe rendering.typography)
    value_weight: str = "bold"  # Strichstärke der Zahl
    label_weight: str = "semibold"  # Strichstärke von Beschriftung und Einheit
    font_width: float = 1.0  # horizontale Streckung: < 1 schmaler, > 1 breiter
    tracking: float = 0.0  # Laufweite (Abstand zwischen Zeichen) in % der Schriftgröße
    italic: bool = False
    label_scale: float = 1.0  # Größe der Beschriftung relativ zum Standard
    unit_scale: float = 1.0  # Größe der Einheit relativ zum Standard
    label_uppercase: bool = False  # Beschriftung in Großbuchstaben
    # --- Effekte
    shadow_distance: float = 1.0  # Schattenabstand relativ zum Standard (0 = direkt dahinter, weich)
    outline_width: float = 1.0  # Konturstärke relativ zum Standard
    badge_radius: float = 0.14  # Eckenrundung der Kachel (Anteil der Höhe, 0 = eckig, 0.5 = Pille)

    def validate(self) -> None:
        from .typography import TEXT_SCALE_RANGE, TRACKING_RANGE, WEIGHTS, WIDTH_RANGE

        for f in fields(self):
            if f.name.endswith("_color"):
                parse_color(getattr(self, f.name))
        for key in ("value_weight", "label_weight"):
            if getattr(self, key) not in WEIGHTS:
                raise ValueError(f"Unbekannte Schriftstärke: {getattr(self, key)!r}")
        checks = (("font_width", WIDTH_RANGE, "Schriftbreite"), ("tracking", TRACKING_RANGE, "Laufweite"),
                  ("label_scale", TEXT_SCALE_RANGE, "Größe der Beschriftung"),
                  ("unit_scale", TEXT_SCALE_RANGE, "Größe der Einheit"),
                  ("shadow_distance", (0.0, 4.0), "Schattenabstand"), ("outline_width", (0.2, 4.0), "Konturstärke"),
                  ("badge_radius", (0.0, 0.5), "Eckenrundung"))
        for key, (lo, hi), title in checks:
            v = float(getattr(self, key))
            if not lo - 1e-9 <= v <= hi + 1e-9:
                raise ValueError(f"{title} muss zwischen {lo:g} und {hi:g} liegen")


@dataclass
class Zone:
    """Ab ``from_value`` (in Anzeige-Einheiten) wird ``color`` verwendet."""

    from_value: float
    color: str


_DEFAULT_ASPECT = {"text": 2.4, "arc": 1.0, "bar_horizontal": 4.0, "bar_vertical": 0.32, "map": 1.0, "profile": 3.6,
                   "minimap": 1.0, "climb": 2.8, "progress": 4.2, "gainloss": 2.4, "scope": 2.6}

DEFAULT_GRADE_STEPS = [-2.0, 2.0, 5.0, 8.0, 12.0]
DEFAULT_GRADE_COLORS = ["#3D8BFF", "#3DDC84", "#FFD43B", "#FF8C1A", "#FF3B3B", "#B0003A"]

#: Farbschemata für die Steigung: (Titel, Schwellen, Farben)
GRADE_SCHEMES: dict[str, tuple[str, list[float], list[str]]] = {
    "climb": ("Anstiege abgestuft, Abfahrt einfarbig", DEFAULT_GRADE_STEPS, DEFAULT_GRADE_COLORS),
    "both": ("Anstiege und Abfahrten abgestuft",
             [-12.0, -8.0, -5.0, -2.0, 2.0, 5.0, 8.0, 12.0],
             ["#8A3FFC", "#1F4FD8", "#3D8BFF", "#8CCBFF", "#3DDC84", "#FFD43B", "#FF8C1A", "#FF3B3B", "#B0003A"]),
}


def grade_scheme_of(steps: list[float], colors: list[str]) -> str | None:
    for key, (_, st, co) in GRADE_SCHEMES.items():
        if list(steps) == list(st) and [c.upper() for c in colors] == [c.upper() for c in co]:
            return key
    return None


@dataclass
class ClimbStyle:
    """Vorschau auf kommende Anstiege (Typ ``climb``)."""

    lookahead_m: float = 3000.0  # gezeigter Bereich vor dir
    behind_m: float = 300.0  # gezeigter Bereich hinter dir
    min_length_m: float = 500.0
    min_gain_m: float = 20.0
    min_grade: float = 3.0
    mode: str = "climbs"  # "climbs" | "descents" | "both"
    colored: bool = True  # Profil nach Steigung einfärben
    show_info: bool = True  # Infozeile (Länge, Höhenmeter, Ø-Steigung)
    hide_when_none: bool = False  # ausblenden, wenn kein Anstieg in Sicht

    def validate(self) -> None:
        if not 300 <= self.lookahead_m <= 50000:
            raise ValueError("Anstiegs-Vorschau: Bereich muss zwischen 300 m und 50 km liegen")
        if not 0 <= self.behind_m <= 5000:
            raise ValueError("Anstiegs-Vorschau: Bereich hinter dir muss zwischen 0 und 5 km liegen")
        if self.min_length_m < 50 or self.min_gain_m < 1 or self.min_grade <= 0:
            raise ValueError("Anstiegs-Vorschau: ungültige Mindestwerte")
        if self.mode not in ("climbs", "descents", "both"):
            raise ValueError(f"Anstiegs-Vorschau: unbekannter Modus {self.mode!r}")


@dataclass
class MinimapStyle:
    """Einstellungen der Navigations-Minimap."""

    orientation: str = "heading"  # "heading" = Fahrtrichtung oben, "north" = Norden oben
    view: str = "top"  # "top" = von oben, "tilt" = 3D-gekippt
    tilt_deg: float = 55.0
    background: str = "osm"  # "osm" = Straßen (falls geladen), "route" = nur Route
    theme: str = "dark"  # "dark" | "light"
    shape: str = "circle"  # "circle" | "rounded"
    radius_m: float = 400.0  # Sichtweite (Pfeil bis Rand)
    #: Route bis zu diesem Vielfachen der Sichtweite zeichnen (über den Kartenrand hinaus), damit
    #: Kurven, die kurz aus dem Bild laufen (Kehren), nicht als Lücke abbrechen
    route_range: float = 2.0
    auto_zoom: bool = True  # bei höherem Tempo weiter herauszoomen
    arrow: str = "chevron"  # "chevron" | "triangle" | "dot"
    arrow_size: float = 0.11  # relativ zur Widget-Höhe
    arrow_color: str | None = None  # None = Farbe des Themas
    route_color: str | None = None  # None = Farbe des Themas
    show_route: bool = True
    show_compass: bool = True
    show_scale: bool = False
    show_attribution: bool = True
    heading_window_m: float = 25.0
    route_coloring: str = "solid"  # "solid" = eine Farbe, "gradient" = nach Steigung
    grade_badge: bool = False  # aktuelle Steigung neben dem Pfeil
    elevation_ribbon: bool = False  # Route als 3D-Höhenband (nur gekippte Ansicht)
    ribbon_exaggeration: float = 4.0  # Überhöhung des Höhenbands
    ribbon_curtain: bool = True  # halbtransparenter „Vorhang“ unter dem Band
    #: Höhenbezug des Bands: "position" = eigene Höhe (Abfahrt taucht unter die Karte),
    #: "lowest" = tiefster Punkt im Umkreis (alles über dem Boden, Abfahrt als Rampe ins Tal)
    ribbon_reference: str = "position"

    def validate(self) -> None:
        checks = {"orientation": ("heading", "north"), "view": ("top", "tilt"), "background": ("osm", "route"),
                  "route_coloring": ("solid", "gradient"), "ribbon_reference": ("position", "lowest"),
                  "theme": ("dark", "light"), "shape": ("circle", "rounded"), "arrow": ("chevron", "triangle", "dot")}
        for key, allowed in checks.items():
            if getattr(self, key) not in allowed:
                raise ValueError(f"Minimap: ungültiger Wert für {key}: {getattr(self, key)!r}")
        if not 0 <= self.tilt_deg <= 75:
            raise ValueError("Minimap: Neigung muss zwischen 0 und 75° liegen")
        if not 20 <= self.radius_m <= 20000:
            raise ValueError("Minimap: Sichtweite muss zwischen 20 m und 20 km liegen")
        if not 1.0 <= self.route_range <= 10:
            raise ValueError("Minimap: Routen-Sichtweite muss zwischen 1× und 10× liegen")
        if not 0.5 <= self.ribbon_exaggeration <= 30:
            raise ValueError("Minimap: Überhöhung muss zwischen 0,5 und 30 liegen")
        for c in (self.arrow_color, self.route_color):
            if c is not None:
                parse_color(c)


@dataclass
class WidgetConfig:
    id: str
    type: str  # text | arc | bar
    channel: str  # Kanal aus units.CHANNELS oder "time_of_day"
    anchor: str = "bottom_left"
    x: float = 0.04
    y: float = 0.06
    size: float = 0.1
    visible: bool = True
    label: str | None = None  # None = Standardbeschriftung des Kanals
    label_position: str = "top"
    show_unit: bool = True
    decimals: int | None = None
    #: Aktualisierungsrate der Zahl in Hz (0 = jedes Frame); Bogen/Balken laufen immer flüssig
    update_rate_hz: float = 4.0
    min_value: float | None = None  # Anzeige-Einheiten; None = Standard des Kanals
    max_value: float | None = None
    zones: list[Zone] = field(default_factory=list)
    #: Zonen auch für die Textfarbe verwenden
    zones_color_text: bool = False
    # Rundinstrument
    sweep_deg: float = 270.0
    show_needle: bool = False
    show_ticks: bool = True
    needle_style: str = "line"  # "line" = Strich · "pointer" = spitzer Zeiger
    show_tick_labels: bool = False  # Zahlen an der Skala
    # Balken
    orientation: str = "horizontal"
    bar_style: str = "fill"  # "fill" = Füllbalken · "marker" = Marke wandert zwischen Min und Max
    zone_fill: str = "solid"  # "solid" = ganze Füllung in Zonenfarbe · "segments" = jede Zone in ihrer Farbe
    # Wert + Beschriftung
    show_icon: bool = False  # Symbol vor dem Wert (Herz, Blitz …)
    # Streckenübersicht
    position_marker: str = "dot"  # "dot" = Punkt · "arrow" = Pfeil in Fahrtrichtung
    # Profil
    value_at_marker: bool = False  # Wert neben der Positionsmarke statt oben
    alt_unit: bool = False  # zusätzlich in der anderen Einheit (m und ft, km/h und mph)
    # Fortschritt
    progress_mode: str = "ridden_total"  # "ridden_total" = 84 / 222 km · "remaining" = noch 138 km
    # Scope
    scope_window_s: float = 30.0  # gezeigte Zeitspanne vor dem aktuellen Moment
    #: Strichstärke von Bogen/Balken relativ zur Widget-Höhe
    stroke: float = 0.085
    # Streckenkarte / Profil
    scope: str = "full"  # "full" = ganze Fahrt, "clip" = Umgebung des Clips
    show_ridden: bool = True  # bereits gefahrenen Teil hervorheben
    minimap: MinimapStyle = field(default_factory=MinimapStyle)
    climb: ClimbStyle = field(default_factory=ClimbStyle)
    #: Farbklassen der Steigung (Schwellen in %, eine Farbe mehr als Schwellen)
    grade_steps: list[float] = field(default_factory=lambda: list(DEFAULT_GRADE_STEPS))
    grade_colors: list[str] = field(default_factory=lambda: list(DEFAULT_GRADE_COLORS))
    aspect: float | None = None
    style: Style = field(default_factory=Style)

    # ---------------------------------------------------------------- Geometrie

    def effective_aspect(self) -> float:
        if self.aspect:
            return float(self.aspect)
        if self.type == "bar":
            return _DEFAULT_ASPECT["bar_vertical" if self.orientation == "vertical" else "bar_horizontal"]
        return _DEFAULT_ASPECT[self.type]

    def rect(self, frame_w: float, frame_h: float) -> tuple[float, float, float, float]:
        """Pixel-Rechteck (links, oben, Breite, Höhe) für eine Bildgröße."""
        ax, ay = ANCHORS[self.anchor]
        h = self.size * frame_h
        w = h * self.effective_aspect()
        px, py = ax * frame_w, ay * frame_h
        dx = self.x * frame_w * (-1.0 if ax == 1.0 else 1.0)
        dy = self.y * frame_h * (-1.0 if ay == 1.0 else 1.0)
        return px + dx - ax * w, py + dy - ay * h, w, h

    def move_to(self, left: float, top: float, frame_w: float, frame_h: float) -> None:
        """Position aus Pixelkoordinaten setzen (für Drag-and-Drop in der GUI)."""
        ax, ay = ANCHORS[self.anchor]
        h = self.size * frame_h
        w = h * self.effective_aspect()
        dx = left + ax * w - ax * frame_w
        dy = top + ay * h - ay * frame_h
        self.x = dx / frame_w * (-1.0 if ax == 1.0 else 1.0)
        self.y = dy / frame_h * (-1.0 if ay == 1.0 else 1.0)

    @property
    def horizontal_align(self) -> str:
        ax = ANCHORS[self.anchor][0]
        return "left" if ax == 0.0 else "right" if ax == 1.0 else "center"

    # ---------------------------------------------------------------- Prüfung & JSON

    def validate(self) -> None:
        if self.type not in WIDGET_TYPES:
            raise ValueError(f"{self.id}: unbekannter Widget-Typ {self.type!r}")
        if self.anchor not in ANCHORS:
            raise ValueError(f"{self.id}: unbekannter Ankerpunkt {self.anchor!r}")
        if self.label_position not in LABEL_POSITIONS:
            raise ValueError(f"{self.id}: unbekannte Label-Position {self.label_position!r}")
        if self.scope not in SCOPES:
            raise ValueError(f"{self.id}: unbekannter Bereich {self.scope!r}")
        if (self.type in POSITION_TYPES) != (self.channel == MAP_CHANNEL):
            raise ValueError(f"{self.id}: Karte/Minimap gehören zum Signal 'position' (und nur sie)")
        self.minimap.validate()
        self.climb.validate()
        if len(self.grade_colors) != len(self.grade_steps) + 1:
            raise ValueError(f"{self.id}: es braucht genau eine Steigungsfarbe mehr als Schwellen")
        if list(self.grade_steps) != sorted(self.grade_steps):
            raise ValueError(f"{self.id}: Steigungsschwellen müssen aufsteigend sein")
        for c in self.grade_colors:
            parse_color(c)
        if self.type == "climb" and self.channel != "elevation":
            raise ValueError(f"{self.id}: die Anstiegs-Vorschau gehört zum Signal 'elevation'")
        if self.orientation not in ("horizontal", "vertical"):
            raise ValueError(f"{self.id}: unbekannte Ausrichtung {self.orientation!r}")
        choices = {"needle_style": ("line", "pointer"), "bar_style": ("fill", "marker"),
                   "zone_fill": ("solid", "segments"), "position_marker": ("dot", "arrow"),
                   "progress_mode": ("ridden_total", "remaining")}
        for key, allowed in choices.items():
            if getattr(self, key) not in allowed:
                raise ValueError(f"{self.id}: ungültiger Wert für {key}: {getattr(self, key)!r}")
        if self.type == "progress" and self.channel not in PROGRESS_CHANNELS:
            raise ValueError(f"{self.id}: Fortschritt gibt es nur für Distanz, Höhenmeter und Zeiten")
        if self.type == "gainloss" and self.channel != "elevation_gain":
            raise ValueError(f"{self.id}: „Höhenmeter hoch/runter“ gehört zum Signal 'elevation_gain'")
        if self.type == "scope" and self.channel in NO_SCOPE_CHANNELS:
            raise ValueError(f"{self.id}: Uhrzeit und Position lassen sich nicht als Verlauf zeigen")
        if not 3 <= self.scope_window_s <= 600:
            raise ValueError(f"{self.id}: Zeitspanne des Verlaufs muss zwischen 3 s und 10 min liegen")
        if not 0 < self.size <= 1:
            raise ValueError(f"{self.id}: size muss zwischen 0 und 1 liegen")
        if self.min_value is not None and self.max_value is not None and self.max_value <= self.min_value:
            raise ValueError(f"{self.id}: max_value muss größer als min_value sein")
        for z in self.zones:
            parse_color(z.color)
        self.style.validate()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "WidgetConfig":
        data = dict(data)
        known = {f.name for f in fields(cls)}
        style = Style(**{k: v for k, v in (data.pop("style", None) or {}).items() if k in {f.name for f in fields(Style)}})
        zones = [Zone(**z) for z in data.pop("zones", None) or []]
        mm_known = {f.name for f in fields(MinimapStyle)}
        minimap = MinimapStyle(**{k: v for k, v in (data.pop("minimap", None) or {}).items() if k in mm_known})
        cl_known = {f.name for f in fields(ClimbStyle)}
        climb = ClimbStyle(**{k: v for k, v in (data.pop("climb", None) or {}).items() if k in cl_known})
        w = cls(**{k: v for k, v in data.items() if k in known}, style=style, zones=zones, minimap=minimap,
                climb=climb)
        w.validate()
        return w


@dataclass
class Layout:
    name: str
    widgets: list[WidgetConfig] = field(default_factory=list)
    version: int = 1
    #: Sprache der Overlay-Texte ("de"/"en", siehe ``rendering.i18n``)
    language: str = "de"

    def to_dict(self) -> dict:
        return {"name": self.name, "version": self.version, "language": self.language,
                "widgets": [w.to_dict() for w in self.widgets]}

    @classmethod
    def from_dict(cls, data: dict) -> "Layout":
        if not isinstance(data, dict) or "widgets" not in data:
            raise ValueError("Ungültiges Layout: 'widgets' fehlt")
        from .i18n import normalize_language

        layout = cls(name=str(data.get("name", "Layout")), version=int(data.get("version", 1)),
                     widgets=[WidgetConfig.from_dict(w) for w in data["widgets"]],
                     language=normalize_language(data.get("language")))
        ids = [w.id for w in layout.widgets]
        if len(ids) != len(set(ids)):
            raise ValueError("Ungültiges Layout: Widget-IDs sind nicht eindeutig")
        return layout

    def save(self, path: str | PathLike) -> Path:
        path = Path(path)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | PathLike) -> "Layout":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"Layout {Path(path).name} ist kein gültiges JSON: {exc}") from exc
        except OSError as exc:
            raise ValueError(f"Layout {Path(path).name} nicht lesbar: {exc}") from exc
        try:
            return cls.from_dict(data)
        except (TypeError, KeyError, AttributeError) as exc:
            raise ValueError(f"Layout {Path(path).name} hat ein unbekanntes Format: {exc}") from exc
