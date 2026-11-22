"""Geometrie der Navigations-Minimap (ohne Qt, vollständig testbar).

Koordinaten:
- Welt: Meter in einer lokalen Ebene um einen festen Ursprung (x = Osten, y = Norden).
- Ansicht: u = rechts, v = vorwärts (Blickrichtung) in Metern, relativ zur aktuellen Position.
- Bildschirm: Pixel (x nach rechts, y nach unten).

Die gesamte Abbildung Welt -> Bildschirm ist eine 3x3-Homographie (Spaltenvektoren).
Für ``tilt_deg = 0`` ist sie affin (Draufsicht), sonst perspektivisch: eine Kamera
schaut schräg auf die Kartenebene – entfernte Teile werden kleiner wie im Navi.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

R_EARTH = 6_371_000.0


def project_local(lat, lon, lat0: float, lon0: float) -> tuple[np.ndarray, np.ndarray]:
    """Geografische Koordinaten -> lokale Meter (Osten, Norden)."""
    x = np.radians(np.asarray(lon, dtype=float) - lon0) * R_EARTH * math.cos(math.radians(lat0))
    y = np.radians(np.asarray(lat, dtype=float) - lat0) * R_EARTH
    return x, y


# --------------------------------------------------------------------------- Fahrtrichtung


def compute_heading(x: np.ndarray, y: np.ndarray, distance: np.ndarray, window_m: float = 25.0,
                    smooth_s: int = 3) -> np.ndarray:
    """Fahrtrichtung je Rasterpunkt in Grad, *entfaltet* (kann über 360 hinaus laufen).

    0 = Norden, 90 = Osten. Berechnet über ein Streckenfenster (nicht Zeit), damit die
    Richtung im Stand stabil bleibt. Die entfaltete Form erlaubt Interpolation und
    Glättung ohne Sprung bei 359° -> 0°.
    """
    n = x.size
    out = np.full(n, np.nan)
    valid = np.isfinite(x) & np.isfinite(y) & np.isfinite(distance)
    if valid.sum() < 2:
        return np.zeros(n)
    d, xv, yv = distance[valid], x[valid], y[valid]
    keep = np.concatenate([[True], np.diff(d) > 1e-6])
    d, xv, yv = d[keep], xv[keep], yv[keep]
    if d.size < 2:
        return np.zeros(n)
    half = window_m / 2
    lo = np.clip(distance - half, d[0], d[-1])
    hi = np.clip(distance + half, d[0], d[-1])
    dx = np.interp(hi, d, xv) - np.interp(lo, d, xv)
    dy = np.interp(hi, d, yv) - np.interp(lo, d, yv)
    ok = np.hypot(dx, dy) >= window_m * 0.25
    out[ok] = np.degrees(np.arctan2(dx[ok], dy[ok]))
    if not ok.any():
        return np.zeros(n)
    # Lücken (Start, Stillstand ohne Strecke) mit Nachbarwerten füllen
    idx = np.where(ok, np.arange(n), 0)
    np.maximum.accumulate(idx, out=idx)
    first = int(np.argmax(ok))
    idx[:first] = first
    out = out[idx]
    unwrapped = np.degrees(np.unwrap(np.radians(out)))
    if smooth_s > 1:
        w = int(smooth_s) | 1
        pad = w // 2
        padded = np.pad(unwrapped, pad, mode="edge")
        unwrapped = np.convolve(padded, np.ones(w) / w, mode="valid")
    return unwrapped


def heading_at(t, grid_t: np.ndarray, heading_unwrapped: np.ndarray) -> np.ndarray:
    """Richtung zu beliebigen Zeiten (0..360), interpoliert ohne Sprung über Norden."""
    return np.mod(np.interp(np.asarray(t, dtype=float), grid_t, heading_unwrapped), 360.0)


def auto_zoom_radius(speed_ms: np.ndarray, base_radius_m: float, smooth_s: int = 7) -> np.ndarray:
    """Sichtradius abhängig vom Tempo (wie bei GTA): schneller = weiter herausgezoomt."""
    kmh = np.nan_to_num(np.asarray(speed_ms, dtype=float)) * 3.6
    factor = np.clip(0.8 + (kmh - 10.0) / 40.0, 0.8, 2.0)
    w = int(smooth_s) | 1
    pad = w // 2
    factor = np.convolve(np.pad(factor, pad, mode="edge"), np.ones(w) / w, mode="valid")
    return base_radius_m * factor


# --------------------------------------------------------------------------- Kamera


@dataclass
class Camera:
    center_x: float  # aktuelle Position (Welt-Meter)
    center_y: float
    heading_deg: float  # Fahrtrichtung (0 = Norden)
    rotate: bool  # True = Fahrtrichtung oben, False = Norden oben
    radius_m: float  # Sichtweite von der Position bis zum oberen Rand (Draufsicht)
    rect: tuple[float, float, float, float]  # Bildschirm-Rechteck (x, y, w, h)
    tilt_deg: float = 0.0
    anchor: float = 0.5  # Position des Pfeils als Anteil der Höhe von oben

    @property
    def view_angle_deg(self) -> float:
        """Drehung der Karte: Richtung, die nach oben zeigt."""
        return self.heading_deg if self.rotate else 0.0

    @property
    def anchor_xy(self) -> tuple[float, float]:
        x, y, w, h = self.rect
        return x + w / 2, y + h * self.anchor

    def _scale(self) -> float:
        """Pixel pro Meter am Pfeil."""
        x, y, w, h = self.rect
        return (h * self.anchor) / max(self.radius_m, 1e-6)

    @property
    def camera_distance(self) -> float:
        return self.radius_m * 1.6

    def matrix(self) -> np.ndarray:
        """3x3-Homographie Welt -> Bildschirm (Spaltenvektoren)."""
        a = math.radians(self.view_angle_deg)
        c, s = math.cos(a), math.sin(a)
        # Welt -> Ansicht (u rechts, v vorwärts), Ursprung = aktuelle Position
        to_view = np.array([
            [c, -s, -(c * self.center_x - s * self.center_y)],
            [s, c, -(s * self.center_x + c * self.center_y)],
            [0.0, 0.0, 1.0],
        ])
        ax, ay = self.anchor_xy
        k = self._scale()
        th = math.radians(self.tilt_deg)
        L = self.camera_distance
        # Lochkamera über dem Boden, um tilt geneigt, schaut auf den Pfeil:
        #   x_c = u,  y_c = v*cos(th),  z_c = v*sin(th) + L
        #   sx = ax + k*L * x_c / z_c,  sy = ay - k*L * y_c / z_c
        F = k * L
        proj = np.array([
            [ax * math.sin(th), F, ax * L],
            [ay * math.sin(th) - F * math.cos(th), 0.0, ay * L],
            [math.sin(th), 0.0, L],
        ])
        # Spalten von proj beziehen sich auf (v, u, 1) -> umsortieren auf (u, v, 1)
        proj = proj[:, [1, 0, 2]]
        return proj @ to_view

    def project(self, x, y) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Weltpunkte -> Bildschirm. Gibt (sx, sy, w) zurück; w <= 0 heißt hinter der Kamera."""
        H = self.matrix()
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        X = H[0, 0] * x + H[0, 1] * y + H[0, 2]
        Y = H[1, 0] * x + H[1, 1] * y + H[1, 2]
        W = H[2, 0] * x + H[2, 1] * y + H[2, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            return X / W, Y / W, W

    def project3d(self, x, y, z) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Weltpunkte mit Höhe ``z`` (Meter über der Kartenebene) -> Bildschirm.

        Gibt (sx, sy, depth) zurück; depth ist der Abstand zur Kamera entlang der
        Blickrichtung (> 0 = vor der Kamera). Für z = 0 identisch mit ``project``.
        """
        a = math.radians(self.view_angle_deg)
        c, s = math.cos(a), math.sin(a)
        dx = np.asarray(x, dtype=float) - self.center_x
        dy = np.asarray(y, dtype=float) - self.center_y
        z = np.asarray(z, dtype=float)
        u = c * dx - s * dy
        v = s * dx + c * dy
        th = math.radians(self.tilt_deg)
        L = self.camera_distance
        F = self._scale() * L
        ax, ay = self.anchor_xy
        yc = v * math.cos(th) + z * math.sin(th)
        zc = v * math.sin(th) - z * math.cos(th) + L
        with np.errstate(divide="ignore", invalid="ignore"):
            return ax + F * u / zc, ay - F * yc / zc, zc

    def unproject(self, sx: float, sy: float) -> tuple[float, float]:
        """Bildschirmpunkt -> Weltpunkt auf der Kartenebene."""
        Hi = np.linalg.inv(self.matrix())
        X, Y, W = Hi @ np.array([sx, sy, 1.0])
        return X / W, Y / W

    def visible_bbox(self, max_ahead_factor: float = 3.0) -> tuple[float, float, float, float]:
        """Weltbereich (xmin, ymin, xmax, ymax), der sichtbar sein kann – für das Kachel-Culling."""
        x, y, w, h = self.rect
        pts = []
        for sx, sy in ((x, y + h), (x + w, y + h), (x, y), (x + w, y)):
            wx, wy = self.unproject(sx, sy)
            pts.append((wx, wy))
        if self.tilt_deg > 0:
            # obere Ecken können am Horizont sehr weit weg liegen -> begrenzen
            lim = self.radius_m * max_ahead_factor
            pts = [(self.center_x + max(min(px - self.center_x, lim), -lim),
                    self.center_y + max(min(py - self.center_y, lim), -lim)) for px, py in pts]
        xs = [p[0] for p in pts] + [self.center_x]
        ys = [p[1] for p in pts] + [self.center_y]
        margin = self.radius_m * 0.15
        return min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin

    def north_vector(self) -> tuple[float, float]:
        """Richtung „Norden“ auf dem Bildschirm (Einheitsvektor) – für den Kompass."""
        ax, ay = self.anchor_xy
        sx, sy, _ = self.project(self.center_x, self.center_y + self.radius_m * 0.5)
        dx, dy = float(sx) - ax, float(sy) - ay
        n = math.hypot(dx, dy) or 1.0
        return dx / n, dy / n

    def meters_per_pixel(self) -> float:
        return 1.0 / self._scale()

    def qt_values(self) -> tuple[float, ...]:
        """Werte für ``QTransform(m11, m12, m13, m21, m22, m23, m31, m32, m33)`` (Zeilenvektoren)."""
        H = self.matrix()
        return (H[0, 0], H[1, 0], H[2, 0], H[0, 1], H[1, 1], H[2, 1], H[0, 2], H[1, 2], H[2, 2])


def nice_scale(meters: float) -> float:
    """Runde Maßstabslänge (z. B. 50, 100, 200, 500 m) kleiner oder gleich ``meters``."""
    if meters <= 0:
        return 0.0
    exp = 10 ** math.floor(math.log10(meters))
    for f in (5, 2, 1):
        if f * exp <= meters:
            return f * exp
    return exp


def route_mask(cam, x: np.ndarray, y: np.ndarray, factor: float = 2.0) -> np.ndarray:
    """Welche Routenpunkte gezeichnet werden.

    Bereich = sichtbarer Kartenausschnitt, um ``factor`` um die Position vergrößert (über den Rand
    hinaus wird ohnehin weggeschnitten). Zusätzlich je ein Nachbarpunkt außerhalb, damit die Linie
    bis an den Rand reicht statt kurz davor aufzuhören.
    """
    f = max(float(factor), 1.0)
    x0, y0, x1, y1 = cam.visible_bbox(max_ahead_factor=3.0 * f)
    cx, cy = cam.center_x, cam.center_y
    x0, x1 = cx - (cx - x0) * f, cx + (x1 - cx) * f
    y0, y1 = cy - (cy - y0) * f, cy + (y1 - cy) * f
    ok = np.isfinite(x) & np.isfinite(y)
    inside = (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1) & ok
    grown = inside.copy()
    grown[1:] |= inside[:-1]
    grown[:-1] |= inside[1:]
    return grown & ok


#: Anteil der Routen-Sichtweite, über den die Route zum Rand hin ausgeblendet wird
ROUTE_FADE = 0.3
ROUTE_FADE_LEVELS = 8


def route_fade_levels(cam, x: np.ndarray, y: np.ndarray, factor: float = 2.0, fade: float = ROUTE_FADE,
                      levels: int = ROUTE_FADE_LEVELS) -> np.ndarray:
    """Deckkraft-Stufe je Routenpunkt (0 = unsichtbar … ``levels`` = voll) nach Abstand zur Position.

    Bis ``(1 - fade)`` × Reichweite voll deckend, danach linear bis 0 am Ende der Reichweite
    (Reichweite = Sichtweite × ``factor``). So endet die Route weich statt abrupt.
    """
    reach = cam.radius_m * max(float(factor), 1.0)
    d = np.hypot(np.asarray(x, dtype=float) - cam.center_x, np.asarray(y, dtype=float) - cam.center_y)
    width = max(reach * fade, 1e-9)
    alpha = np.clip((reach - d) / width, 0.0, 1.0)
    alpha[~np.isfinite(alpha)] = 0.0
    return np.ceil(alpha * levels - 1e-9).astype(int)


def segment_levels(point_levels: np.ndarray) -> np.ndarray:
    """Stufe je Strecke zwischen zwei Punkten: die hellere der beiden Enden.

    Dadurch reicht die Linie noch bis in den ersten unsichtbaren Punkt hinein (blasser Ausläufer).
    """
    lv = np.asarray(point_levels)
    if lv.size < 2:
        return np.zeros(0, dtype=int)
    return np.maximum(lv[:-1], lv[1:])


def segment_runs(keys: np.ndarray, visible: np.ndarray) -> list[tuple[int, int]]:
    """Zusammenhängende sichtbare Strecken mit gleichem Schlüssel als (erste, letzte) Strecken-Indizes."""
    keys = np.asarray(keys)
    vis = np.asarray(visible, dtype=bool)
    if keys.size == 0 or not vis.any():
        return []
    brk = np.ones(keys.size, dtype=bool)
    brk[1:] = (keys[1:] != keys[:-1]) | (vis[1:] != vis[:-1])
    starts = np.flatnonzero(brk)
    ends = np.r_[starts[1:] - 1, keys.size - 1]
    return [(int(a), int(b)) for a, b in zip(starts, ends) if vis[a]]

