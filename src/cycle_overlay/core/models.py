"""Datenmodelle der Kern-Pipeline.

Interne Einheiten sind immer SI bzw. fest definiert:
Geschwindigkeit m/s, Distanz/Höhe/Höhenmeter m, VAM m/h, Steigung %,
Leistung W, Herzfrequenz bpm, Trittfrequenz rpm, Temperatur °C, Zeiten s.
Umrechnung für die Anzeige: siehe ``units.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path

import numpy as np

#: Sensor-Kanäle, wie sie der Parser liefert (NaN = kein Wert)
SENSOR_CHANNELS = ("heart_rate", "cadence", "power", "temperature", "speed_device")

#: Kanäle, die die Telemetrie nach der Verarbeitung enthält
TELEMETRY_CHANNELS = (
    "speed",
    "distance",
    "elevation",
    "gradient",
    "elevation_gain",
    "elevation_loss",
    "vam",
    "power",
    "heart_rate",
    "cadence",
    "temperature",
    "elapsed_time",
    "moving_time",
    "lat",
    "lon",
)


@dataclass
class RawTrack:
    """Rohdaten aus der GPX-Datei, bereinigt (Zeit streng steigend)."""

    start_time: datetime  # UTC, zeitzonenbewusst
    t: np.ndarray  # Sekunden seit start_time
    lat: np.ndarray
    lon: np.ndarray
    ele: np.ndarray  # NaN, wenn nicht vorhanden
    sensors: dict[str, np.ndarray]
    name: str | None = None
    creator: str | None = None
    source_path: Path | None = None
    warnings: list[str] = field(default_factory=list)
    #: False bei Dateien ohne GPS (z. B. Indoor-FIT); lat/lon sind dann Platzhalter
    has_position: bool = True

    @property
    def n_points(self) -> int:
        return int(self.t.size)

    @property
    def duration(self) -> float:
        return float(self.t[-1]) if self.t.size else 0.0

    @property
    def end_time(self) -> datetime:
        return self.start_time + timedelta(seconds=self.duration)


@dataclass
class SignalStats:
    min: float
    max: float
    mean: float
    coverage: float  # Anteil 0..1 der Fahrzeit (ohne Pausen) mit gültigem Wert


def interp_with_nan(x: np.ndarray, xp: np.ndarray, fp: np.ndarray) -> np.ndarray:
    """Lineare Interpolation; ist ein Nachbarwert NaN, ist das Ergebnis NaN.

    Außerhalb von ``xp`` wird NaN zurückgegeben.
    """
    x = np.asarray(x, dtype=float)
    nan_mask = np.isnan(fp)
    values = np.interp(x, xp, np.where(nan_mask, 0.0, fp), left=np.nan, right=np.nan)
    if nan_mask.any():
        m = np.interp(x, xp, nan_mask.astype(float), left=1.0, right=1.0)
        values[m > 1e-9] = np.nan
    return values


@dataclass
class Telemetry:
    """Aufbereitete Telemetrie auf einem 1-Hz-Raster."""

    start_time: datetime
    t: np.ndarray
    channels: dict[str, np.ndarray]
    available: dict[str, bool]
    stats: dict[str, SignalStats]
    pause_mask: np.ndarray
    speed_source: str  # "device" | "gps"
    min_speed_ms: float
    n_pauses: int = 0
    warnings: list[str] = field(default_factory=list)
    #: Zwischenspeicher für abgeleitete Daten (z. B. Fahrtrichtung der Minimap)
    cache: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def duration(self) -> float:
        return float(self.t[-1]) if self.t.size else 0.0

    @property
    def end_time(self) -> datetime:
        return self.start_time + timedelta(seconds=self.duration)

    def origin(self) -> tuple[float, float]:
        """Fester Bezugspunkt (Mittelpunkt der Strecke) für lokale Meter-Koordinaten."""
        if "origin" not in self.cache:
            lat, lon = self.channels["lat"], self.channels["lon"]
            ok = np.isfinite(lat) & np.isfinite(lon)
            self.cache["origin"] = (float(np.mean(lat[ok])), float(np.mean(lon[ok]))) if ok.any() else (0.0, 0.0)
        return self.cache["origin"]

    def absolute_time(self, t: float) -> datetime:
        """Track-Zeit (s) -> UTC-Zeitpunkt."""
        return self.start_time + timedelta(seconds=float(t))

    def sample(self, times: np.ndarray | list[float] | float) -> dict[str, np.ndarray]:
        """Werte für beliebige Zeitpunkte (z. B. jedes Video-Frame) linear interpolieren.

        Zeitpunkte außerhalb des Tracks liefern NaN.
        """
        times = np.atleast_1d(np.asarray(times, dtype=float))
        out = {name: interp_with_nan(times, self.t, arr) for name, arr in self.channels.items()}
        if "speed" in out:
            sp = out["speed"]
            out["speed"] = np.where(sp < self.min_speed_ms, 0.0, sp)
        return out

    def value_at(self, t: float) -> dict[str, float]:
        return {k: float(v[0]) for k, v in self.sample([t]).items()}


@dataclass
class VideoInfo:
    """Metadaten eines Videos aus ffprobe."""

    path: Path
    width: int  # Anzeige-Breite (Rotation berücksichtigt)
    height: int
    fps: Fraction  # exakte Framerate, z. B. 30000/1001
    duration: float  # Sekunden
    n_frames: int
    codec: str | None = None
    creation_time: datetime | None = None  # wie im Feld creation_time angegeben
    creation_time_raw: str | None = None
    apple_creation_date: datetime | None = None  # com.apple.quicktime.creationdate (mit Zeitzone)
    timecode: str | None = None
    rotation: int = 0
    make: str | None = None
    model: str | None = None
    handler_name: str | None = None
    camera: str = "generic"  # erkannte Kamera: gopro | dji | insta360 | apple | generic

    @property
    def fps_float(self) -> float:
        return float(self.fps)
