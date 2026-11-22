"""Sprache der Texte im Overlay (Beschriftungen der Widgets, Anstiegs-Vorschau) – ohne Qt.

Die Bedienoberfläche bleibt deutsch; das hier betrifft nur, was im exportierten Video steht.
Einheiten (km/h, W, bpm …) sind international und werden nicht übersetzt.
"""

from __future__ import annotations

DEFAULT_LANGUAGE = "de"

#: Sprachcode -> Name in der Auswahl
LANGUAGES: dict[str, str] = {"de": "Deutsch", "en": "English"}

#: Standard-Beschriftung je Kanal
CHANNEL_LABELS: dict[str, dict[str, str]] = {
    "de": {
        "speed": "TEMPO",
        "distance": "DISTANZ",
        "elevation": "HÖHE",
        "gradient": "STEIGUNG",
        "elevation_gain": "HÖHENMETER",
        "vam": "VAM",
        "power": "LEISTUNG",
        "heart_rate": "PULS",
        "cadence": "KADENZ",
        "temperature": "TEMP",
        "elapsed_time": "GESAMTZEIT",
        "moving_time": "FAHRZEIT",
        "clock": "UHRZEIT",
        "map": "ROUTE",
    },
    "en": {
        "speed": "SPEED",
        "distance": "DIST",
        "elevation": "ALT",
        "gradient": "GRADE",
        "elevation_gain": "GAIN",
        "vam": "VAM",
        "power": "POWER",
        "heart_rate": "HR",
        "cadence": "CAD",
        "temperature": "TEMP",
        "elapsed_time": "ELAPSED",
        "moving_time": "MOVING",
        "clock": "TIME",
        "map": "ROUTE",
    },
}

#: sonstige Texte; Platzhalter im str.format-Stil
TEXTS: dict[str, dict[str, str]] = {
    "de": {
        "climb": "ANSTIEG",
        "descent": "ABFAHRT",
        "climb_ahead": "IN {dist}",
        "descent_ahead": "ABFAHRT IN {dist}",
        "no_climb": "KEIN ANSTIEG",
        "no_descent": "KEINE ABFAHRT",
        "flat": "FLACH",
        "remaining": "noch {dist}",
        "gain_unit_m": "hm",
    },
    "en": {
        "climb": "CLIMB",
        "descent": "DESCENT",
        "climb_ahead": "CLIMB IN {dist}",
        "descent_ahead": "DESCENT IN {dist}",
        "no_climb": "NO CLIMB",
        "no_descent": "NO DESCENT",
        "flat": "FLAT",
        "remaining": "{dist} to go",
        "gain_unit_m": "m",
    },
}


def normalize_language(lang: object) -> str:
    """Unbekannte oder fehlende Angaben werden zur Standardsprache."""
    return lang if isinstance(lang, str) and lang in LANGUAGES else DEFAULT_LANGUAGE


def channel_text(channel: str, lang: str) -> str:
    lang = normalize_language(lang)
    return CHANNEL_LABELS[lang].get(channel) or CHANNEL_LABELS["en"].get(channel) or channel.upper()


def tr(key: str, lang: str, **kwargs: object) -> str:
    lang = normalize_language(lang)
    text = TEXTS[lang].get(key) or TEXTS["en"][key]
    return text.format(**kwargs) if kwargs else text
