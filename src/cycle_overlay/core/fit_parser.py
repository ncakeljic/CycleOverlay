"""FIT-Import (Garmin/Wahoo/Hammerhead), ohne externe Bibliothek.

Gelesen werden die ``record``-Nachrichten (globale Nachricht 20): Zeit, Position,
Höhe, Herzfrequenz, Trittfrequenz, Distanz, Geschwindigkeit, Leistung, Temperatur.
Unterstützt: beide Byte-Reihenfolgen, komprimierte Zeitstempel, Developer-Felder
(werden übersprungen), Array-Felder, mehrere aneinandergehängte FIT-Dateien.

Anders als GPX enthält FIT meist die Geschwindigkeit und Distanz des Geräts
(Radsensor oder gefilterte GPS-Werte) – diese werden bevorzugt verwendet.
"""

from __future__ import annotations

import math
import struct
from datetime import datetime, timedelta, timezone
from os import PathLike
from pathlib import Path
from typing import IO

import numpy as np

from .gpx_parser import GPXParseError
from .models import SENSOR_CHANNELS, RawTrack

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc)
SEMICIRCLE = 180.0 / 2**31

MESG_RECORD = 20
MESG_SESSION = 18

# Basistypen: Code -> (struct-Format, Größe, ungültiger Wert)
_BASE: dict[int, tuple[str, int, int | float | None]] = {
    0x00: ("B", 1, 0xFF),  # enum
    0x01: ("b", 1, 0x7F),  # sint8
    0x02: ("B", 1, 0xFF),  # uint8
    0x83: ("h", 2, 0x7FFF),
    0x84: ("H", 2, 0xFFFF),
    0x85: ("i", 4, 0x7FFFFFFF),
    0x86: ("I", 4, 0xFFFFFFFF),
    0x07: ("s", 1, None),  # string
    0x88: ("f", 4, None),
    0x89: ("d", 8, None),
    0x0A: ("B", 1, 0x00),  # uint8z
    0x8B: ("H", 2, 0x0000),
    0x8C: ("I", 4, 0x00000000),
    0x0D: ("B", 1, None),  # byte
    0x8E: ("q", 8, 0x7FFFFFFFFFFFFFFF),
    0x8F: ("Q", 8, 0xFFFFFFFFFFFFFFFF),
    0x90: ("Q", 8, 0),
}

# record-Felder: Feldnummer -> (Name, Skalierung, Offset)
_RECORD_FIELDS: dict[int, tuple[str, float, float]] = {
    253: ("timestamp", 1, 0),
    0: ("lat", 1, 0),
    1: ("lon", 1, 0),
    2: ("altitude", 5, 500),
    78: ("enhanced_altitude", 5, 500),
    3: ("heart_rate", 1, 0),
    4: ("cadence", 1, 0),
    5: ("distance", 100, 0),
    6: ("speed", 1000, 0),
    73: ("enhanced_speed", 1000, 0),
    7: ("power", 1, 0),
    13: ("temperature", 1, 0),
}


class FITParseError(GPXParseError):
    """Die Datei ist keine gültige oder verwendbare FIT-Datei."""


def fit_time(ts: int) -> datetime:
    return FIT_EPOCH + timedelta(seconds=ts)


def _read_records(data: bytes) -> tuple[list[dict], str | None]:
    records: list[dict] = []
    sport_name: str | None = None
    pos = 0
    n = len(data)
    while pos + 12 <= n:
        hsize = data[pos]
        if hsize not in (12, 14) or data[pos + 8 : pos + 12] != b".FIT":
            if not records and pos == 0:
                raise FITParseError("Die Datei ist keine FIT-Datei (Kopf fehlt).")
            break
        data_size = struct.unpack_from("<I", data, pos + 4)[0]
        start = pos + hsize
        end = min(start + data_size, n)
        _parse_block(data, start, end, records)
        pos = end + 2  # CRC am Ende jeder Datei
    return records, sport_name


def _parse_block(data: bytes, pos: int, end: int, records: list[dict]) -> None:
    defs: dict[int, dict] = {}
    last_ts: int | None = None
    while pos < end:
        header = data[pos]
        pos += 1
        if header & 0x80:  # komprimierter Zeitstempel
            local = (header >> 5) & 0x03
            offset = header & 0x1F
            if last_ts is not None:
                ts = (last_ts & ~0x1F) + offset
                if offset < (last_ts & 0x1F):
                    ts += 0x20
                last_ts = ts
            d = defs.get(local)
            if d is None:
                raise FITParseError("Ungültige FIT-Datei (Daten ohne Definition).")
            pos, msg = _read_message(data, pos, d)
            if d["global"] == MESG_RECORD:
                if last_ts is not None and "timestamp" not in msg:
                    msg["timestamp"] = last_ts
                records.append(msg)
            continue
        local = header & 0x0F
        if header & 0x40:  # Definition
            arch = data[pos + 1]
            endian = ">" if arch == 1 else "<"
            gmsg = struct.unpack_from(endian + "H", data, pos + 2)[0]
            nfields = data[pos + 4]
            pos += 5
            fields = []
            for _ in range(nfields):
                num, size, base = data[pos], data[pos + 1], data[pos + 2]
                fields.append((num, size, base))
                pos += 3
            dev_size = 0
            if header & 0x20:
                ndev = data[pos]
                pos += 1
                for _ in range(ndev):
                    dev_size += data[pos + 1]
                    pos += 3
            defs[local] = {"global": gmsg, "endian": endian, "fields": fields, "dev_size": dev_size}
        else:
            d = defs.get(local)
            if d is None:
                raise FITParseError("Ungültige FIT-Datei (Daten ohne Definition).")
            pos, msg = _read_message(data, pos, d)
            if "timestamp" in msg:
                last_ts = msg["timestamp"]
            if d["global"] == MESG_RECORD:
                records.append(msg)


def _read_message(data: bytes, pos: int, d: dict) -> tuple[int, dict]:
    msg: dict = {}
    is_record = d["global"] == MESG_RECORD
    for num, size, base in d["fields"]:
        if is_record and num in _RECORD_FIELDS:
            spec = _BASE.get(base) or _BASE.get(base | 0x80) or _BASE.get(base & 0x7F)
            if spec is not None and spec[0] != "s" and size >= spec[1]:
                fmt, bsize, invalid = spec
                val = struct.unpack_from(d["endian"] + fmt, data, pos)[0]
                if invalid is None or val != invalid:
                    name, scale, offset = _RECORD_FIELDS[num]
                    msg[name] = val if name in ("timestamp", "lat", "lon") else val / scale - offset
        elif num == 253 and size == 4:  # Zeitstempel auch anderer Nachrichten für komprimierte Zeiten
            ts = struct.unpack_from(d["endian"] + "I", data, pos)[0]
            if ts != 0xFFFFFFFF:
                msg["timestamp"] = ts
        pos += size
    pos += d["dev_size"]
    return pos, msg


def parse_fit(source: str | PathLike | IO[bytes]) -> RawTrack:
    path = Path(source) if isinstance(source, (str, PathLike)) else None
    try:
        data = path.read_bytes() if path is not None else source.read()  # type: ignore[union-attr]
    except OSError as exc:
        raise FITParseError(f"Die Datei konnte nicht gelesen werden: {exc}") from exc
    try:
        records, _ = _read_records(data)
    except (struct.error, IndexError) as exc:
        raise FITParseError("Die FIT-Datei ist beschädigt oder unvollständig.") from exc

    recs = [r for r in records if "timestamp" in r]
    if len(recs) < 2:
        raise FITParseError("Die FIT-Datei enthält weniger als zwei Datenpunkte mit Zeit.")
    warnings: list[str] = []

    ts = np.array([r["timestamp"] for r in recs], dtype=float)
    keep = np.zeros(ts.size, dtype=bool)
    last = -math.inf
    for i, t in enumerate(ts):
        if t > last:
            keep[i] = True
            last = t
    if (~keep).sum():
        warnings.append(f"{int((~keep).sum())} Datenpunkte mit doppelter Zeit wurden entfernt.")
    recs = [r for r, k in zip(recs, keep) if k]
    ts = ts[keep]

    def col(*names: str) -> np.ndarray:
        out = np.full(len(recs), np.nan)
        for i, r in enumerate(recs):
            for nm in names:
                if nm in r:
                    out[i] = r[nm]
                    break
        return out

    lat = col("lat") * SEMICIRCLE
    lon = col("lon") * SEMICIRCLE
    bad = (np.abs(lat) > 90) | (np.abs(lon) > 180)
    lat[bad] = np.nan
    lon[bad] = np.nan
    has_pos = np.isfinite(lat).sum() >= 2
    if has_pos:
        # kurze GPS-Lücken (z. B. Tunnel) überbrücken
        ok = np.isfinite(lat)
        lat = np.interp(ts, ts[ok], lat[ok])
        lon = np.interp(ts, ts[ok], lon[ok])
    else:
        warnings.append("Die Datei enthält keine GPS-Positionen (z. B. Indoor-Fahrt) – keine Streckenkarte.")
        lat = np.zeros(ts.size)
        lon = np.zeros(ts.size)

    sensors = {
        "heart_rate": col("heart_rate"),
        "cadence": col("cadence"),
        "power": col("power"),
        "temperature": col("temperature"),
        "speed_device": col("enhanced_speed", "speed"),
        "distance_device": col("distance"),
    }
    limits = {"heart_rate": (1, 250), "cadence": (0, 250), "power": (0, 3000), "temperature": (-50, 70),
              "speed_device": (0, 40)}
    for ch, (lo, hi) in limits.items():
        a = sensors[ch]
        a[(a < lo) | (a > hi)] = np.nan
    for ch in SENSOR_CHANNELS:
        sensors.setdefault(ch, np.full(ts.size, np.nan))

    return RawTrack(
        start_time=fit_time(int(ts[0])),
        t=ts - ts[0],
        lat=lat,
        lon=lon,
        ele=col("enhanced_altitude", "altitude"),
        sensors=sensors,
        name=path.stem if path else None,
        creator="FIT",
        source_path=path,
        warnings=warnings,
        has_position=bool(has_pos),
    )
