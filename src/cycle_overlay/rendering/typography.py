"""Schrift-Einstellungen der Widgets – ohne Qt.

Mitgelieferte Schriften (alle SIL Open Font License, siehe ``resources/fonts``) sehen auf macOS und
Windows gleich aus. „Schriftstile“ sind fertige Kombinationen aus Schrift, Stärke, Breite usw.,
die mit einem Klick gesetzt und danach frei verändert werden können.
"""

from __future__ import annotations

from dataclasses import dataclass

#: mitgelieferte Schriftfamilien -> kurze Beschreibung für die Auswahl
BUNDLED_FAMILIES: dict[str, str] = {
    "Inter": "neutral, sehr gut lesbar",
    "Roboto Condensed": "schmal, sportlich",
    "Anton": "hoch und kräftig, Plakat",
    "Fira Sans": "technisch, klar",
    "Montserrat": "rund und modern",
    "Archivo Black": "breit und fett",
}

#: Strichstärken: Schlüssel -> (Titel, CSS-Gewicht)
WEIGHTS: dict[str, tuple[str, int]] = {
    "light": ("dünn", 300),
    "regular": ("normal", 400),
    "medium": ("mittel", 500),
    "semibold": ("halbfett", 600),
    "bold": ("fett", 700),
    "black": ("extra fett", 900),
}

#: Auswahl in der Oberfläche (je 4 Möglichkeiten -> Knopfreihe)
VALUE_WEIGHT_OPTIONS = ["light", "regular", "bold", "black"]
LABEL_WEIGHT_OPTIONS = ["light", "regular", "semibold", "bold"]

WIDTH_RANGE = (0.6, 1.4)  # Breite (horizontale Streckung)
TRACKING_RANGE = (-10.0, 40.0)  # Laufweite in % der Schriftgröße
TEXT_SCALE_RANGE = (0.5, 2.0)  # Größe von Beschriftung/Einheit relativ zum Standard


@dataclass(frozen=True)
class TypePreset:
    title: str
    hint: str
    font_family: str
    value_weight: str
    label_weight: str
    font_width: float = 1.0
    tracking: float = 0.0
    italic: bool = False
    label_scale: float = 1.0
    unit_scale: float = 1.0


#: fertige Schriftstile – bewusst wenige, gut unterscheidbare
TYPE_PRESETS: dict[str, TypePreset] = {
    "classic": TypePreset("Klassisch", "Inter, fett", "Inter", "bold", "semibold"),
    "condensed": TypePreset("Schmal", "schmal und fett, sportlich", "Roboto Condensed", "bold", "bold"),
    "tall_thin": TypePreset("Lang und dünn", "hohe, dünne Ziffern", "Roboto Condensed", "light", "regular",
                            font_width=0.9, tracking=2.0),
    "poster": TypePreset("Plakat", "groß, hoch, kräftig wie im TV", "Anton", "regular", "regular",
                         tracking=1.0, label_scale=1.15),
    "compact_bold": TypePreset("Klein und fett", "breit und wuchtig", "Archivo Black", "regular", "regular",
                               font_width=0.95, label_scale=0.9),
    "round": TypePreset("Rund", "geometrisch, modern", "Montserrat", "bold", "semibold"),
    "technical": TypePreset("Technisch", "klar, mit Luft zwischen den Zeichen", "Fira Sans", "medium", "regular",
                            tracking=4.0),
    "race": TypePreset("Rennen kursiv", "schmal, extra fett, kursiv", "Roboto Condensed", "black", "bold",
                       italic=True),
}

PRESET_KEYS = ("font_family", "value_weight", "label_weight", "font_width", "tracking", "italic",
               "label_scale", "unit_scale")


def apply_preset(style, key: str) -> None:
    """Schriftstil auf ein ``Style``-Objekt übertragen (Farben bleiben)."""
    preset = TYPE_PRESETS[key]
    for k in PRESET_KEYS:
        setattr(style, k, getattr(preset, k))


def preset_of(style) -> str | None:
    """Schlüssel des Schriftstils, der genau den Einstellungen entspricht (sonst None)."""
    for key, preset in TYPE_PRESETS.items():
        if all(_same(getattr(style, k), getattr(preset, k)) for k in PRESET_KEYS):
            return key
    return None


def _same(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return abs(float(a) - float(b)) < 1e-6
    return a == b


def summary(style) -> str:
    """Kurzinfo für den zugeklappten Abschnitt."""
    key = preset_of(style)
    if key:
        return f"{TYPE_PRESETS[key].title} · {style.font_family}"
    parts = [style.font_family, WEIGHTS.get(style.value_weight, (style.value_weight,))[0]]
    if abs(style.font_width - 1.0) > 1e-6:
        parts.append(f"Breite {style.font_width * 100:.0f} %")
    if style.italic:
        parts.append("kursiv")
    return " · ".join(parts)
