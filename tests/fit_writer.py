"""Minimaler FIT-Schreiber für Tests (erzeugt gültige FIT-Dateien aus einem RawTrack)."""

from __future__ import annotations

import math
import struct
from datetime import timezone

FIT_EPOCH_UNIX = 631065600  # 1989-12-31T00:00:00Z

_CRC_TABLE = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
              0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]


def crc16(data: bytes, crc: int = 0) -> int:
    for byte in data:
        tmp = _CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC_TABLE[byte & 0xF]
        tmp = _CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC_TABLE[(byte >> 4) & 0xF]
    return crc


# (Feldnummer, Größe, Basistyp, struct-Format, ungültig)
RECORD_FIELDS = {
    "timestamp": (253, 4, 0x86, "I", 0xFFFFFFFF),
    "lat": (0, 4, 0x85, "i", 0x7FFFFFFF),
    "lon": (1, 4, 0x85, "i", 0x7FFFFFFF),
    "enhanced_altitude": (78, 4, 0x86, "I", 0xFFFFFFFF),
    "heart_rate": (3, 1, 0x02, "B", 0xFF),
    "cadence": (4, 1, 0x02, "B", 0xFF),
    "distance": (5, 4, 0x86, "I", 0xFFFFFFFF),
    "enhanced_speed": (73, 4, 0x86, "I", 0xFFFFFFFF),
    "power": (7, 2, 0x84, "H", 0xFFFF),
    "temperature": (13, 1, 0x01, "b", 0x7F),
}


def write_fit(path, points: list[dict], *, big_endian: bool = False, compressed: bool = False,
              dev_field: bool = False, fields: list[str] | None = None) -> None:
    """``points``: Dicts mit unix_ts, lat, lon, alt, hr, cad, dist, speed, power, temp (None = fehlt)."""
    e = ">" if big_endian else "<"
    names = fields or ["timestamp", "lat", "lon", "enhanced_altitude", "heart_rate", "cadence",
                       "distance", "enhanced_speed", "power", "temperature"]
    if compressed:
        names = [n for n in names if n != "timestamp"]
    body = bytearray()
    # file_id (global 0): type=activity
    body += bytes([0x40, 0, 1 if big_endian else 0]) + struct.pack(e + "H", 0) + bytes([1, 0, 1, 0x00])
    body += bytes([0x00, 4])
    # Definition record (lokal 1)
    hdr = 0x40 | 0x20 if dev_field else 0x40
    body += bytes([hdr | 1, 0, 1 if big_endian else 0]) + struct.pack(e + "H", 20) + bytes([len(names)])
    for n in names:
        num, size, base, _, _ = RECORD_FIELDS[n]
        body += bytes([num, size, base])
    if dev_field:
        body += bytes([1, 0, 2, 0])  # 1 Developer-Feld, 2 Bytes
    # timestamp-Nachricht (global 253 "timestamp_correlation" vereinfacht: nutzen event 21) für komprimiert
    if compressed:
        body += bytes([0x40 | 2, 0, 1 if big_endian else 0]) + struct.pack(e + "H", 21) + bytes([1, 253, 4, 0x86])
    last_ts = None
    for p in points:
        ts = p["unix_ts"] - FIT_EPOCH_UNIX
        if compressed:
            if last_ts is None or ts - last_ts >= 32:
                body += bytes([2]) + struct.pack(e + "I", ts)
                last_ts = ts
            body += bytes([0x80 | (1 << 5) | (ts & 0x1F)])
            last_ts = ts
        else:
            body += bytes([1])
        for n in names:
            _, _, _, fmt, invalid = RECORD_FIELDS[n]
            v = {
                "timestamp": ts,
                "lat": None if p.get("lat") is None else int(round(p["lat"] / 180 * 2**31)),
                "lon": None if p.get("lon") is None else int(round(p["lon"] / 180 * 2**31)),
                "enhanced_altitude": None if p.get("alt") is None or math.isnan(p["alt"]) else int(round((p["alt"] + 500) * 5)),
                "heart_rate": p.get("hr"),
                "cadence": p.get("cad"),
                "distance": None if p.get("dist") is None else int(round(p["dist"] * 100)),
                "enhanced_speed": None if p.get("speed") is None else int(round(p["speed"] * 1000)),
                "power": p.get("power"),
                "temperature": p.get("temp"),
            }[n]
            if v is None or (isinstance(v, float) and math.isnan(v)):
                v = invalid
            body += struct.pack(e + fmt, int(v))
        if dev_field:
            body += b"\x12\x34"
    header = struct.pack("<BBHI4s", 14, 0x20, 2132, len(body), b".FIT")
    header += struct.pack("<H", crc16(header))
    data = header + bytes(body)
    data += struct.pack("<H", crc16(data))
    with open(path, "wb") as fh:
        fh.write(data)


def points_from_raw(raw, device_speed: float | None = None) -> list[dict]:
    import numpy as np

    from cycle_overlay.core.processor import haversine

    seg = haversine(raw.lat[:-1], raw.lon[:-1], raw.lat[1:], raw.lon[1:])
    dist = np.concatenate([[0.0], np.cumsum(seg)])
    start = int(raw.start_time.astimezone(timezone.utc).timestamp())
    pts = []
    for i in range(raw.n_points):
        def val(a):
            x = a[i]
            return None if math.isnan(x) else x
        pts.append({
            "unix_ts": start + int(raw.t[i]),
            "lat": float(raw.lat[i]), "lon": float(raw.lon[i]),
            "alt": float(raw.ele[i]),
            "hr": None if val(raw.sensors["heart_rate"]) is None else int(raw.sensors["heart_rate"][i]),
            "cad": None if val(raw.sensors["cadence"]) is None else int(raw.sensors["cadence"][i]),
            "power": None if val(raw.sensors["power"]) is None else int(raw.sensors["power"][i]),
            "temp": None if val(raw.sensors["temperature"]) is None else int(round(raw.sensors["temperature"][i])),
            "dist": float(dist[i]),
            "speed": device_speed,
        })
    return pts
