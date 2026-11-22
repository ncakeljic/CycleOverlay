"""Kartendaten aus OpenStreetMap für die Minimap (ohne Qt).

- Abfrage per Overpass-API, nur in einem Korridor um die Strecke (nicht das ganze Rechteck).
- Straßen werden nach Typ klassifiziert, dazu Wasser und Wald/Grünflächen.
- ``MapData`` ist kompakt als JSON speicherbar (Projektpaket, Zwischenspeicher).
- ``MapIndex`` projiziert die Daten in lokale Meter und sortiert sie in Kacheln,
  damit pro Frame nur das Sichtbare gezeichnet wird.

Lizenz: Daten © OpenStreetMap-Mitwirkende, ODbL – im Video ist ein Hinweis nötig.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import math
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np

from .. import __version__

log = logging.getLogger(__name__)

ATTRIBUTION = "© OpenStreetMap"
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",  # früher overpass.kumi.systems
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
#: Wartezeit auf eine Antwort. Overpass schickt erst etwas, wenn die ganze Abfrage berechnet ist –
#: bei einer langen Strecke am Stück kann das einige Minuten dauern.
REQUEST_TIMEOUT_S = 300.0
TOTAL_TIMEOUT_S = 900.0  # Obergrenze für den gesamten Download
SERVER_QUERY_TIMEOUT_S = 600  # Zeitlimit, das der Overpass-Server für die Abfrage gewährt
USER_AGENT = f"CycleOverlay/{__version__} (Desktop-App fuer Video-Overlays)"

#: Reihenfolge = Zeichenreihenfolge (unten -> oben)
CLASSES = ("green", "water", "waterline", "path", "cycle", "minor", "primary", "major")
AREA_CLASSES = {"green", "water"}

_HIGHWAY = {
    "motorway": "major", "trunk": "major", "motorway_link": "major", "trunk_link": "major",
    "primary": "primary", "secondary": "primary", "primary_link": "primary", "secondary_link": "primary",
    "tertiary": "minor", "tertiary_link": "minor", "unclassified": "minor", "residential": "minor",
    "living_street": "minor", "service": "minor", "road": "minor",
    "cycleway": "cycle",
    "track": "path", "path": "path", "footway": "path", "bridleway": "path", "pedestrian": "path",
    "steps": "path",
}


class MapDataError(RuntimeError):
    pass


@dataclass
class Feature:
    cls: str
    lat: np.ndarray
    lon: np.ndarray
    osm_id: int = 0

    @property
    def is_area(self) -> bool:
        return self.cls in AREA_CLASSES


@dataclass
class MapData:
    features: list[Feature] = field(default_factory=list)
    source: str = "OpenStreetMap (Overpass)"
    attribution: str = ATTRIBUTION
    fetched_at: str = ""
    buffer_m: float = 0.0
    #: Anteil der Strecke (0..1), in dessen Nähe Kartendaten vorhanden sind; None = unbekannt
    coverage: float | None = None

    def __len__(self) -> int:
        return len(self.features)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.features:
            out[f.cls] = out.get(f.cls, 0) + 1
        return out

    # -------------------------------------------------------------- JSON

    def to_json_bytes(self) -> bytes:
        d = {
            "format": "cycleoverlay-mapdata",
            "version": 1,
            "source": self.source,
            "attribution": self.attribution,
            "fetched_at": self.fetched_at,
            "buffer_m": self.buffer_m,
            "coverage": self.coverage,
            # Koordinaten als ganzzahlige Mikrograd (kompakt, ~0,1 m genau)
            "features": [
                [f.cls, np.round(f.lat * 1e6).astype(np.int64).tolist(), np.round(f.lon * 1e6).astype(np.int64).tolist()]
                for f in self.features
            ],
        }
        return json.dumps(d, separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_json_bytes(cls, data: bytes) -> "MapData":
        try:
            d = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MapDataError(f"Kartendaten sind beschädigt: {exc}") from exc
        if d.get("format") != "cycleoverlay-mapdata":
            raise MapDataError("Keine CycleOverlay-Kartendaten.")
        feats = [Feature(c, np.asarray(la, dtype=float) / 1e6, np.asarray(lo, dtype=float) / 1e6)
                 for c, la, lo in d.get("features", []) if c in CLASSES]
        return cls(feats, d.get("source", ""), d.get("attribution", ATTRIBUTION), d.get("fetched_at", ""),
                   float(d.get("buffer_m", 0)), d.get("coverage"))


# --------------------------------------------------------------------------- Abfrage


def simplify_track(lat: np.ndarray, lon: np.ndarray, max_points: int = 150, min_step_m: float = 300.0) -> list[tuple[float, float]]:
    """Streckenpunkte für die Korridor-Abfrage ausdünnen."""
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    ok = np.isfinite(lat) & np.isfinite(lon)
    lat, lon = lat[ok], lon[ok]
    if lat.size == 0:
        return []
    pts = [(float(lat[0]), float(lon[0]))]
    cos0 = math.cos(math.radians(float(lat[0])))
    for a, b in zip(lat[1:], lon[1:]):
        pa, pb = pts[-1]
        dist = math.hypot((a - pa) * 111_195, (b - pb) * 111_195 * cos0)
        if dist >= min_step_m:
            pts.append((float(a), float(b)))
    if (float(lat[-1]), float(lon[-1])) != pts[-1]:
        pts.append((float(lat[-1]), float(lon[-1])))
    if len(pts) > max_points:
        idx = np.linspace(0, len(pts) - 1, max_points).round().astype(int)
        pts = [pts[i] for i in idx]
    return pts


#: Wege, die in einer Fahrrad-Minimap nur Ballast sind (Gehsteige, Einfahrten, Parkplatzgassen …).
#: Sie werden in der App aussortiert – die Server-Abfrage bleibt bewusst einfach, weil komplexe
#: Filter bei den Overpass-Servern zu unvollständigen bzw. leeren Ergebnissen geführt haben.
_SKIP_HIGHWAY = {"steps", "elevator", "corridor", "proposed", "construction", "platform", "bus_stop",
                 "raceway", "abandoned", "razed"}
_SKIP_FOOTWAY = {"sidewalk", "crossing", "traffic_island"}
_SKIP_SERVICE = {"driveway", "parking_aisle", "drive-through", "emergency_access"}
_SKIP_ACCESS = {"private", "no"}

DEFAULT_BUFFER_M = 1000.0
#: Höchstzahl Punkte je around-Linie (bewährt; längere Linien werden auf mehrere Zeilen verteilt)
AROUND_MAX_POINTS = 40


def build_query(points: list[tuple[float, float]], buffer_m: float = DEFAULT_BUFFER_M,
                timeout_s: int = SERVER_QUERY_TIMEOUT_S) -> str:
    """Overpass-Abfrage (eine Anfrage): Straßen, Wasser und Wald im Korridor um die Strecke.

    Lange Strecken werden innerhalb derselben Abfrage auf mehrere around-Linien verteilt.
    """
    if not points:
        raise MapDataError("Keine Positionen für die Kartenabfrage.")
    if len(points) == 1:
        points = points * 2
    r = int(buffer_m)
    lines = []
    for part in split_chunks(points, AROUND_MAX_POINTS):
        if len(part) == 1:
            part = part * 2
        coords = ",".join(f"{a:.5f},{b:.5f}" for a, b in part)
        around = f"(around:{r},{coords})"
        lines += [
            f'  way["highway"]{around};',
            f'  way["natural"="water"]{around};',
            f'  way["waterway"~"^(river|canal|stream)$"]{around};',
            f'  way["natural"="wood"]{around};',
            f'  way["landuse"~"^(forest|grass|meadow|recreation_ground)$"]{around};',
            f'  way["leisure"="park"]{around};',
        ]
    return f"[out:json][timeout:{timeout_s}];\n(\n" + "\n".join(lines) + "\n);\nout geom qt;\n"


def classify(tags: dict) -> str | None:
    hw = tags.get("highway")
    if hw:
        if (hw in _SKIP_HIGHWAY or tags.get("footway") in _SKIP_FOOTWAY
                or tags.get("service") in _SKIP_SERVICE or tags.get("access") in _SKIP_ACCESS):
            return None
        return _HIGHWAY.get(hw)
    if tags.get("natural") == "water":
        return "water"
    if tags.get("waterway") in ("river", "canal", "stream"):
        return "waterline"
    if tags.get("natural") == "wood" or tags.get("landuse") in ("forest", "grass", "meadow", "recreation_ground") \
            or tags.get("leisure") == "park":
        return "green"
    return None


def parse_overpass(data: dict) -> MapData:
    feats: list[Feature] = []
    for el in data.get("elements", []):
        if el.get("type") != "way":
            continue
        cls = classify(el.get("tags") or {})
        geom = el.get("geometry") or []
        if cls is None or len(geom) < 2:
            continue
        lat = np.array([g["lat"] for g in geom if g], dtype=float)
        lon = np.array([g["lon"] for g in geom if g], dtype=float)
        if lat.size < 2:
            continue
        if cls in AREA_CLASSES and (lat.size < 4 or (lat[0], lon[0]) != (lat[-1], lon[-1])):
            if cls == "water":
                cls = "waterline"  # nicht geschlossene Wasserkante als Linie
            else:
                continue
        feats.append(Feature(cls, lat, lon, int(el.get("id", 0))))
    order = {c: i for i, c in enumerate(CLASSES)}
    feats.sort(key=lambda f: order[f.cls])
    return MapData(feats, fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))


def ssl_context() -> tuple[ssl.SSLContext, str]:
    """TLS-Kontext mit den Zertifikaten des Betriebssystems.

    Von uv installierte Python-Versionen finden auf macOS die System-Zertifikate oft nicht
    („CERTIFICATE_VERIFY_FAILED“). ``truststore`` nutzt direkt den Schlüsselbund (macOS) bzw.
    den Windows-Zertifikatspeicher – das funktioniert auch hinter Firmen-Proxys.
    """
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT), "System-Zertifikate (truststore)"
    except ImportError:
        pass
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where()), "certifi"
    except ImportError:
        return ssl.create_default_context(), "Python-Standard"


def _error_text(raw: bytes) -> str:
    """Fehlertext aus einer Overpass-Antwort (HTML oder JSON) herausziehen."""
    text = raw.decode("utf-8", errors="replace")
    try:
        d = json.loads(text)
        if isinstance(d, dict) and d.get("remark"):
            return str(d["remark"])
    except json.JSONDecodeError:
        pass
    m = re.findall(r"<p>.*?</p>", text, re.S)
    body = " ".join(m) if m else text
    body = re.sub(r"<[^>]+>", " ", body)
    return re.sub(r"\s+", " ", body).strip()[:300]


class TransientError(MapDataError):
    """Server überlastet – nach kurzer Pause erneut versuchen."""


class ServerTimeout(MapDataError):
    """Server antwortet nicht (Zeitüberschreitung) – nicht erneut warten."""


def _http_post(url: str, query: str, timeout: float) -> bytes:
    body = urllib.parse.urlencode({"data": query}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "User-Agent": USER_AGENT, "Accept-Encoding": "gzip", "Content-Type": "application/x-www-form-urlencoded"})
    host = urllib.parse.urlparse(url).netloc
    ctx, _ = ssl_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:  # noqa: S310 – feste https-URL
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return raw
    except urllib.error.HTTPError as exc:
        detail = _error_text(exc.read() or b"")
        if exc.code in (429, 502, 503, 504):
            raise TransientError(f"{host}: HTTP {exc.code} (Server ausgelastet) {detail}") from exc
        raise MapDataError(f"{host}: HTTP {exc.code} {detail}") from exc
    except ssl.SSLError as exc:
        raise MapDataError(f"{host}: TLS-/Zertifikatsfehler ({exc}). Ist 'truststore' installiert (uv sync)?") from exc
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            raise ServerTimeout(f"{host}: keine Antwort innerhalb von {timeout:.0f} s") from exc
        if isinstance(reason, ssl.SSLError):
            raise MapDataError(f"{host}: TLS-/Zertifikatsfehler ({reason}). Ist 'truststore' installiert (uv sync)?") from exc
        raise MapDataError(f"{host}: keine Verbindung ({reason})") from exc
    except TimeoutError as exc:
        raise ServerTimeout(f"{host}: keine Antwort innerhalb von {timeout:.0f} s") from exc
    except OSError as exc:
        raise MapDataError(f"{host}: Netzwerkfehler ({exc})") from exc


def cache_path(cache_dir: Path, query: str) -> Path:
    return cache_dir / f"osm_{hashlib.sha1(query.encode('utf-8')).hexdigest()[:16]}.json"


def split_chunks(points: list[tuple[float, float]], max_points: int = 40) -> list[list[tuple[float, float]]]:
    """Korridor in Abschnitte teilen (je ~12 km), die sich um einen Punkt überlappen."""
    if len(points) <= max_points:
        return [points]
    chunks = []
    i = 0
    while i < len(points) - 1:
        chunks.append(points[i : i + max_points])
        i += max_points - 1
    return chunks


class DownloadCancelled(MapDataError):
    pass


#: Fehlschlag nach so vielen Sekunden -> Server für diesen Download aufgeben
SLOW_FAILURE_S = 30.0
#: Zuletzt erfolgreicher Server (wird beim nächsten Download zuerst gefragt)
_last_good: dict[str, str] = {}


def _dump_response(label: str, host: str, query: str, raw: bytes) -> None:
    """Diagnose: Abfrage und Rohantwort speichern, wenn CYCLE_OVERLAY_OSM_DUMP auf einen Ordner zeigt."""
    folder = os.environ.get("CYCLE_OVERLAY_OSM_DUMP")
    if not folder:
        return
    try:
        d = Path(folder)
        d.mkdir(parents=True, exist_ok=True)
        stem = f"{time.strftime('%H%M%S')}_{re.sub(r'[^A-Za-z0-9]+', '_', label)[:30]}_{host}"
        (d / f"{stem}_query.txt").write_text(query, encoding="utf-8")
        (d / f"{stem}_response.json").write_bytes(raw)
    except OSError as exc:
        log.warning("Diagnose-Dump fehlgeschlagen: %s", exc)


def _fetch_chunk(query: str, post, timeout: float, progress, label: str, errors: list[str],
                 sleep=time.sleep, cancel=None, deadline: float | None = None, dead_servers: set | None = None) -> dict:
    dead_servers = dead_servers if dead_servers is not None else set()
    urls = sorted(OVERPASS_URLS, key=lambda u: u != _last_good.get("url"))  # bewährten Server zuerst
    for url in urls:
        host = urllib.parse.urlparse(url).netloc
        if host in dead_servers:
            continue
        for attempt in range(3):
            if cancel is not None and cancel.is_set():
                raise DownloadCancelled("Download abgebrochen.")
            if deadline is not None and time.monotonic() > deadline:
                raise MapDataError("Zeitlimit für den Kartendownload überschritten.\n\nDetails:\n• "
                                   + "\n• ".join(dict.fromkeys(errors or ["keine Antwort"])))
            if progress:
                progress(f"{label}: {host}" + (f" (Versuch {attempt + 1})" if attempt else "") + " …")
            t0 = time.monotonic()
            try:
                raw = post(url, query, timeout)
                try:
                    data = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise MapDataError(f"{host}: unerwartete Antwort – {_error_text(raw)}") from None
                if "elements" not in data:
                    raise MapDataError(f"{host}: {data.get('remark') or 'Antwort ohne Daten'}")
                if data.get("remark"):
                    # Overpass meldet Laufzeitfehler (Zeitlimit, Speicher) mit HTTP 200 und evtl.
                    # unvollständigen Daten – nie übernehmen, sondern erneut abfragen
                    raise TransientError(f"{host}: unvollständige Antwort – {data['remark']}")
                _dump_response(label, host, query, raw)
                if not data["elements"]:
                    # Für eine echte Strecke gibt es immer Wege – leer heißt: Server hat versagt
                    raise MapDataError(f"{host}: leere Antwort ({len(raw)} Bytes)")
                _last_good["url"] = url
                return data
            except ServerTimeout as exc:
                # Server antwortet gar nicht: nicht erneut warten, nächsten Server probieren
                log.warning("%s", exc)
                errors.append(str(exc))
                dead_servers.add(host)
                break
            except TransientError as exc:
                log.warning("%s", exc)
                errors.append(str(exc))
                if time.monotonic() - t0 > SLOW_FAILURE_S:
                    # Server hat lange gerechnet und dann abgebrochen – nicht noch zweimal warten
                    dead_servers.add(host)
                    break
                sleep(2.0 * (attempt + 1))
            except MapDataError as exc:
                log.warning("%s", exc)
                errors.append(str(exc))
                break  # dauerhafter Fehler bei diesem Server -> nächsten Server versuchen
            except (urllib.error.URLError, OSError) as exc:  # z. B. von einem Test-Ersatz für post()
                log.warning("%s: %s", host, exc)
                errors.append(f"{host}: {exc}")
                break
    raise MapDataError("Kartendaten konnten nicht geladen werden. Internetverbindung prüfen oder später "
                       "erneut versuchen – die Minimap zeigt bis dahin nur die Route.\n\nDetails:\n• "
                       + "\n• ".join(dict.fromkeys(errors)))


def route_coverage(features: list[Feature], lat: np.ndarray, lon: np.ndarray, radius_m: float = 300.0,
                   step_m: float = 250.0) -> tuple[float, np.ndarray]:
    """Anteil der Strecke mit Kartendaten in der Nähe; dazu eine Maske der Prüfpunkte ohne Daten."""
    pts = simplify_track(lat, lon, max_points=100_000, min_step_m=step_m)
    if not pts or not features:
        return 0.0, np.ones(len(pts), dtype=bool)
    lat0, lon0 = pts[0]
    cos0 = math.cos(math.radians(lat0))
    cell = radius_m
    grid: set[tuple[int, int]] = set()
    for f in features:
        fx = (f.lon - lon0) * 111_195 * cos0
        fy = (f.lat - lat0) * 111_195
        for cx, cy in zip(np.floor(fx / cell).astype(int), np.floor(fy / cell).astype(int)):
            grid.add((int(cx), int(cy)))
    missing = np.zeros(len(pts), dtype=bool)
    for k, (a, b) in enumerate(pts):
        cx = int(math.floor((b - lon0) * 111_195 * cos0 / cell))
        cy = int(math.floor((a - lat0) * 111_195 / cell))
        missing[k] = not any((cx + dx, cy + dy) in grid for dx in (-1, 0, 1) for dy in (-1, 0, 1))
    return float(1.0 - missing.mean()), missing


def fetch_map_data(lat: np.ndarray, lon: np.ndarray, *, buffer_m: float = DEFAULT_BUFFER_M,
                   cache_dir: Path | None = None, timeout: float = REQUEST_TIMEOUT_S,
                   post: Callable[[str, str, float], bytes] | None = None,
                   progress: Callable[[str], None] | None = None, sleep=time.sleep,
                   cancel=None, total_timeout: float = TOTAL_TIMEOUT_S, chunked: bool = False,
                   min_coverage: float = 0.97) -> MapData:
    """Kartendaten für die Umgebung der Strecke laden (mit Zwischenspeicher).

    Standard: **eine** Abfrage für die ganze Strecke. Liefert der Server darauf nichts oder nur
    einen Teil, wird automatisch abschnittsweise nachgeladen (das Ergebnis ist trotzdem eine
    vollständige Karte). ``chunked=True`` lädt gleich abschnittsweise.
    """
    kw = dict(buffer_m=buffer_m, cache_dir=cache_dir, timeout=timeout, post=post, progress=progress,
              sleep=sleep, cancel=cancel, min_coverage=min_coverage)
    t_end = time.monotonic() + total_timeout
    if chunked:
        return _fetch_map_data(lat, lon, chunked=True, total_timeout=total_timeout, **kw)
    first: MapData | None = None
    try:
        first = _fetch_map_data(lat, lon, chunked=False, total_timeout=total_timeout, **kw)
        if first.coverage is None or first.coverage >= min_coverage:
            return first
        msg = f"Am Stück nur {first.coverage * 100:.0f} % der Strecke – lade abschnittsweise nach …"
    except DownloadCancelled:
        raise
    except MapDataError as exc:
        if "Zeitlimit" in str(exc):
            raise
        log.warning("Abfrage am Stück fehlgeschlagen: %s", exc)
        msg = "Abfrage am Stück fehlgeschlagen – lade abschnittsweise …"
    if progress:
        progress(msg)
    remaining = max(t_end - time.monotonic(), 60.0)
    try:
        second = _fetch_map_data(lat, lon, chunked=True, total_timeout=remaining, **kw)
    except DownloadCancelled:
        raise
    except MapDataError:
        if first is not None:
            return first
        raise
    if first is not None and (first.coverage or 0) >= (second.coverage or 0):
        return first
    return second


def _fetch_map_data(lat: np.ndarray, lon: np.ndarray, *, buffer_m: float = DEFAULT_BUFFER_M,
                   cache_dir: Path | None = None, timeout: float = REQUEST_TIMEOUT_S,
                   post: Callable[[str, str, float], bytes] | None = None,
                   progress: Callable[[str], None] | None = None, sleep=time.sleep,
                   cancel=None, total_timeout: float = TOTAL_TIMEOUT_S, chunked: bool = False,
                   min_coverage: float = 0.97) -> MapData:
    """Kartendaten für die Umgebung der Strecke laden (mit Zwischenspeicher).

    Standard: **eine** Abfrage für die ganze Strecke. ``chunked=True`` teilt lange Strecken in
    Abschnitte (nur für sehr lange Touren, falls der Server die Abfrage am Stück ablehnt).
    """
    pts = simplify_track(lat, lon, max_points=400 if chunked else 250)
    chunks = split_chunks(pts) if chunked else [pts]
    queries = [build_query(c, buffer_m) for c in chunks]
    key = "\n".join(queries)
    if cache_dir is not None:
        cp = cache_path(cache_dir, key)
        if cp.exists():
            try:
                md = MapData.from_json_bytes(cp.read_bytes())
                if progress:
                    progress("Kartendaten aus dem Zwischenspeicher geladen")
                return md
            except MapDataError:
                cp.unlink(missing_ok=True)
    post = post or _http_post
    merged: dict[int, Feature] = {}
    anonymous: list[Feature] = []
    errors: list[str] = []
    deadline = time.monotonic() + total_timeout
    dead: set[str] = set()
    def add(data: dict) -> int:
        n = 0
        for f in parse_overpass(data).features:
            n += 1
            if f.osm_id:
                merged.setdefault(f.osm_id, f)
            else:
                anonymous.append(f)
        return n

    for k, q in enumerate(queries):
        label = f"Abschnitt {k + 1}/{len(queries)}" if len(queries) > 1 else \
            "Kartendaten für die ganze Strecke (kann einige Minuten dauern)"
        data = _fetch_chunk(q, post, timeout, progress, label, errors, sleep,
                            cancel=cancel, deadline=deadline, dead_servers=dead)
        n = add(data)
        log.info("%s: %d Elemente", label, n)
        if progress:
            progress(f"{label}: {n} Elemente")

    # Abdeckung prüfen und Abschnitte ohne Daten einmal nachladen
    coverage, missing = route_coverage(list(merged.values()) + anonymous, lat, lon)
    if coverage < min_coverage and len(chunks) > 1:
        miss_pts = [p for p, m in zip(simplify_track(lat, lon, max_points=100_000, min_step_m=250.0), missing) if m]
        redo = sorted({k for k, c in enumerate(chunks) for mp in miss_pts
                       if min(abs(mp[0] - a) + abs(mp[1] - b) for a, b in c) < 0.01})
        for k in redo:
            if progress:
                progress(f"Lücke in Abschnitt {k + 1} – lade nach …")
            try:
                add(_fetch_chunk(queries[k], post, timeout, progress, f"Nachladen {k + 1}", errors, sleep,
                                 cancel=cancel, deadline=deadline, dead_servers=dead))
            except DownloadCancelled:
                raise
            except MapDataError as exc:
                log.warning("Nachladen von Abschnitt %d fehlgeschlagen: %s", k + 1, exc)
        coverage, _ = route_coverage(list(merged.values()) + anonymous, lat, lon)
    if not merged and not anonymous:
        raise MapDataError("Der Kartenserver hat für die Strecke keine Daten geliefert. Bitte später erneut "
                           "versuchen.\n\nDetails:\n• " + "\n• ".join(dict.fromkeys(errors or ["leere Antwort"])))
    order = {c: i for i, c in enumerate(CLASSES)}
    feats = sorted(list(merged.values()) + anonymous, key=lambda f: order[f.cls])
    md = MapData(feats, fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), buffer_m=buffer_m,
                 coverage=coverage)
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path(cache_dir, key).write_bytes(md.to_json_bytes())
    if progress:
        progress(f"{len(md)} Kartenelemente geladen, Abdeckung der Strecke {coverage * 100:.0f} %")
    return md


def check_servers(timeout: float = 10.0) -> list[str]:
    """Erreichbarkeit der Overpass-Server prüfen (parallel, für die Diagnose)."""
    from concurrent.futures import ThreadPoolExecutor

    ctx, _ = ssl_context()

    def one(url: str) -> str:
        # Die /status-Seiten der Server sind unzuverlässig – daher eine winzige echte Abfrage
        host = urllib.parse.urlparse(url).netloc
        t0 = time.monotonic()
        try:
            body = urllib.parse.urlencode({"data": "[out:json][timeout:10];node(1);out ids;"}).encode()
            req = urllib.request.Request(url, data=body, method="POST", headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:  # noqa: S310
                ok = b"elements" in resp.read(2000)
            return f"{host}: {'antwortet' if ok else 'unerwartete Antwort'} ({time.monotonic() - t0:.1f} s)"
        except Exception as exc:  # noqa: BLE001 – Diagnose
            return f"{host}: keine brauchbare Antwort nach {time.monotonic() - t0:.1f} s – {type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(len(OVERPASS_URLS)) as ex:
        return list(ex.map(one, OVERPASS_URLS))


def network_check(timeout: float = 12.0) -> list[str]:
    """Verschiedene Verbindungswege vergleichen, um die Ursache von Download-Problemen zu finden."""
    import shutil
    import socket
    import subprocess
    from concurrent.futures import ThreadPoolExecutor

    from ..platform_utils import subprocess_kwargs

    ctx_trust, name = ssl_context()
    ctx_plain = ssl.create_default_context()
    try:
        import certifi

        ctx_certifi = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx_certifi = None

    def get(url: str, ctx, ipv4: bool = False) -> str:
        t0 = time.monotonic()
        orig = socket.getaddrinfo
        try:
            if ipv4:
                socket.getaddrinfo = lambda *a, **k: [r for r in orig(*a, **k) if r[0] == socket.AF_INET]
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:  # noqa: S310
                resp.read(200)
                return f"OK {resp.status} ({time.monotonic() - t0:.1f} s)"
        except Exception as exc:  # noqa: BLE001
            return f"FEHLER nach {time.monotonic() - t0:.1f} s: {type(exc).__name__}: {exc}"
        finally:
            socket.getaddrinfo = orig

    def curl(url: str) -> str:
        exe = shutil.which("curl")
        if not exe:
            return "curl nicht vorhanden"
        t0 = time.monotonic()
        try:
            r = subprocess.run([exe, "-sS", "-o", "/dev/null", "-m", str(int(timeout)), "-w", "%{http_code} %{remote_ip}", url],
                               capture_output=True, text=True, timeout=timeout + 5, **subprocess_kwargs())
            out = (r.stdout or "").strip()
            return (f"OK HTTP {out} ({time.monotonic() - t0:.1f} s)" if r.returncode == 0
                    else f"FEHLER ({time.monotonic() - t0:.1f} s): {r.stderr.strip()[:150]} {out}")
        except Exception as exc:  # noqa: BLE001
            return f"FEHLER: {exc}"

    def resolve(host: str) -> str:
        try:
            infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
            fams = sorted({"IPv6" if i[0] == socket.AF_INET6 else "IPv4" for i in infos})
            ips = sorted({i[4][0] for i in infos})[:4]
            return f"{', '.join(fams)}: {', '.join(ips)}"
        except OSError as exc:
            return f"DNS-FEHLER: {exc}"

    status = "https://overpass-api.de/api/status"
    tasks = {
        "DNS overpass-api.de": lambda: resolve("overpass-api.de"),
        "curl overpass-api.de/status": lambda: curl(status),
        "curl -4 (nur IPv4)": lambda: curl4(),
        f"Python ({name})": lambda: get(status, ctx_trust),
        "Python (Standard-Zertifikate)": lambda: get(status, ctx_plain),
        "Python (certifi)": lambda: get(status, ctx_certifi) if ctx_certifi else "certifi nicht installiert",
        "Python nur IPv4": lambda: get(status, ctx_trust, ipv4=True),
        "Python private.coffee": lambda: get("https://overpass.private.coffee/api/status", ctx_trust),
        "Python maps.mail.ru": lambda: get("https://maps.mail.ru/osm/tools/overpass/api/status", ctx_trust),
        "Python openstreetmap.org": lambda: get("https://www.openstreetmap.org/robots.txt", ctx_trust),
        "Python example.com": lambda: get("https://example.com/", ctx_trust),
    }

    def curl4() -> str:
        exe = shutil.which("curl")
        if not exe:
            return "curl nicht vorhanden"
        t0 = time.monotonic()
        r = subprocess.run([exe, "-4", "-sS", "-o", "/dev/null", "-m", str(int(timeout)), "-w", "%{http_code}", status],
                           capture_output=True, text=True, timeout=timeout + 5, **subprocess_kwargs())
        return (f"OK HTTP {r.stdout.strip()} ({time.monotonic() - t0:.1f} s)" if r.returncode == 0
                else f"FEHLER ({time.monotonic() - t0:.1f} s): {r.stderr.strip()[:150]}")

    with ThreadPoolExecutor(len(tasks)) as ex:
        futures = {k: ex.submit(fn) for k, fn in tasks.items()}
        return [f"{k:<32} {f.result()}" for k, f in futures.items()]


# --------------------------------------------------------------------------- Kachelindex


@dataclass
class ProjectedFeature:
    cls: str
    x: np.ndarray
    y: np.ndarray
    bbox: tuple[float, float, float, float]

    @property
    def is_area(self) -> bool:
        return self.cls in AREA_CLASSES


class MapIndex:
    """Kartendaten in lokalen Metern, in Kacheln sortiert."""

    def __init__(self, data: MapData, lat0: float, lon0: float, cell_m: float = 250.0):
        from ..rendering.minimap_geometry import project_local

        self.data = data
        self.origin = (lat0, lon0)
        self.cell = cell_m
        self.features: list[ProjectedFeature] = []
        self.grid: dict[tuple[int, int], list[int]] = {}
        self.cache: dict = {}  # z. B. vorbereitete Qt-Pfade
        for f in data.features:
            x, y = project_local(f.lat, f.lon, lat0, lon0)
            bb = (float(x.min()), float(y.min()), float(x.max()), float(y.max()))
            k = len(self.features)
            self.features.append(ProjectedFeature(f.cls, x, y, bb))
            for cx in range(int(math.floor(bb[0] / cell_m)), int(math.floor(bb[2] / cell_m)) + 1):
                for cy in range(int(math.floor(bb[1] / cell_m)), int(math.floor(bb[3] / cell_m)) + 1):
                    self.grid.setdefault((cx, cy), []).append(k)

    def query(self, bbox: tuple[float, float, float, float]) -> list[int]:
        """Indizes der Features, die den Bereich berühren können (in Zeichenreihenfolge)."""
        x0, y0, x1, y1 = bbox
        found: set[int] = set()
        c = self.cell
        cx0, cx1 = int(math.floor(x0 / c)), int(math.floor(x1 / c))
        cy0, cy1 = int(math.floor(y0 / c)), int(math.floor(y1 / c))
        if (cx1 - cx0 + 1) * (cy1 - cy0 + 1) > 40_000:  # absurd großer Bereich
            return list(range(len(self.features)))
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                found.update(self.grid.get((cx, cy), ()))
        return sorted(found)
