"""Datenaufbereitung (PRD 3.1/3.2).

Ablauf von ``process()``:
1. Pausen erkennen (Zeitlücken > Schwellenwert)
2. Alles auf ein gleichmäßiges 1-Hz-Raster bringen (Smart Recording ausgleichen)
3. Distanz (mit Stillstands-Filter gegen GPS-Drift) und Geschwindigkeit
4. Höhe glätten -> Steigung über ein Streckenfenster, Höhenmeter mit Hysterese, VAM
5. Sensoren (HF, Trittfrequenz, Leistung, Temperatur) mit Dropout-Behandlung
6. Zentrierte Glättung (ohne Verzögerung), Pausenverhalten, Statistiken

Die Interpolation auf Video-Frames übernimmt ``Telemetry.sample()``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from fractions import Fraction

import numpy as np

from .models import RawTrack, SignalStats, Telemetry

R_EARTH = 6_371_000.0  # mittlerer Erdradius in m


@dataclass
class ProcessingConfig:
    pause_threshold_s: float = 5.0
    #: Verhalten in Pausen für Geschwindigkeit/Leistung/Trittfrequenz: "zero" oder "hold"
    pause_mode: str = "zero"
    #: Sensor-Lücken bis zu dieser Dauer werden interpoliert, längere als "kein Wert" angezeigt
    dropout_max_s: float = 10.0
    elevation_dropout_max_s: float = 30.0
    temperature_dropout_max_s: float = 60.0
    #: Glättungsfenster in Sekunden je Kanal
    smoothing_s: dict[str, float] = field(
        default_factory=lambda: {
            "speed": 3.0,
            "elevation": 10.0,
            "power": 3.0,
            "heart_rate": 3.0,  # mittige Fenster sind immer ungerade (2 würde ohnehin 3)
            "cadence": 3.0,
            "temperature": 10.0,
        }
    )
    #: "centered" (ohne Verzögerung, Standard) oder "trailing" (Live-Look)
    smoothing_mode: str = "centered"
    gradient_window_m: float = 50.0
    gradient_limit_pct: float = 40.0
    gain_hysteresis_m: float = 3.0
    vam_window_s: float = 60.0
    min_speed_kmh: float = 2.0
    prefer_device_speed: bool = True
    #: GPS-Ausreißer (Positionssprünge) erkennen und durch den gleitenden Median ersetzen
    outlier_filter: bool = True
    #: größte plausible Beschleunigung/Verzögerung in m/s² – stärkere Sprünge, die sofort
    #: zurückgehen, sind GPS-Fehler
    outlier_max_accel: float = 4.0

    @property
    def min_speed_ms(self) -> float:
        return self.min_speed_kmh / 3.6


# --------------------------------------------------------------------------- Hilfsfunktionen


def haversine(lat1, lon1, lat2, lon2) -> np.ndarray:
    """Großkreis-Distanz in Metern (vektorisiert)."""
    p1 = np.radians(lat1)
    p2 = np.radians(lat2)
    dphi = p2 - p1
    dlmb = np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * R_EARTH * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


# --------------------------------------------------------------------------- Filterstufen

#: wählbare Stufen (Oberfläche: Aus / niedrig / mittel / stark)
FILTER_LEVELS = ("off", "low", "medium", "strong")
FILTER_LEVEL_TITLES = {"off": "Aus", "low": "niedrig", "medium": "mittel", "strong": "stark"}
#: je Kanal: Glättungsfenster in Sekunden für Aus/niedrig/mittel/stark (Steigung: Strecke in Metern)
FILTER_TABLE: dict[str, tuple[float, float, float, float]] = {
    "speed": (0, 3, 5, 9),
    "power": (0, 3, 5, 10),
    "heart_rate": (0, 3, 5, 9),
    "cadence": (0, 3, 5, 9),
    "elevation": (0, 5, 10, 20),
    "gradient": (20, 50, 100, 200),  # Meter, über die die Steigung bestimmt wird
    "temperature": (0, 5, 10, 30),
}
#: Standardstufe je Kanal (entspricht dem bisherigen Verhalten)
FILTER_DEFAULTS: dict[str, str] = {
    "speed": "low", "power": "low", "heart_rate": "low", "cadence": "low",
    "elevation": "medium", "gradient": "low", "temperature": "medium",
}


def filter_value(channel: str, level: str) -> float:
    """Fensterlänge (s bzw. m bei der Steigung) für eine Filterstufe."""
    if level not in FILTER_LEVELS:
        raise ValueError(f"Unbekannte Filterstufe: {level}")
    return float(FILTER_TABLE[channel][FILTER_LEVELS.index(level)])


def config_for(filters: dict[str, str] | None = None, pause_mode: str = "zero") -> "ProcessingConfig":
    """Verarbeitungs-Einstellungen aus den Filterstufen eines Projekts."""
    levels = {**FILTER_DEFAULTS, **{k: v for k, v in (filters or {}).items() if k in FILTER_TABLE}}
    smoothing = {ch: filter_value(ch, lv) for ch, lv in levels.items() if ch != "gradient"}
    return ProcessingConfig(pause_mode=pause_mode, smoothing_s=smoothing,
                            gradient_window_m=filter_value("gradient", levels["gradient"]))


def smooth(values: np.ndarray, window_s: float, mode: str = "centered") -> np.ndarray:
    """Gleitender Mittelwert auf einem 1-Hz-Raster, NaN-tolerant.

    ``centered``: Fenster symmetrisch um den Punkt (gerade Fensterbreiten werden
    auf die nächste ungerade aufgerundet) – keine zeitliche Verzögerung.
    ``trailing``: nur vergangene Werte – wirkt wie eine Live-Anzeige.
    Fehlende Werte (NaN) bleiben fehlend.
    """
    arr = np.asarray(values, dtype=float)
    w = int(round(window_s))
    if w <= 1 or arr.size == 0:
        return arr.copy()
    valid = ~np.isnan(arr)
    x = np.where(valid, arr, 0.0)
    vf = valid.astype(float)
    if mode == "centered":
        if w % 2 == 0:
            w += 1
        k = np.ones(w)
        s = np.convolve(x, k, mode="same")
        c = np.convolve(vf, k, mode="same")
    elif mode == "trailing":
        k = np.ones(w)
        s = np.convolve(x, k, mode="full")[: arr.size]
        c = np.convolve(vf, k, mode="full")[: arr.size]
    else:
        raise ValueError(f"Unbekannter Glättungsmodus: {mode}")
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(c > 0, s / np.maximum(c, 1e-12), np.nan)
    out[~valid] = np.nan
    return out


def interp_sparse(t_raw: np.ndarray, values: np.ndarray, grid: np.ndarray, max_gap_s: float) -> np.ndarray:
    """Werte mit Lücken auf das Raster interpolieren.

    Zwischen zwei gültigen Messwerten wird nur interpoliert, wenn sie höchstens
    ``max_gap_s`` auseinanderliegen; sonst NaN (Sensor-Dropout).
    """
    out = np.full(grid.shape, np.nan)
    valid = ~np.isnan(values)
    if not valid.any():
        return out
    tv = t_raw[valid]
    vv = values[valid]
    out = np.interp(grid, tv, vv, left=np.nan, right=np.nan)
    if tv.size >= 2:
        idx = np.searchsorted(tv, grid, side="right")
        prev = np.clip(idx - 1, 0, tv.size - 1)
        nxt = np.clip(idx, 0, tv.size - 1)
        exact = np.isclose(grid, tv[prev])
        gap = tv[nxt] - tv[prev]
        out[(~exact) & (gap > max_gap_s)] = np.nan
    return out


def forward_fill(values: np.ndarray) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return arr.copy()
    idx = np.where(~np.isnan(arr), np.arange(arr.size), 0)
    np.maximum.accumulate(idx, out=idx)
    return arr[idx]


def _fill_both(values: np.ndarray) -> np.ndarray:
    """NaN vorwärts und am Anfang rückwärts auffüllen."""
    out = forward_fill(values)
    return forward_fill(out[::-1])[::-1]


def hold_in_mask(values: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Innerhalb von ``mask`` den letzten Wert davor halten."""
    out = np.asarray(values, dtype=float).copy()
    if not mask.any():
        return out
    tmp = out.copy()
    tmp[mask] = np.nan
    filled = forward_fill(tmp)
    out[mask] = filled[mask]
    return out


def apply_pause(values: np.ndarray, mask: np.ndarray, mode: str) -> np.ndarray:
    if mode == "zero":
        out = np.asarray(values, dtype=float).copy()
        out[mask] = 0.0
        return out
    if mode == "hold":
        return hold_in_mask(values, mask)
    raise ValueError(f"Unbekannter Pausenmodus: {mode}")


def pause_mask(t_raw: np.ndarray, grid: np.ndarray, threshold_s: float) -> tuple[np.ndarray, np.ndarray]:
    """Rasterpunkte, die in einer Zeitlücke > threshold liegen, sowie Index des Rohpunkts davor."""
    k = np.clip(np.searchsorted(t_raw, grid, side="right") - 1, 0, max(t_raw.size - 2, 0))
    if t_raw.size < 2:
        return np.zeros(grid.shape, dtype=bool), k
    dt = np.diff(t_raw)
    mask = (dt[k] > threshold_s) & (grid > t_raw[k]) & (grid < t_raw[k + 1])
    return mask, k


def despike(speed: np.ndarray, dt: np.ndarray, max_accel: float, iterations: int = 6) -> tuple[np.ndarray, np.ndarray]:
    """GPS-Sprünge in Segment-Geschwindigkeiten entfernen. Gibt (korrigiert, Maske) zurück.

    Ein Segment ist eine Spitze, wenn es über *beiden* Nachbarn (oder unter beiden) liegt
    und der Sprung zu beiden Seiten physikalisch unmöglich ist (> ``max_accel``).
    Spitzen werden durch den Mittelwert der Nachbarn ersetzt. Zuerst werden Spitzen nach
    oben behandelt (typischer GPS-Fehler), danach nach unten; mehrere Durchläufe erfassen
    auch Fehler über mehrere Segmente.
    """
    v = speed.astype(float).copy()
    bad = np.zeros(v.size, dtype=bool)
    if v.size < 3:
        return v, bad
    lim_prev = max_accel * (dt[:-2] + dt[1:-1]) / 2
    lim_next = max_accel * (dt[1:-1] + dt[2:]) / 2
    for _ in range(iterations):
        prev, cur, nxt = v[:-2], v[1:-1], v[2:]
        spikes = np.flatnonzero((cur - prev > lim_prev) & (cur - nxt > lim_next)) + 1
        if spikes.size == 0:
            spikes = np.flatnonzero((prev - cur > lim_prev) & (nxt - cur > lim_next)) + 1
        if spikes.size == 0:
            break
        bad[spikes] = True
        v[spikes] = (v[spikes - 1] + v[spikes + 1]) / 2
    return v, bad


def gradient_over_distance(distance: np.ndarray, elevation: np.ndarray, window_m: float) -> np.ndarray:
    """Steigung in % über ein Streckenfenster.

    Höhe wird als Funktion der Distanz betrachtet. Im Stand ändert sich die Distanz
    nicht, daher bleibt die Steigung dort automatisch stabil (keine Division durch ~0).
    """
    n = distance.size
    out = np.full(n, np.nan)
    valid = ~np.isnan(elevation) & ~np.isnan(distance)
    if valid.sum() < 2:
        return out
    d = distance[valid]
    e = elevation[valid]
    keep = np.concatenate([[True], np.diff(d) > 1e-6])
    d, e = d[keep], e[keep]
    if d.size < 2:
        return out
    half = window_m / 2
    lo = np.clip(distance - half, d[0], d[-1])
    hi = np.clip(distance + half, d[0], d[-1])
    span = hi - lo
    e_lo = np.interp(lo, d, e)
    e_hi = np.interp(hi, d, e)
    with np.errstate(invalid="ignore", divide="ignore"):
        g = np.where(span >= window_m * 0.25, (e_hi - e_lo) / span * 100.0, np.nan)
    g[~valid] = np.nan
    return g


def elevation_gain(elevation: np.ndarray, hysteresis_m: float) -> np.ndarray:
    """Kumulierte Höhenmeter mit Hysterese.

    Ein Anstieg zählt erst, wenn er die Schwelle überschreitet; danach wird
    kontinuierlich gezählt, bis ein Abstieg ebenfalls die Schwelle überschreitet.
    Rauschen unterhalb der Schwelle wird so ignoriert.
    """
    out = np.zeros(elevation.size)
    gain = 0.0
    ref = math.nan
    state = 0  # 0 = unbestimmt, 1 = Anstieg, -1 = Abstieg
    for i, e in enumerate(elevation):
        if not math.isnan(e):
            if math.isnan(ref):
                ref = e
            elif state == 1:
                if e > ref:
                    gain += e - ref
                    ref = e
                elif ref - e >= hysteresis_m:
                    state, ref = -1, e
            elif state == -1:
                if e < ref:
                    ref = e
                elif e - ref >= hysteresis_m:
                    state = 1
                    gain += e - ref
                    ref = e
            else:
                if e - ref >= hysteresis_m:
                    state = 1
                    gain += e - ref
                    ref = e
                elif ref - e >= hysteresis_m:
                    state, ref = -1, e
        out[i] = gain
    return out


def vam(t: np.ndarray, elevation: np.ndarray, window_s: float) -> np.ndarray:
    """Steigrate in m/h über ein zentriertes Zeitfenster."""
    n = t.size
    h = max(int(round(window_s / 2)), 1)
    i = np.arange(n)
    lo = np.clip(i - h, 0, n - 1)
    hi = np.clip(i + h, 0, n - 1)
    dt = t[hi] - t[lo]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(dt > 0, (elevation[hi] - elevation[lo]) / dt * 3600.0, np.nan)


def _stats(values: np.ndarray, mask: np.ndarray, denominator: int) -> SignalStats:
    v = values[mask & ~np.isnan(values)]
    if v.size == 0:
        return SignalStats(math.nan, math.nan, math.nan, 0.0)
    cov = v.size / denominator if denominator else 0.0
    return SignalStats(float(v.min()), float(v.max()), float(v.mean()), float(min(cov, 1.0)))


def parse_fps(value: Fraction | float | int | str) -> Fraction:
    """Framerate als exakter Bruch. 23.976/29.97/59.94 werden zu 24000/1001, 30000/1001, 60000/1001."""
    if isinstance(value, Fraction):
        f = value
    else:
        f = Fraction(str(value).strip()) if isinstance(value, str) else Fraction(value)
    if f <= 0:
        raise ValueError(f"Ungültige Framerate: {value}")
    x = float(f)
    n = round(x * 1.001)
    if f.denominator != 1001 and abs(x - round(x)) > 0.005 and abs(x - n * 1000 / 1001) < 0.005:
        return Fraction(n * 1000, 1001)
    return f.limit_denominator(1001)


def frame_times(start_s: float, n_frames: int, fps: Fraction | float | str) -> np.ndarray:
    """Exakte Frame-Zeitpunkte: ``start + n * den / num`` (keine Drift bei 29,97 FPS)."""
    f = parse_fps(fps)
    n = np.arange(int(n_frames), dtype=np.int64)
    return start_s + (n * f.denominator) / f.numerator


# --------------------------------------------------------------------------- Hauptfunktion


def process(raw: RawTrack, config: ProcessingConfig | None = None) -> Telemetry:
    cfg = config or ProcessingConfig()
    t = raw.t
    warnings = list(raw.warnings)
    grid = np.arange(0.0, math.floor(raw.duration) + 1.0, 1.0)
    pmask, k = pause_mask(t, grid, cfg.pause_threshold_s)
    dt = np.diff(t)
    gap_seg = dt > cfg.pause_threshold_s
    n_pauses = int(gap_seg.sum())
    min_ms = cfg.min_speed_ms

    # --- Distanz & Position
    seg = haversine(raw.lat[:-1], raw.lon[:-1], raw.lat[1:], raw.lon[1:])
    distance_source = "gps"
    dev_dist = raw.sensors.get("distance_device")
    dev_speed_raw = raw.sensors.get("speed_device")
    if dev_dist is not None and np.mean(np.isfinite(dev_dist)) >= 0.5:
        # Distanz des Geräts (Radsensor oder gefiltertes GPS) – zuverlässiger als Rohpositionen
        ok = np.isfinite(dev_dist)
        dd = np.interp(t, t[ok], dev_dist[ok])
        dd = np.maximum.accumulate(dd - dd[0])
        seg = np.diff(dd)
        distance_source = "device"
    elif not raw.has_position:
        if dev_speed_raw is not None and np.isfinite(dev_speed_raw).any():
            sp = np.nan_to_num(interp_sparse(t, dev_speed_raw, t, cfg.dropout_max_s))
            seg = (sp[:-1] + sp[1:]) / 2 * dt
        else:
            seg = np.zeros(dt.size)
            warnings.append("Weder GPS noch Geschwindigkeitssensor – Distanz und Geschwindigkeit fehlen.")
        distance_source = "device"
    seg_speed = seg / dt
    n_outliers = 0
    if cfg.outlier_filter and distance_source == "gps" and seg_speed.size >= 3:
        # GPS-Sprünge (z. B. 1 s mit 130 km/h mitten im Anstieg) durch Nachbarwerte ersetzen
        moving_idx = np.flatnonzero(~gap_seg)
        sv = seg_speed[moving_idx]
        fixed, bad = despike(sv, dt[moving_idx], cfg.outlier_max_accel)
        if bad.any():
            idx = moving_idx[bad]
            seg_speed = seg_speed.copy()
            seg_speed[idx] = fixed[bad]
            seg = seg.copy()
            seg[idx] = seg_speed[idx] * dt[idx]
            n_outliers = int(bad.sum())
            warnings.append(f"{n_outliers} GPS-Ausreißer wurden geglättet.")
    # Stillstands-Filter: GPS-Drift im Stand nicht als Strecke zählen
    seg_eff = np.where((seg_speed < min_ms) & ~gap_seg, 0.0, seg) if distance_source == "gps" else seg
    cum_raw = np.concatenate([[0.0], np.cumsum(seg_eff)])
    distance = np.interp(grid, t, cum_raw)
    distance[pmask] = cum_raw[k[pmask]]
    lat = np.interp(grid, t, raw.lat)
    lon = np.interp(grid, t, raw.lon)
    lat[pmask] = raw.lat[k[pmask]]
    lon[pmask] = raw.lon[k[pmask]]

    # --- Geschwindigkeit
    moving_seg = ~gap_seg
    if moving_seg.any():
        tm = (t[:-1] + dt / 2)[moving_seg]
        speed = np.interp(grid, tm, seg_speed[moving_seg])
    else:
        speed = np.zeros(grid.shape)
    speed_source = "gps"
    dev = raw.sensors.get("speed_device")
    if cfg.prefer_device_speed and dev is not None and np.mean(~np.isnan(dev)) >= 0.5:
        dev_grid = interp_sparse(t, dev, grid, cfg.dropout_max_s)
        speed = np.where(np.isnan(dev_grid), speed, dev_grid)
        speed_source = "device"
    speed = apply_pause(speed, pmask, cfg.pause_mode)
    speed = smooth(speed, cfg.smoothing_s.get("speed", 0), cfg.smoothing_mode)
    speed = np.where(speed < min_ms, 0.0, speed)

    # --- Höhe, Steigung, Höhenmeter, VAM
    has_ele = bool((~np.isnan(raw.ele)).any())
    if has_ele:
        ele = interp_sparse(t, raw.ele, grid, cfg.elevation_dropout_max_s)
        ele = hold_in_mask(ele, pmask)
        ele = smooth(ele, cfg.smoothing_s.get("elevation", 0), "centered")
        gradient = gradient_over_distance(distance, ele, cfg.gradient_window_m)
        gradient = np.clip(_fill_both(gradient), -cfg.gradient_limit_pct, cfg.gradient_limit_pct)
        gradient[np.isnan(ele)] = np.nan
        gain = elevation_gain(ele, cfg.gain_hysteresis_m)
        vam_v = vam(grid, ele, cfg.vam_window_s)
    else:
        ele = np.full(grid.shape, np.nan)
        gradient = ele.copy()
        gain = ele.copy()
        vam_v = ele.copy()
        warnings.append("Die Datei enthält keine Höhendaten – Höhe, Steigung, Höhenmeter und VAM sind nicht verfügbar.")

    # --- Sensoren
    channels: dict[str, np.ndarray] = {}
    available: dict[str, bool] = {}
    sensor_coverage: dict[str, float] = {}
    not_paused = ~pmask
    n_active = int(not_paused.sum())
    for ch in ("heart_rate", "cadence", "power", "temperature"):
        raw_vals = raw.sensors.get(ch)
        if raw_vals is None or not (~np.isnan(raw_vals)).any():
            channels[ch] = np.full(grid.shape, np.nan)
            available[ch] = False
            sensor_coverage[ch] = 0.0
            continue
        max_gap = cfg.temperature_dropout_max_s if ch == "temperature" else cfg.dropout_max_s
        vals = interp_sparse(t, raw_vals, grid, max_gap)
        sensor_coverage[ch] = float(np.sum(~np.isnan(vals) & not_paused) / max(n_active, 1))
        if ch in ("power", "cadence"):
            vals = apply_pause(vals, pmask, cfg.pause_mode)
        else:
            vals = hold_in_mask(vals, pmask)
        vals = smooth(vals, cfg.smoothing_s.get(ch, 0), cfg.smoothing_mode)
        channels[ch] = vals
        available[ch] = True

    # --- Zeiten
    moving_flag = not_paused & (speed >= min_ms)
    moving_time = np.concatenate([[0.0], np.cumsum(moving_flag[1:].astype(float))])

    channels.update(
        {
            "speed": speed,
            "distance": distance,
            "elevation": ele,
            "gradient": gradient,
            "elevation_gain": gain,
            "vam": vam_v,
            "elapsed_time": grid.copy(),
            "moving_time": moving_time,
            "lat": lat,
            "lon": lon,
        }
    )
    for ch in ("speed", "distance", "elapsed_time", "moving_time", "lat", "lon"):
        available[ch] = True
    available["position"] = bool(raw.has_position and np.isfinite(lat).all() and np.ptp(lat) + np.ptp(lon) > 0)
    for ch in ("elevation", "gradient", "elevation_gain", "vam"):
        available[ch] = has_ele

    # --- Statistiken
    stats: dict[str, SignalStats] = {}
    for ch, arr in channels.items():
        if ch == "speed":
            st = _stats(arr, moving_flag, int(moving_flag.sum()))
            st.coverage = 1.0
            # Minimum der Geschwindigkeit bei Bewegung ist wenig aussagekräftig
        else:
            st = _stats(arr, not_paused, n_active)
        if ch in sensor_coverage:
            st.coverage = sensor_coverage[ch]
        stats[ch] = st

    return Telemetry(
        start_time=raw.start_time,
        t=grid,
        channels=channels,
        available=available,
        stats=stats,
        pause_mask=pmask,
        speed_source=speed_source,
        min_speed_ms=min_ms,
        n_pauses=n_pauses,
        warnings=warnings,
    )
