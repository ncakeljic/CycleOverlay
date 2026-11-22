"""Beschreibung des Widget-Inspectors (ohne Qt).

Für jeden Widget-Typ steht hier, **welche Einstellung in welchem Abschnitt mit welchem
Bedienelement** erscheint und wann sie sichtbar ist. ``inspector_panel`` baut daraus die
Oberfläche. Vorteile: übersichtlich, neue Optionen sind eine Zeile, und ein Test prüft ohne Qt,
dass keine Einstellung eines Widgets in der Oberfläche fehlt.

Regeln der Darstellung (siehe ``form_kit``):
- Auswahl mit höchstens ``SEGMENT_MAX`` Möglichkeiten → nebeneinanderliegende Knöpfe (ein Klick),
  sonst Aufklappliste.
- Zahlen mit ``slider=True`` → Schieberegler plus Zahlenfeld.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..rendering.layout import WidgetConfig
from ..rendering.minimap_theme import THEMES
from ..rendering.typography import LABEL_WEIGHT_OPTIONS, VALUE_WEIGHT_OPTIONS, WEIGHTS
from ..rendering.typography import summary as type_summary
from ..rendering.values import CLOCK_CHANNEL

SEGMENT_MAX = 4

Pred = Callable[[WidgetConfig, Any], bool]


def _always(_cfg: WidgetConfig, _ctx: Any) -> bool:
    return True


@dataclass
class Field:
    key: str  # Attributname im Ziel
    label: str  # Beschriftung ("" = keine, z. B. bei Häkchen mit eigenem Text)
    kind: str  # choice | bool | number | color | optional_color | text | font | anchor | custom
    target: str = "cfg"  # cfg | style | minimap | climb
    options: list[tuple[Any, str]] = field(default_factory=list)  # für choice
    lo: float = 0.0
    hi: float = 100.0
    step: float = 1.0
    decimals: int = 1
    suffix: str = ""
    prefix: str = ""
    scale: float = 1.0  # angezeigter Wert = gespeicherter Wert / scale
    slider: bool = False
    special: str = ""  # Text für den kleinsten Wert (z. B. „Standard“)
    tooltip: str = ""
    rebuild: bool = False  # nach Änderung neu aufbauen (andere Felder hängen davon ab)
    visible_if: Pred = _always
    keys: tuple[str, ...] = ()  # custom: welche Einstellungen das Element bedient (für die Prüfung)
    default: Callable[[WidgetConfig], str] | None = None  # optional_color: Farbe, wenn nichts gewählt

    @property
    def segmented(self) -> bool:
        return self.kind == "choice" and len(self.options) <= SEGMENT_MAX

    def covered(self) -> list[tuple[str, str]]:
        if self.kind == "custom":
            return [(self.target, k) for k in self.keys]
        return [(self.target, self.key)]


@dataclass
class Section:
    key: str
    title: str
    fields: list[Field]
    collapsed: bool = False  # Standard beim ersten Öffnen
    summary: Callable[[WidgetConfig], str] | None = None  # Kurzinfo im zugeklappten Kopf
    visible_if: Pred = _always


# --------------------------------------------------------------------------- Hilfen

ANCHOR_TITLES = {
    "top_left": "oben links", "top_center": "oben Mitte", "top_right": "oben rechts",
    "center_left": "Mitte links", "center": "Mitte", "center_right": "Mitte rechts",
    "bottom_left": "unten links", "bottom_center": "unten Mitte", "bottom_right": "unten rechts",
}
LABEL_POS = [("top", "oben"), ("bottom", "unten"), ("left", "links"), ("none", "keine")]


def _mm(cfg: WidgetConfig):
    return cfg.minimap


def _is(*types: str) -> Pred:
    return lambda cfg, _ctx: cfg.type in types


def _yes(b: bool) -> str:
    return "an" if b else "aus"


# --------------------------------------------------------------------------- gemeinsame Abschnitte


def content_section() -> Section:
    return Section("content", "Inhalt", [
        Field("channel", "Signal", "custom", keys=("channel",)),
        Field("type", "Darstellung", "custom", keys=("type",)),
        Field("scope", "Ausschnitt", "choice", options=[("full", "ganze Fahrt"), ("clip", "Umgebung des Clips")],
              visible_if=_is("map", "profile")),
        Field("show_ridden", "", "bool", visible_if=_is("map", "profile"),
              tooltip="Gefahrenen Teil hervorheben"),
    ], summary=None)


def position_section() -> Section:
    return Section("position", "Position & Größe", [
        Field("anchor", "Ausrichtung an", "anchor"),
        Field("x", "Abstand X", "number", lo=-100, hi=100, step=0.5, suffix=" %", scale=0.01),
        Field("y", "Abstand Y", "number", lo=-100, hi=100, step=0.5, suffix=" %", scale=0.01),
        Field("size", "Größe", "number", lo=0.5, hi=100, step=0.5, suffix=" %", scale=0.01, slider=True),
        Field("visible", "", "bool", tooltip="Widget anzeigen"),
    ], summary=lambda c: f"{ANCHOR_TITLES.get(c.anchor, c.anchor)} · {c.size * 100:.0f} %")


def grade_section(collapsed: bool = True, visible_if: Pred = _always) -> Section:
    return Section("grade", "Steigungsfarben", [
        Field("grade_scheme", "Farbschema", "custom", keys=("grade_steps", "grade_colors")),
    ], collapsed=collapsed, visible_if=visible_if)


def _weights(keys: list[str]) -> list[tuple[str, str]]:
    return [(k, WEIGHTS[k][0]) for k in keys]


def _has_label(cfg: WidgetConfig, _ctx: Any) -> bool:
    return cfg.type not in ("minimap", "climb") and cfg.label_position != "none"


def _has_unit(cfg: WidgetConfig, _ctx: Any) -> bool:
    return cfg.type not in ("minimap", "climb") and cfg.channel != CLOCK_CHANNEL and cfg.show_unit


def type_section() -> Section:
    """Schrift: fertige Schriftstile zum Anklicken, darunter die Feineinstellungen."""
    return Section("type", "Schrift", [
        Field("type_preset", "", "custom", target="style", keys=()),
        Field("font_family", "Schrift", "font", target="style", rebuild=True),
        Field("value_weight", "Stärke Zahl", "choice", target="style", options=_weights(VALUE_WEIGHT_OPTIONS),
              rebuild=True),
        Field("label_weight", "Stärke Text", "choice", target="style", options=_weights(LABEL_WEIGHT_OPTIONS),
              rebuild=True, tooltip="Beschriftung und Einheit"),
        Field("font_width", "Breite", "number", target="style", lo=60, hi=140, step=1, decimals=0, suffix=" %",
              scale=0.01, slider=True, tooltip="Unter 100 % schmaler und höher wirkend, über 100 % breiter"),
        Field("tracking", "Zeichenabstand", "number", target="style", lo=-10, hi=40, step=1, decimals=0, suffix=" %",
              slider=True),
        Field("italic", "", "bool", target="style", rebuild=True, tooltip="Kursiv"),
        Field("label_scale", "Größe Beschriftung", "number", target="style", lo=50, hi=200, step=5, decimals=0,
              suffix=" %", scale=0.01, slider=True, visible_if=_has_label),
        Field("unit_scale", "Größe Einheit", "number", target="style", lo=50, hi=200, step=5, decimals=0,
              suffix=" %", scale=0.01, slider=True, visible_if=_has_unit),
        Field("label_uppercase", "", "bool", target="style", visible_if=_has_label, tooltip="Beschriftung in GROSSBUCHSTABEN"),
        Field("apply_type_all", "", "custom", target="style", keys=()),
    ], collapsed=True, summary=lambda c: type_summary(c.style))


def style_section(*, fills: bool = True, outline: bool = True, colors: bool = True,
                  badge: bool = True, color_titles: dict[str, str] | None = None) -> Section:
    titles = {"text_color": "Zahl", "label_color": "Beschriftung", "accent_color": "Füllung",
              "track_color": "Hintergrundspur"}
    titles.update(color_titles or {})
    fields: list[Field] = []
    for key in ("text_color", "label_color", "accent_color", "track_color") if colors else ():
        if key in ("accent_color", "track_color") and not fills:
            continue
        fields.append(Field(key, titles[key], "color", target="style"))
    fields.append(Field("shadow", "Schatten", "custom", target="style", keys=("shadow", "shadow_color")))
    fields.append(Field("shadow_distance", "Schattenabstand", "number", target="style", lo=0, hi=400, step=10,
                        decimals=0, suffix=" %", scale=0.01, slider=True,
                        visible_if=lambda c, _x: c.style.shadow))
    if outline:
        fields.append(Field("outline", "Kontur", "custom", target="style", keys=("outline", "outline_color")))
        fields.append(Field("outline_width", "Konturstärke", "number", target="style", lo=20, hi=400, step=10,
                            decimals=0, suffix=" %", scale=0.01, slider=True,
                            visible_if=lambda c, _x: c.style.outline))
    if badge:
        fields.append(Field("badge", "Hintergrund-Kachel", "custom", target="style", keys=("badge", "badge_color")))
        fields.append(Field("badge_radius", "Ecken", "number", target="style", lo=0, hi=50, step=1, decimals=0,
                            suffix=" %", scale=0.01, slider=True, special="eckig",
                            visible_if=lambda c, _x: c.style.badge))
    fields.append(Field("apply_style_all", "", "custom", target="style", keys=()))
    return Section("style", "Farben & Effekte", fields, collapsed=True,
                   summary=lambda c: " · ".join(t for on, t in ((c.style.shadow, "Schatten"), (c.style.outline, "Kontur"),
                                                                (c.style.badge, "Kachel")) if on) or "ohne Effekte")


# --------------------------------------------------------------------------- Wert, Instrumente, Karte/Profil


def value_sections() -> list[Section]:
    is_clock = lambda cfg, _ctx: cfg.channel == CLOCK_CHANNEL  # noqa: E731
    not_clock = lambda cfg, _ctx: cfg.channel != CLOCK_CHANNEL  # noqa: E731
    text = Section("text", "Text & Zahl", [
        Field("label", "Beschriftung", "text", tooltip="Leer = Standard"),
        Field("label_position", "Beschriftung", "choice", options=LABEL_POS),
        Field("show_unit", "", "bool", visible_if=not_clock, tooltip="Einheit anzeigen"),
        Field("show_icon", "", "bool", visible_if=_is("text"), tooltip="Symbol vor dem Wert (Herz, Blitz …)"),
        Field("progress_mode", "Anzeige", "choice", visible_if=_is("progress"),
              options=[("ridden_total", "gefahren / gesamt"), ("remaining", "noch übrig")]),
        Field("value_at_marker", "", "bool", visible_if=_is("profile"), rebuild=True,
              tooltip="Wert neben der Positionsmarke statt oben"),
        Field("alt_unit", "", "bool", visible_if=_is("profile"),
              tooltip="Zusätzlich in der anderen Einheit (m und ft, km/h und mph)"),
        Field("scope_window_s", "Zeitspanne", "number", lo=3, hi=600, step=1, decimals=0, suffix=" s", slider=True,
              visible_if=_is("scope"), tooltip="Wie viele Sekunden vor dem aktuellen Moment der Graph zeigt"),
        Field("decimals", "Nachkommastellen", "choice",
              options=[(None, "Standard"), (0, "0"), (1, "1"), (2, "2")], visible_if=not_clock),
        Field("decimals", "Sekunden", "choice", options=[(None, "Standard"), (0, "nein"), (1, "ja")],
              visible_if=is_clock),
        Field("update_rate_hz", "Zahl aktualisieren", "number", lo=0, hi=30, step=0.5, suffix=" Hz",
              special="jedes Frame", tooltip="Takt, in dem die Zahl neue Messwerte übernimmt. Niedrige Werte verhindern\n"
                                             "Flackern. Mit „Durchzählen“ (Tab Filter) läuft die Zahl dazwischen durch\n"
                                             "alle Zwischenwerte. Bogen und Balken laufen immer flüssig."),
    ], summary=lambda c: c.label or "Standardbeschriftung")
    scale = Section("scale", "Skala & Zonen", [
        Field("range", "Bereich", "custom", keys=("min_value", "max_value"), visible_if=_is("arc", "bar", "scope")),
        Field("bar_style", "Form", "choice", visible_if=_is("bar"), rebuild=True,
              options=[("fill", "Füllbalken"), ("marker", "Marke im Bereich")]),
        Field("zones", "Farbzonen", "custom", keys=("zones",), visible_if=_is("text", "arc", "bar", "scope", "profile", "map")),
        Field("zone_fill", "Zonen zeigen", "choice", visible_if=lambda c, _x: c.type == "bar" and bool(c.zones)
              and c.bar_style == "fill", options=[("solid", "aktuelle Farbe"), ("segments", "alle Abschnitte")],
              tooltip="aktuelle Farbe: ganzer Balken in der Farbe der aktuellen Zone\n"
                      "alle Abschnitte: jede Zone in ihrer Farbe, wie eine Ampel"),
        Field("zones_color_text", "", "bool", visible_if=_is("text"), tooltip="Zahl in Zonenfarbe"),
        Field("sweep_deg", "Bogen", "number", lo=90, hi=360, step=10, decimals=0, suffix=" °", slider=True,
              visible_if=_is("arc")),
        Field("show_needle", "", "bool", visible_if=_is("arc"), tooltip="Zeiger", rebuild=True),
        Field("needle_style", "Zeiger", "choice", visible_if=lambda c, _x: c.type == "arc" and c.show_needle,
              options=[("line", "Strich"), ("pointer", "spitz")]),
        Field("show_ticks", "", "bool", visible_if=_is("arc"), tooltip="Skala", rebuild=True),
        Field("show_tick_labels", "", "bool", visible_if=lambda c, _x: c.type == "arc" and c.show_ticks,
              tooltip="Zahlen an der Skala"),
        Field("orientation", "Ausrichtung", "choice", options=[("horizontal", "waagerecht"), ("vertical", "senkrecht")],
              visible_if=_is("bar")),
        Field("stroke", "Strichstärke", "number", lo=1, hi=30, step=0.5, suffix=" %", scale=0.01, slider=True,
              visible_if=_is("arc", "bar", "map", "profile", "progress", "scope")),
    ], collapsed=True)
    route = Section("route", "Route", [
        Field("position_marker", "Position", "choice", options=[("dot", "Punkt"), ("arrow", "Pfeil in Fahrtrichtung")]),
        Field("route_coloring", "Route einfärben", "choice", target="minimap",
              options=[("solid", "eine Farbe"), ("gradient", "nach Steigung")], rebuild=True),
    ], visible_if=_is("map"))
    return [text, scale, route,
            grade_section(visible_if=lambda c, _x: c.type == "map" and c.minimap.route_coloring == "gradient")]


# --------------------------------------------------------------------------- Minimap


def minimap_sections() -> list[Section]:
    tilt = lambda c, _x: c.minimap.view == "tilt"  # noqa: E731
    ribbon_on = lambda c, _x: c.minimap.elevation_ribbon  # noqa: E731
    view = Section("view", "Ansicht", [
        Field("orientation", "Ausrichtung", "choice", target="minimap",
              options=[("heading", "Fahrtrichtung oben"), ("north", "Norden oben")],
              tooltip="Fahrtrichtung oben: die Karte dreht sich · Norden oben: der Pfeil dreht sich"),
        Field("view", "Ansicht", "choice", target="minimap", options=[("top", "Von oben"), ("tilt", "3D")],
              rebuild=True),
        Field("tilt_deg", "Neigung", "number", target="minimap", lo=10, hi=75, step=1, decimals=0, suffix=" °",
              slider=True, visible_if=tilt),
    ], summary=lambda c: ("Fahrtrichtung" if c.minimap.orientation == "heading" else "Norden") + " · "
                         + ("3D" if c.minimap.view == "tilt" else "von oben"))
    themes = [(k, t.title) for k, t in THEMES.items()]
    karte = Section("map", "Karte", [
        Field("background", "Hintergrund", "choice", target="minimap",
              options=[("osm", "Straßen (OSM)"), ("route", "Nur Route")], rebuild=True),
        Field("map_hint", "", "custom", keys=()),
        Field("theme", "Farbthema", "choice", target="minimap", options=themes, rebuild=True),
        Field("shape", "Form", "choice", target="minimap", options=[("circle", "Rund"), ("rounded", "Eckig")]),
    ], collapsed=True, summary=lambda c: ("OSM" if c.minimap.background == "osm" else "nur Route") + " · "
                                         + THEMES[c.minimap.theme].title + " · "
                                         + ("rund" if c.minimap.shape == "circle" else "eckig"))
    zoom = Section("zoom", "Zoom", [
        Field("radius_m", "Sichtweite", "number", target="minimap", lo=50, hi=5000, step=50, decimals=0,
              suffix=" m", slider=True),
        Field("route_range", "Route zeichnen bis", "number", target="minimap", lo=1, hi=10, step=0.5,
              suffix=" × Sichtweite",
              tooltip="Route bis zu diesem Vielfachen der Sichtweite zeichnen; zum Ende hin wird sie\n"
                      "weich ausgeblendet. Verhindert Lücken, wenn eine Kehre kurz aus dem Bild läuft."),
        Field("auto_zoom", "", "bool", target="minimap", tooltip="Bei höherem Tempo herauszoomen"),
    ], summary=lambda c: f"{c.minimap.radius_m:.0f} m · Route {c.minimap.route_range:g}×")
    route = Section("route", "Route & Höhe", [
        Field("show_route", "", "bool", target="minimap", tooltip="Route anzeigen"),
        Field("route_coloring", "Route einfärben", "choice", target="minimap",
              options=[("solid", "eine Farbe"), ("gradient", "nach Steigung")]),
        Field("route_color", "Routenfarbe", "optional_color", target="minimap",
              default=lambda c: THEMES[c.minimap.theme].route_ahead),
        Field("grade_badge", "", "bool", target="minimap", tooltip="Steigung neben dem Pfeil anzeigen"),
        Field("elevation_ribbon", "", "bool", target="minimap", rebuild=True,
              tooltip="Route als 3D-Höhenband (nur in der Ansicht 3D)"),
        Field("ribbon_hint", "", "custom", keys=(), visible_if=lambda c, _x: c.minimap.elevation_ribbon
              and c.minimap.view != "tilt"),
        Field("ribbon_exaggeration", "Überhöhung", "number", target="minimap", lo=0.5, hi=30, step=0.5,
              prefix="× ", slider=True, visible_if=ribbon_on),
        Field("ribbon_reference", "Höhenbezug", "choice", target="minimap",
              options=[("position", "meine Position"), ("lowest", "tiefster Punkt")], visible_if=ribbon_on,
              tooltip="meine Position: Abfahrten tauchen unter die Karte ab\n"
                      "tiefster Punkt im Umkreis: alles über dem Boden, Abfahrt als Rampe ins Tal"),
        Field("ribbon_curtain", "", "bool", target="minimap", visible_if=ribbon_on,
              tooltip="Vorhang bis zum Boden"),
    ], collapsed=True, summary=lambda c: ("nach Steigung" if c.minimap.route_coloring == "gradient" else "eine Farbe")
                                          + (f" · Band {c.minimap.ribbon_exaggeration:g}×"
                                             if c.minimap.elevation_ribbon else ""))
    arrow = Section("arrow", "Pfeil", [
        Field("arrow", "Form", "choice", target="minimap",
              options=[("chevron", "Navi-Pfeil"), ("triangle", "Dreieck"), ("dot", "Punkt")]),
        Field("arrow_size", "Größe", "number", target="minimap", lo=3, hi=30, step=0.5, suffix=" %", scale=0.01,
              slider=True),
        Field("arrow_color", "Farbe", "optional_color", target="minimap",
              default=lambda c: THEMES[c.minimap.theme].arrow),
    ], collapsed=True, summary=lambda c: {"chevron": "Navi-Pfeil", "triangle": "Dreieck", "dot": "Punkt"}
                                          .get(c.minimap.arrow, c.minimap.arrow) + f" · {c.minimap.arrow_size * 100:.0f} %")
    extras = Section("extras", "Extras", [
        Field("show_compass", "", "bool", target="minimap", tooltip="Kompass (N)"),
        Field("show_scale", "", "bool", target="minimap", tooltip="Maßstab (nur von oben)"),
        Field("show_attribution", "", "bool", target="minimap", rebuild=True, tooltip="Hinweis „© OpenStreetMap“"),
        Field("attribution_hint", "", "custom", keys=(), visible_if=lambda c, _x: not c.minimap.show_attribution),
    ], collapsed=True, summary=lambda c: " · ".join(
        t for on, t in ((c.minimap.show_compass, "Kompass"), (c.minimap.show_scale, "Maßstab"),
                        (c.minimap.show_attribution, "Quelle")) if on) or "nichts")
    advanced = Section("advanced", "Erweitert", [
        Field("heading_window_m", "Fahrtrichtung glätten über", "number", target="minimap", lo=5, hi=200, step=5,
              decimals=0, suffix=" m", tooltip="Strecke, über die die Fahrtrichtung bestimmt wird.\n"
                                               "Größer = ruhigere Karte, kleiner = reagiert schneller in Kurven."),
    ], collapsed=True)
    return [view, karte, zoom, route,
            grade_section(visible_if=lambda c, _x: c.minimap.route_coloring == "gradient"),
            arrow, extras, advanced]


# --------------------------------------------------------------------------- Anstiegs-Vorschau


def climb_sections() -> list[Section]:
    show = Section("climb", "Anzeige", [
        Field("mode", "Zeigt", "choice", target="climb",
              options=[("climbs", "Anstiege"), ("descents", "Abfahrten"), ("both", "beides")], rebuild=True),
        Field("lookahead_m", "Vorschau voraus", "number", target="climb", lo=0.3, hi=50, step=0.5, suffix=" km",
              scale=1000.0, slider=True),
        Field("behind_m", "Bereich hinter dir", "number", target="climb", lo=0, hi=5, step=0.1, suffix=" km",
              scale=1000.0),
        Field("colored", "", "bool", target="climb", tooltip="Profil nach Steigung einfärben"),
        Field("show_info", "", "bool", target="climb", tooltip="Infozeile (Länge, hm, Ø %)"),
        Field("hide_when_none", "", "bool", target="climb", tooltip="Ausblenden, wenn kein Anstieg in Sicht"),
    ], summary=lambda c: {"climbs": "Anstiege", "descents": "Abfahrten", "both": "Anstiege und Abfahrten"}
                         .get(c.climb.mode, c.climb.mode) + f" · {c.climb.lookahead_m / 1000:g} km")
    detect = Section("detect", "Was zählt als Anstieg/Abfahrt?", [
        Field("min_length_m", "Mindestlänge", "number", target="climb", lo=50, hi=20000, step=100, decimals=0,
              suffix=" m", rebuild=True),
        Field("min_gain_m", "Mindest-Höhenmeter", "number", target="climb", lo=1, hi=2000, step=5, decimals=0,
              suffix=" m", rebuild=True),
        Field("min_grade", "Mindest-Ø-Steigung", "number", target="climb", lo=0.5, hi=20, step=0.5, suffix=" %",
              rebuild=True),
        Field("climb_count", "", "custom", keys=()),
    ], collapsed=True, summary=lambda c: f"ab {c.climb.min_length_m:.0f} m · {c.climb.min_gain_m:.0f} hm · "
                                         f"{c.climb.min_grade:g} %")
    return [show, detect, grade_section(collapsed=True)]


# --------------------------------------------------------------------------- Zusammenstellung


def sections_for(cfg: WidgetConfig) -> list[Section]:
    """Alle Abschnitte für ein Widget, in Anzeigereihenfolge (noch ohne Sichtbarkeitsfilter)."""
    out = [content_section(), position_section()]
    if cfg.type == "minimap":
        out += minimap_sections()
        out.append(type_section())
        out.append(style_section(colors=False, outline=False, badge=False))
    elif cfg.type == "climb":
        out += climb_sections()
        out.append(type_section())
        out.append(style_section(outline=False, color_titles={
            "text_color": "Text", "label_color": "Linie/Details", "accent_color": "Position",
            "track_color": "Füllung (ohne Steigungsfarben)"}))
    else:
        out += value_sections()
        out.append(type_section())
        out.append(style_section(fills=cfg.type != "text"))
    return out


def visible_sections(cfg: WidgetConfig, ctx: Any = None) -> list[tuple[Section, list[Field]]]:
    """Abschnitte mit ihren sichtbaren Feldern; leere Abschnitte fallen weg."""
    res = []
    for sec in sections_for(cfg):
        if not sec.visible_if(cfg, ctx):
            continue
        fields = [f for f in sec.fields if f.visible_if(cfg, ctx)]
        if fields:
            res.append((sec, fields))
    return res


def covered_settings(cfg: WidgetConfig) -> set[tuple[str, str]]:
    """Welche Einstellungen die Oberfläche für diesen Widget-Typ bedienen kann (für Tests)."""
    out: set[tuple[str, str]] = set()
    for sec in sections_for(cfg):
        for f in sec.fields:
            out.update(f.covered())
    return out
