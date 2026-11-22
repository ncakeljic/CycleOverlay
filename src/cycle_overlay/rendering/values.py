"""Werte für jedes Frame aufbereiten (ohne Qt).

- Bogen/Balken verwenden den flüssig interpolierten Wert jedes Frames.
- Zahlen im Text werden nur mit ``update_rate_hz`` aktualisiert (gegen Flackern, PRD 3.4).
- Umrechnung in Anzeige-Einheiten, Nachkommastellen, Bereiche und Farbzonen.
"""

from __future__ import annotations

import math
from datetime import timedelta
from zoneinfo import ZoneInfo

import numpy as np

from .i18n import DEFAULT_LANGUAGE, channel_text
from ..core.models import Telemetry
from ..core.units import CHANNELS, IMPERIAL, METRIC, convert, format_duration, unit_for
from .layout import MAP_CHANNEL, WidgetConfig

CLOCK_CHANNEL = "time_of_day"
R_EARTH = 6_371_000.0
MAX_ROUTE_POINTS = 1500

#: Standardbereiche für Rundinstrument/Balken (metrisch, imperial)
DEFAULT_RANGES: dict[str, dict[str, tuple[float, float]]] = {
    "speed": {"metric": (0, 70), "imperial": (0, 45)},
    "power": {"metric": (0, 600), "imperial": (0, 600)},
    "heart_rate": {"metric": (60, 200), "imperial": (60, 200)},
    "cadence": {"metric": (0, 130), "imperial": (0, 130)},
    "gradient": {"metric": (-15, 15), "imperial": (-15, 15)},
    "temperature": {"metric": (-10, 40), "imperial": (14, 104)},
    "vam": {"metric": (0, 2000), "imperial": (0, 6500)},
}


def channel_label(channel: str, language: str = DEFAULT_LANGUAGE) -> str:
    """Standard-Beschriftung eines Kanals in der Overlay-Sprache (``rendering.i18n``)."""
    if channel == CLOCK_CHANNEL:
        return channel_text("clock", language)
    if channel == MAP_CHANNEL:
        return channel_text("map", language)
    CHANNELS[channel]  # unbekannter Kanal -> KeyError wie bisher
    return channel_text(channel, language)


def format_number(value: float, decimals: int) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "--"
    if decimals <= 0:
        # -0 vermeiden
        v = int(round(value))
        return str(0 if v == 0 else v)
    s = f"{value:.{decimals}f}"
    return s[1:] if s.startswith("-") and float(s) == 0 else s


class FrameValues:
    def __init__(self, telemetry: Telemetry, track_times: np.ndarray, units: str = METRIC, tz: str | None = None,
                 t0: float | None = None, clip: tuple[float, float] | None = None, map_data=None,
                 count_numbers: bool = True):
        """``t0``: Bezugszeit für die Text-Aktualisierung (Clip-Beginn). Standard: erstes Frame.
        Für die Vorschau einzelner Frames muss der Clip-Beginn übergeben werden, damit die
        Zahlen genau dann wechseln wie im Export."""
        self.tel = telemetry
        self.times = np.asarray(track_times, dtype=float)
        self.t0 = float(t0) if t0 is not None else (float(self.times[0]) if self.times.size else 0.0)
        self.units = units
        #: Sprache der Overlay-Texte; setzt der CanvasRenderer aus ``Layout.language``
        self.language = DEFAULT_LANGUAGE
        self.tz = ZoneInfo(tz) if tz else None
        #: Clip-Bereich (Track-Zeit) – für Karte/Profil mit Bereich "clip"
        self.clip = clip if clip is not None else (
            (float(self.times[0]), float(self.times[-1])) if self.times.size else (0.0, 0.0))
        self._smooth = telemetry.sample(self.times) if self.times.size else {}
        self._quant: dict[float, tuple[np.ndarray, dict[str, np.ndarray]]] = {}
        self._routes: dict[str, dict[str, np.ndarray]] = {}
        #: OpenStreetMap-Daten für die Minimap (``core.osm.MapData``) oder None
        self.map_data = map_data
        #: Zahlen zählen zwischen zwei Aktualisierungen durch (keine Sprünge, z. B. 20→30 km/h zeigt 21, 22 …)
        self.count_numbers = count_numbers
        self._counted: dict[float, tuple[np.ndarray, dict[str, np.ndarray]]] = {}

    @property
    def n_frames(self) -> int:
        return int(self.times.size)

    # ------------------------------------------------------------------ Grundwerte

    def is_available(self, channel: str) -> bool:
        return channel == CLOCK_CHANNEL or bool(self.tel.available.get(channel))

    def track_time(self, i: int) -> float:
        return float(self.times[i])

    # ------------------------------------------------------------------ Karte & Profil

    def window(self, scope: str) -> tuple[float, float]:
        """Zeitfenster (Track-Zeit) für Karte/Profil."""
        if scope == "clip":
            a, b = self.clip
            margin = max((b - a) * 0.15, 30.0)
            return max(a - margin, 0.0), min(b + margin, self.tel.duration)
        return 0.0, self.tel.duration

    def route(self, scope: str) -> dict[str, np.ndarray]:
        """Ausgedünnte Streckendaten im Fenster: t, x/y (Meter, Norden oben), Distanz, alle Kanäle."""
        if scope in self._routes:
            return self._routes[scope]
        a, b = self.window(scope)
        t = self.tel.t
        i0 = int(np.searchsorted(t, a, side="left"))
        i1 = int(np.searchsorted(t, b, side="right"))
        idx = np.arange(i0, max(i1, i0 + 1))
        step = max(1, idx.size // MAX_ROUTE_POINTS)
        idx = idx[::step]
        if idx[-1] != min(i1, t.size) - 1 and i1 - 1 < t.size:
            idx = np.append(idx, min(i1, t.size) - 1)
        lat = self.tel.channels["lat"][idx]
        lon = self.tel.channels["lon"][idx]
        lat0 = float(np.nanmean(lat)) if np.isfinite(lat).any() else 0.0
        lon0 = float(np.nanmean(lon)) if np.isfinite(lon).any() else 0.0
        route = {
            "idx": idx,
            "t": t[idx],
            "x": np.radians(lon - lon0) * R_EARTH * math.cos(math.radians(lat0)),
            "y": np.radians(lat - lat0) * R_EARTH,
            "lat0": np.array([lat0]),
            "lon0": np.array([lon0]),
        }
        for ch, arr in self.tel.channels.items():
            route[ch] = arr[idx]
        self._routes[scope] = route
        return route

    # ------------------------------------------------------------------ Minimap

    def world_route(self) -> tuple[np.ndarray, np.ndarray]:
        """Ganze Strecke in lokalen Metern (1-Hz-Raster) um den festen Ursprung."""
        from .minimap_geometry import project_local

        cache = self.tel.cache
        if "world_route" not in cache:
            lat0, lon0 = self.tel.origin()
            cache["world_route"] = project_local(self.tel.channels["lat"], self.tel.channels["lon"], lat0, lon0)
        return cache["world_route"]

    def heading_track(self, window_m: float) -> np.ndarray:
        from .minimap_geometry import compute_heading

        key = ("heading", round(window_m, 1))
        cache = self.tel.cache
        if key not in cache:
            x, y = self.world_route()
            cache[key] = compute_heading(x, y, self.tel.channels["distance"], window_m)
        return cache[key]

    def zoom_track(self, base_radius: float) -> np.ndarray:
        from .minimap_geometry import auto_zoom_radius

        key = ("zoom", round(base_radius, 1))
        cache = self.tel.cache
        if key not in cache:
            cache[key] = auto_zoom_radius(self.tel.channels["speed"], base_radius)
        return cache[key]

    def world_position(self, i: int) -> tuple[float, float]:
        from .minimap_geometry import project_local

        lat0, lon0 = self.tel.origin()
        x, y = project_local([self.raw("lat", i)], [self.raw("lon", i)], lat0, lon0)
        return float(x[0]), float(y[0])

    def heading(self, i: int, window_m: float = 25.0) -> float:
        from .minimap_geometry import heading_at

        return float(heading_at(self.track_time(i), self.tel.t, self.heading_track(window_m)))

    def minimap_camera(self, w: WidgetConfig, i: int, rect: tuple[float, float, float, float]):
        from .minimap_geometry import Camera

        mm = w.minimap
        t = min(max(self.track_time(i), 0.0), self.tel.duration)
        radius = float(np.interp(t, self.tel.t, self.zoom_track(mm.radius_m))) if mm.auto_zoom else mm.radius_m
        tilt = mm.tilt_deg if mm.view == "tilt" else 0.0
        cx, cy = self.world_position(i)
        return Camera(cx, cy, self.heading(i, mm.heading_window_m), mm.orientation == "heading", radius, rect,
                      tilt_deg=tilt, anchor=0.66 if tilt > 0 else 0.5)

    def map_index(self):
        """Kartendaten als Kachelindex (zwischengespeichert an den Kartendaten)."""
        if self.map_data is None or len(self.map_data) == 0:
            return None
        from ..core.osm import MapIndex

        lat0, lon0 = self.tel.origin()
        key = ("index", round(lat0, 7), round(lon0, 7))
        store = self.map_data.__dict__.setdefault("_indexes", {})
        if key not in store:
            store.clear()
            store[key] = MapIndex(self.map_data, lat0, lon0)
        return store[key]

    def position_xy(self, scope: str, i: int) -> tuple[float, float]:
        r = self.route(scope)
        lat, lon = self.raw("lat", i), self.raw("lon", i)
        x = math.radians(lon - float(r["lon0"][0])) * R_EARTH * math.cos(math.radians(float(r["lat0"][0])))
        y = math.radians(lat - float(r["lat0"][0])) * R_EARTH
        return x, y

    def _quantized(self, rate_hz: float) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        if rate_hz <= 0:
            return self.times, self._smooth
        key = float(rate_hz)
        if key not in self._quant:
            q = self.t0 + np.floor((self.times - self.t0) * key + 1e-6) / key
            self._quant[key] = (q, self.tel.sample(q))
        return self._quant[key]

    def _next_samples(self, rate_hz: float) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        """Anteil zwischen zwei Aktualisierungen (0…1) und die Werte bei der nächsten Aktualisierung."""
        key = float(rate_hz)
        if key not in self._counted:
            q, _ = self._quantized(rate_hz)
            frac = np.clip((self.times - q) * key, 0.0, 1.0)
            self._counted[key] = (frac, self.tel.sample(q + 1.0 / key))
        return self._counted[key]

    def raw(self, channel: str, i: int, rate_hz: float = 0.0) -> float:
        """Wert in internen Einheiten (SI)."""
        return float(self._quantized(rate_hz)[1][channel][i])

    def counted(self, channel: str, i: int, rate_hz: float) -> float:
        """Wert für die Zahlenanzeige (SI): mit ``count_numbers`` linear zwischen zwei Aktualisierungen –
        die Zahl läuft dann durch alle Zwischenwerte, statt zu springen; ohne Zählen wie ``raw``."""
        v0 = self.raw(channel, i, rate_hz)
        if not self.count_numbers or rate_hz <= 0 or channel not in CHANNELS \
                or CHANNELS[channel].kind == "duration":
            return v0
        frac, nxt = self._next_samples(rate_hz)
        v1 = float(nxt[channel][i])
        if math.isnan(v0) or math.isnan(v1):
            return v0
        return v0 + (v1 - v0) * float(frac[i])

    def value(self, channel: str, i: int, rate_hz: float = 0.0) -> float:
        """Wert in Anzeige-Einheiten."""
        return float(convert(channel, self.raw(channel, i, rate_hz), self.units)[0])

    def unit(self, channel: str) -> str:
        return "" if channel == CLOCK_CHANNEL else unit_for(channel, self.units)

    # ------------------------------------------------------------------ für Widgets

    def label(self, w: WidgetConfig) -> str:
        text = w.label if w.label is not None else channel_label(w.channel, self.language)
        return text.upper() if w.style.label_uppercase else text

    def decimals(self, w: WidgetConfig) -> int:
        if w.decimals is not None:
            return int(w.decimals)
        return 0 if w.channel in (CLOCK_CHANNEL, MAP_CHANNEL) else CHANNELS[w.channel].decimals

    def text(self, w: WidgetConfig, i: int) -> tuple[str, str]:
        """(Zahl als Text, Einheit) für Frame i."""
        if w.channel == MAP_CHANNEL:
            return "", ""
        if w.channel == CLOCK_CHANNEL:
            q = self._quantized(w.update_rate_hz)[0][i]
            if not 0 <= q <= self.tel.duration:
                return "--:--", ""
            dt = self.tel.start_time + timedelta(seconds=float(q))
            local = dt.astimezone(self.tz) if self.tz else dt.astimezone()
            return (f"{local:%H:%M:%S}" if (w.decimals or 0) > 0 else f"{local:%H:%M}"), ""
        if CHANNELS[w.channel].kind == "duration":
            v = self.raw(w.channel, i, w.update_rate_hz)
            return format_duration(v), ""
        v = float(convert(w.channel, self.counted(w.channel, i, w.update_rate_hz), self.units)[0])
        return format_number(v, self.decimals(w)), (self.unit(w.channel) if w.show_unit else "")

    def alt_text(self, w: WidgetConfig, i: int) -> tuple[str, str] | None:
        """Derselbe Wert in der jeweils anderen Einheit (m ↔ ft, km/h ↔ mph); None, wenn es keine gibt."""
        if w.channel in (CLOCK_CHANNEL, MAP_CHANNEL) or CHANNELS[w.channel].kind == "duration":
            return None
        other = IMPERIAL if self.units == METRIC else METRIC
        unit = unit_for(w.channel, other)
        if unit == unit_for(w.channel, self.units):
            return None
        v = float(convert(w.channel, self.counted(w.channel, i, w.update_rate_hz), other)[0])
        return format_number(v, self.decimals(w)), unit

    def total(self, channel: str) -> float:
        """Endwert eines kumulierten Signals (Distanz, Höhenmeter, Zeit) über die ganze Fahrt – SI."""
        arr = self.tel.channels.get(channel)
        if arr is None or arr.size == 0:
            return math.nan
        finite = arr[np.isfinite(arr)]
        return float(finite[-1]) if finite.size else math.nan

    def format_value(self, channel: str, si_value: float, decimals: int) -> tuple[str, str]:
        """Beliebigen Wert (SI) wie die Anzeige formatieren: (Zahl, Einheit)."""
        if CHANNELS[channel].kind == "duration":
            return format_duration(si_value), ""
        v = float(convert(channel, si_value, self.units)[0])
        return format_number(v, decimals), self.unit(channel)

    def series(self, channel: str, t0: float, t1: float, n: int = 160) -> tuple[np.ndarray, np.ndarray]:
        """Verlauf eines Signals zwischen Track-Zeit t0 und t1 in Anzeige-Einheiten (für den Scope)."""
        ts = np.linspace(t0, t1, max(int(n), 2))
        inside = (ts >= 0) & (ts <= self.tel.duration)
        vals = np.full(ts.shape, np.nan)
        if inside.any():
            raw = self.tel.sample(ts[inside])[channel]
            vals[inside] = convert(channel, np.asarray(raw, dtype=float), self.units)[0]
        return ts, vals

    def range(self, w: WidgetConfig) -> tuple[float, float]:
        lo, hi = w.min_value, w.max_value
        default = DEFAULT_RANGES.get(w.channel, {}).get(self.units)
        if default is None and w.channel in self.tel.stats:
            st = self.tel.stats[w.channel]
            if not math.isnan(st.min):
                a = float(convert(w.channel, st.min, self.units)[0])
                b = float(convert(w.channel, st.max, self.units)[0])
                default = (a, b if b > a else a + 1)
        default = default or (0.0, 100.0)
        lo = default[0] if lo is None else lo
        hi = default[1] if hi is None else hi
        if hi <= lo:
            hi = lo + 1
        return float(lo), float(hi)

    def fraction(self, w: WidgetConfig, i: int) -> float:
        """Füllgrad 0..1 für Bogen/Balken (flüssig, jedes Frame); NaN ohne Wert."""
        v = self.value(w.channel, i, 0.0)
        if math.isnan(v):
            return math.nan
        lo, hi = self.range(w)
        return min(max((v - lo) / (hi - lo), 0.0), 1.0)

    def zone_color(self, w: WidgetConfig, value: float) -> str:
        color = w.style.accent_color
        if math.isnan(value):
            return color
        for z in sorted(w.zones, key=lambda z: z.from_value):
            if value >= z.from_value:
                color = z.color
        return color

    def fill_color(self, w: WidgetConfig, i: int) -> str:
        if w.channel in (CLOCK_CHANNEL, MAP_CHANNEL):
            return w.style.accent_color
        return self.zone_color(w, self.value(w.channel, i, 0.0))

    def text_color(self, w: WidgetConfig, i: int) -> str:
        if w.zones and w.zones_color_text and w.channel not in (CLOCK_CHANNEL, MAP_CHANNEL):
            return self.zone_color(w, self.value(w.channel, i, w.update_rate_hz))
        return w.style.text_color
