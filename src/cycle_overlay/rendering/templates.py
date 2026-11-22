"""Vorlagen für „Widget hinzufügen“ – ohne Qt.

Eine Vorlage ist ein Widget-Typ plus fertige Einstellungen (z. B. „Tacho mit Nadel“ = Rundinstrument
mit spitzem Zeiger und Skalenzahlen). So findet man jede Darstellung direkt, statt sie aus Optionen
zusammenzubauen; danach lässt sich alles im Widget-Tab weiter anpassen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .layout import NO_SCOPE_CHANNELS, PROGRESS_CHANNELS


@dataclass(frozen=True)
class WidgetTemplate:
    key: str
    title: str
    group: str
    description: str
    wtype: str
    #: erlaubte Signale (None = alle Zahlen-Signale); das erste verfügbare ist der Vorschlag
    channels: tuple[str, ...] | None = None
    default_channel: str | None = None
    cfg: dict[str, Any] = field(default_factory=dict)
    style: dict[str, Any] = field(default_factory=dict)
    size: float | None = None
    #: Farbzonen gleichmäßig über den Bereich verteilen (für den Zonen-Balken)
    auto_zones: bool = False

    def allows(self, channel: str) -> bool:
        if self.channels is not None:
            return channel in self.channels
        if self.wtype == "text":
            return channel != "position"
        return channel not in NO_SCOPE_CHANNELS


VALUE = "Werte"
INSTRUMENTS = "Instrumente"
ROUTE = "Strecke und Höhe"

#: Zonenfarben von ruhig nach anstrengend
ZONE_COLORS = ["#3D8BFF", "#3DDC84", "#FFD43B", "#FF8C1A", "#FF3B3B"]

TEMPLATES: dict[str, WidgetTemplate] = {t.key: t for t in [
    WidgetTemplate("text", "Wert + Beschriftung", VALUE, "Große Zahl mit Beschriftung und Einheit.", "text",
                   default_channel="speed"),
    WidgetTemplate("mini", "Mini mit Symbol", VALUE, "Kompakt: Symbol (Herz, Blitz …) und Wert, ohne Beschriftung.",
                   "text", default_channel="heart_rate", cfg={"show_icon": True, "label_position": "none"},
                   size=0.06),
    WidgetTemplate("gainloss", "Höhenmeter hoch und runter", VALUE, "▲ Anstieg und ▼ Abstieg zusammen.",
                   "gainloss", channels=("elevation_gain",), cfg={"label_position": "none"}, size=0.11),
    WidgetTemplate("progress", "Distanz mit Fortschritt", VALUE,
                   "Gefahren / Gesamt mit Fortschrittsbalken – wahlweise „noch … km“.", "progress",
                   channels=PROGRESS_CHANNELS, default_channel="distance", size=0.075),
    WidgetTemplate("arc", "Rundinstrument", INSTRUMENTS, "Bogen, der sich mit dem Wert füllt.", "arc",
                   default_channel="speed"),
    WidgetTemplate("needle", "Tacho mit Nadel", INSTRUMENTS, "Klassischer Tacho: spitzer Zeiger und Skalenzahlen.",
                   "arc", default_channel="speed",
                   cfg={"show_needle": True, "needle_style": "pointer", "show_tick_labels": True, "sweep_deg": 240.0}),
    WidgetTemplate("bar", "Balken", INSTRUMENTS, "Füllbalken mit Zahl.", "bar", default_channel="power"),
    WidgetTemplate("zone_bar", "Zonen-Balken", INSTRUMENTS,
                   "Balken in Zonenfarben (blau → rot), z. B. für Puls oder Leistung.", "bar",
                   default_channel="heart_rate", cfg={"zone_fill": "segments"}, auto_zones=True),
    WidgetTemplate("marker", "Marker im Bereich", INSTRUMENTS,
                   "Eine Marke wandert zwischen Minimum und Maximum, z. B. für die Trittfrequenz.", "bar",
                   default_channel="cadence", cfg={"bar_style": "marker"}),
    WidgetTemplate("scope", "Scope (laufender Graph)", INSTRUMENTS,
                   "Verlauf der letzten Sekunden wie ein Oszilloskop – rechts ist jetzt.", "scope",
                   default_channel="heart_rate", size=0.12),
    WidgetTemplate("minimap", "Minimap (Navi)", ROUTE, "Karte, die mitfährt – mit Straßen aus OpenStreetMap.",
                   "minimap", channels=("position",)),
    WidgetTemplate("map", "Streckenübersicht", ROUTE, "Ganze Strecke mit gefahrenem Teil und Position.", "map",
                   channels=("position",)),
    WidgetTemplate("map_arrow", "Route mit Richtungspfeil", ROUTE,
                   "Streckenübersicht, die Position zeigt als Pfeil in Fahrtrichtung.", "map", channels=("position",),
                   cfg={"position_marker": "arrow"}),
    WidgetTemplate("route_line", "Freie Routenlinie", ROUTE,
                   "Nur eine weiße Linie mit Punkt, ohne Kasten – wie in TV-Übertragungen.", "map",
                   channels=("position",), cfg={"show_ridden": False, "stroke": 0.06},
                   style={"badge": False, "shadow": True, "track_color": "#FFFFFFFF", "accent_color": "#FFFFFFFF"}),
    WidgetTemplate("profile", "Höhenprofil", ROUTE, "Profil über die Distanz mit Position.", "profile",
                   default_channel="elevation"),
    WidgetTemplate("profile_corner", "Profil-Ecke (m und ft)", ROUTE,
                   "Randloses Profil für die Bildecke, Höhe neben der Marke in m und ft.", "profile",
                   default_channel="elevation",
                   cfg={"value_at_marker": True, "alt_unit": True, "show_ridden": False},
                   style={"badge": False, "track_color": "#FFFFFF40", "label_uppercase": True}),
    WidgetTemplate("climb", "Anstiegs-Vorschau", ROUTE, "Die nächsten Kilometer nach Steigung eingefärbt.", "climb",
                   channels=("elevation",)),
]}

GROUPS = (VALUE, INSTRUMENTS, ROUTE)


def channels_for(template: WidgetTemplate, available: list[str]) -> list[str]:
    """Signale, die zur Vorlage passen und in der Fahrt vorhanden sind (Vorschlag zuerst)."""
    out = [c for c in available if template.allows(c)]
    if template.default_channel in out:
        out.remove(template.default_channel)
        out.insert(0, template.default_channel)
    return out


def even_zones(lo: float, hi: float, n: int = len(ZONE_COLORS)) -> list[tuple[float, str]]:
    """n Farbzonen gleichmäßig über den Bereich (die erste beginnt am Minimum)."""
    step = (hi - lo) / n
    return [(round(lo + step * k, 1), ZONE_COLORS[k % len(ZONE_COLORS)]) for k in range(n)]
