"""Einheiten für die Anzeige (metrisch/imperial)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

METRIC = "metric"
IMPERIAL = "imperial"


@dataclass(frozen=True)
class ChannelInfo:
    label: str  # Standard-Beschriftung im Widget
    title: str  # Bezeichnung in Listen
    kind: str  # Art der Größe für die Umrechnung
    decimals: int  # Standard-Nachkommastellen


CHANNELS: dict[str, ChannelInfo] = {
    "speed": ChannelInfo("SPEED", "Geschwindigkeit", "speed", 1),
    "distance": ChannelInfo("DIST", "Distanz", "distance", 2),
    "elevation": ChannelInfo("ALT", "Höhe", "elevation", 0),
    "gradient": ChannelInfo("GRADE", "Steigung", "percent", 1),
    "elevation_gain": ChannelInfo("GAIN", "Höhenmeter", "elevation", 0),
    "vam": ChannelInfo("VAM", "VAM / Steigrate", "vam", 0),
    "power": ChannelInfo("POWER", "Leistung", "power", 0),
    "heart_rate": ChannelInfo("HR", "Herzfrequenz", "heart_rate", 0),
    "cadence": ChannelInfo("CAD", "Trittfrequenz", "cadence", 0),
    "temperature": ChannelInfo("TEMP", "Temperatur", "temperature", 1),
    "elapsed_time": ChannelInfo("ELAPSED", "Gesamtzeit", "duration", 0),
    "moving_time": ChannelInfo("MOVING", "Fahrzeit", "duration", 0),
}

_UNITS = {
    METRIC: {
        "speed": ("km/h", lambda v: v * 3.6),
        "distance": ("km", lambda v: v / 1000.0),
        "elevation": ("m", lambda v: v),
        "vam": ("m/h", lambda v: v),
        "temperature": ("°C", lambda v: v),
    },
    IMPERIAL: {
        "speed": ("mph", lambda v: v * 2.2369362920544),
        "distance": ("mi", lambda v: v / 1609.344),
        "elevation": ("ft", lambda v: v / 0.3048),
        "vam": ("ft/h", lambda v: v / 0.3048),
        "temperature": ("°F", lambda v: v * 9 / 5 + 32),
    },
}

_FIXED = {
    "percent": "%",
    "power": "W",
    "heart_rate": "bpm",
    "cadence": "rpm",
    "duration": "",
}


def unit_for(channel: str, system: str = METRIC) -> str:
    kind = CHANNELS[channel].kind
    if kind in _FIXED:
        return _FIXED[kind]
    return _UNITS[system][kind][0]


def convert(channel: str, values, system: str = METRIC):
    """Interne Werte (SI) in Anzeige-Einheiten umrechnen. Gibt (Werte, Einheit) zurück."""
    if system not in _UNITS:
        raise ValueError(f"Unbekanntes Einheitensystem: {system}")
    kind = CHANNELS[channel].kind
    if kind in _FIXED:
        return values, _FIXED[kind]
    unit, fn = _UNITS[system][kind]
    if isinstance(values, np.ndarray):
        return fn(values.astype(float)), unit
    return fn(values), unit


def format_duration(seconds: float) -> str:
    """Sekunden als H:MM:SS (negativ mit Vorzeichen)."""
    if seconds != seconds:  # NaN
        return "--:--:--"
    sign = "-" if seconds < 0 else ""
    s = int(round(abs(seconds)))
    return f"{sign}{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def format_value(channel: str, value: float, system: str = METRIC, decimals: int | None = None) -> str:
    """Wert inkl. Einheit für die Textausgabe, '--' bei fehlendem Wert."""
    info = CHANNELS[channel]
    if info.kind == "duration":
        return format_duration(value)
    v, unit = convert(channel, value, system)
    if v != v:
        return f"-- {unit}".strip()
    d = info.decimals if decimals is None else decimals
    return f"{v:.{d}f} {unit}".strip()


def format_offset(seconds: float, decimals: int = 3) -> str:
    """Versatz als ±H:MM:SS.mmm (z. B. „+0:00:12.500“, „-1:02:03.000“)."""
    if seconds != seconds:
        return "--:--:--"
    sign = "-" if seconds < 0 else "+"
    scale = 10 ** decimals
    total = int(round(abs(seconds) * scale))
    whole, frac = divmod(total, scale)
    h, rest = divmod(whole, 3600)
    m, s = divmod(rest, 60)
    out = f"{sign}{h}:{m:02d}:{s:02d}"
    if decimals:
        out += f".{frac:0{decimals}d}"
    return out


def parse_offset(text: str) -> float:
    """Versatz aus Text lesen: Sekunden („-12.5“, „90 s“, „1,5“) oder [-]H:MM:SS(.ms) bzw. [-]M:SS.

    Ein Komma gilt als Dezimaltrenner. Löst ``ValueError`` bei ungültiger Eingabe aus.
    """
    t = str(text).strip().replace(",", ".").replace("−", "-").replace(" ", "")
    if t.lower().endswith("s") and ":" not in t:
        t = t[:-1]
    if not t:
        raise ValueError("Leerer Versatz")
    sign = 1.0
    if t[0] in "+-":
        sign = -1.0 if t[0] == "-" else 1.0
        t = t[1:]
    if not t:
        raise ValueError("Leerer Versatz")
    parts = t.split(":")
    if len(parts) > 3:
        raise ValueError(f"Ungültiger Versatz: {text}")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise ValueError(f"Ungültiger Versatz: {text}") from None
    if any(n < 0 for n in nums) or any(p == "" for p in parts):
        raise ValueError(f"Ungültiger Versatz: {text}")
    if len(parts) > 1 and (nums[-1] >= 60 or (len(parts) == 3 and nums[1] >= 60)):
        raise ValueError(f"Minuten und Sekunden müssen unter 60 liegen: {text}")
    if len(parts) > 1 and any(not float(n).is_integer() for n in nums[:-1]):
        raise ValueError(f"Nur die Sekunden dürfen Nachkommastellen haben: {text}")
    value = 0.0
    for n in nums:
        value = value * 60 + n
    return sign * value
