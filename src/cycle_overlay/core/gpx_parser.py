"""GPX-Import (PRD 3.1).

Liest GPX 1.0/1.1 mit Pythons eingebautem XML-Parser (``iterparse``, speicherschonend
auch bei großen Dateien). Extensions werden über den *lokalen* Tag-Namen erkannt,
da die Namespace-Präfixe je nach Exporter verschieden sind
(``gpxtpx:hr``, ``ns3:hr``, ``power`` direkt unter ``extensions`` usw.).
"""

from __future__ import annotations

import logging
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from os import PathLike
from pathlib import Path
from typing import IO

import numpy as np

from .models import SENSOR_CHANNELS, RawTrack

log = logging.getLogger(__name__)


class GPXParseError(ValueError):
    """Die Datei ist keine gültige oder verwendbare GPX-Datei."""


#: lokaler Tag-Name (klein geschrieben) -> Sensor-Kanal
_EXTENSION_TAGS: dict[str, str] = {
    "hr": "heart_rate",
    "heartrate": "heart_rate",
    "heart_rate": "heart_rate",
    "cad": "cadence",
    "cadence": "cadence",
    "power": "power",
    "watts": "power",
    "powerinwatts": "power",
    "atemp": "temperature",
    "temp": "temperature",
    "temperature": "temperature",
    "speed": "speed_device",
}

#: Plausibilitätsgrenzen; Werte außerhalb gelten als fehlend
_VALID_RANGE: dict[str, tuple[float, float]] = {
    "heart_rate": (1.0, 250.0),  # 0 bpm = Sensor verloren
    "cadence": (0.0, 250.0),
    "power": (0.0, 3000.0),
    "temperature": (-50.0, 70.0),
    "speed_device": (0.0, 40.0),  # m/s (144 km/h)
}

_TZ_NO_COLON = re.compile(r"([+-]\d{2})(\d{2})$")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1].lower()


def parse_time(text: str) -> datetime:
    """ISO-8601-Zeit lesen; ohne Zeitzone wird UTC angenommen. Ergebnis immer in UTC."""
    s = text.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    s = _TZ_NO_COLON.sub(r"\1:\2", s)
    # Bruchteile von Sekunden auf 6 Stellen normieren (manche Exporter schreiben 7–9)
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_float(text: str | None) -> float:
    if text is None:
        return math.nan
    try:
        return float(text.strip())
    except ValueError:
        return math.nan


def parse_gpx(source: str | PathLike | IO[bytes]) -> RawTrack:
    """GPX-Datei einlesen.

    ``source`` ist ein Dateipfad oder ein geöffnetes Binär-Dateiobjekt.
    """
    path = Path(source) if isinstance(source, (str, PathLike)) else None
    times: list[datetime] = []
    lats: list[float] = []
    lons: list[float] = []
    eles: list[float] = []
    sensors: dict[str, list[float]] = {c: [] for c in SENSOR_CHANNELS}
    name: str | None = None
    creator: str | None = None
    counters = [0, 0]  # [ohne Zeit, ungültige Koordinaten]
    stack: list[str] = []

    try:
        for event, elem in ET.iterparse(source, events=("start", "end")):
            tag = _local(elem.tag)
            if event == "start":
                stack.append(tag)
                if len(stack) == 1:
                    if tag != "gpx":
                        raise GPXParseError("Die Datei ist keine GPX-Datei (Wurzelelement ist nicht <gpx>).")
                    creator = elem.get("creator")
                continue

            # event == "end"
            parent = stack[-2] if len(stack) >= 2 else None
            if tag == "name" and name is None and parent in ("trk", "metadata") and elem.text:
                name = elem.text.strip() or None
            elif tag == "trkpt":
                _read_point(elem, times, lats, lons, eles, sensors, counters)
                elem.clear()
            stack.pop()
    except ET.ParseError as exc:
        raise GPXParseError(f"Die GPX-Datei ist beschädigt oder kein gültiges XML: {exc}") from exc
    except OSError as exc:
        raise GPXParseError(f"Die Datei konnte nicht gelesen werden: {exc}") from exc

    n_no_time, n_bad_coord = counters
    warnings: list[str] = []
    if n_no_time:
        warnings.append(f"{n_no_time} Trackpunkte ohne Zeitstempel wurden übersprungen.")
    if n_bad_coord:
        warnings.append(f"{n_bad_coord} Trackpunkte mit ungültigen Koordinaten wurden übersprungen.")
    if len(times) < 2:
        raise GPXParseError("Die GPX-Datei enthält weniger als zwei Trackpunkte mit Zeitstempel.")

    # Zeit muss streng steigen: doppelte oder rückwärts laufende Punkte verwerfen
    start = times[0]
    t_all = np.array([(t - start).total_seconds() for t in times])
    keep = np.zeros(t_all.size, dtype=bool)
    last = -math.inf
    for i, ti in enumerate(t_all):
        if ti > last:
            keep[i] = True
            last = ti
    n_dropped = int((~keep).sum())
    if n_dropped:
        warnings.append(f"{n_dropped} Trackpunkte mit doppeltem oder rückwärts laufendem Zeitstempel wurden entfernt.")

    sensor_arrays: dict[str, np.ndarray] = {}
    for ch, vals in sensors.items():
        arr = np.array(vals, dtype=float)[keep]
        lo, hi = _VALID_RANGE[ch]
        arr[(arr < lo) | (arr > hi)] = np.nan
        sensor_arrays[ch] = arr

    track = RawTrack(
        start_time=start,
        t=t_all[keep],
        lat=np.array(lats, dtype=float)[keep],
        lon=np.array(lons, dtype=float)[keep],
        ele=np.array(eles, dtype=float)[keep],
        sensors=sensor_arrays,
        name=name,
        creator=creator,
        source_path=path,
        warnings=warnings,
    )
    for w in warnings:
        log.warning("%s: %s", path or "GPX", w)
    return track


def _read_point(elem, times, lats, lons, eles, sensors, counters) -> None:
    lat = _to_float(elem.get("lat"))
    lon = _to_float(elem.get("lon"))
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        counters[1] += 1
        return
    time_val: datetime | None = None
    ele = math.nan
    values: dict[str, float] = {}
    for child in elem.iter():
        if child is elem:
            continue
        tag = _local(child.tag)
        if tag == "time" and child.text and time_val is None:
            try:
                time_val = parse_time(child.text)
            except ValueError:
                time_val = None
        elif tag == "ele" and math.isnan(ele):
            ele = _to_float(child.text)
        else:
            ch = _EXTENSION_TAGS.get(tag)
            if ch and ch not in values:
                v = _to_float(child.text)
                if not math.isnan(v):
                    values[ch] = v
    if time_val is None:
        counters[0] += 1
        return
    times.append(time_val)
    lats.append(lat)
    lons.append(lon)
    eles.append(ele)
    for ch in SENSOR_CHANNELS:
        sensors[ch].append(values.get(ch, math.nan))
