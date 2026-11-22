"""Track-Datei laden: GPX oder FIT (erkannt am Inhalt, nicht nur an der Endung)."""

from __future__ import annotations

from os import PathLike
from pathlib import Path

from .fit_parser import parse_fit
from .gpx_parser import GPXParseError, parse_gpx
from .models import RawTrack

TRACK_SUFFIXES = {".gpx", ".fit"}


def is_fit(path: str | PathLike) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
    except OSError:
        return False
    return len(head) == 12 and head[0] in (12, 14) and head[8:12] == b".FIT"


def load_track(path: str | PathLike) -> RawTrack:
    p = Path(path)
    if not p.exists():
        raise GPXParseError(f"Datei nicht gefunden: {p}")
    return parse_fit(p) if is_fit(p) else parse_gpx(p)


def load_track_bytes(data: bytes, name: str = "track") -> RawTrack:
    """Track aus dem Speicher laden (z. B. aus einem Projektpaket)."""
    import io

    if len(data) >= 12 and data[0] in (12, 14) and data[8:12] == b".FIT":
        raw = parse_fit(io.BytesIO(data))
    else:
        raw = parse_gpx(io.BytesIO(data))
    raw.source_path = Path(name)
    return raw
