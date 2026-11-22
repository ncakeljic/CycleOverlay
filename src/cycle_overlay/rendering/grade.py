"""Steigung: Farbklassen für die Route und Erkennung von Anstiegen (ohne Qt).

Farbklassen (Standard): Abfahrt < -2 % blau · flach grün · ab 2 % gelb · ab 5 % orange ·
ab 8 % rot · ab 12 % dunkelrot. Schwellen und Farben sind einstellbar.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..core.processor import gradient_over_distance

DEFAULT_STEPS = (-2.0, 2.0, 5.0, 8.0, 12.0)
DEFAULT_COLORS = ("#3D8BFF", "#3DDC84", "#FFD43B", "#FF8C1A", "#FF3B3B", "#B0003A")


def grade_class(grade, steps=DEFAULT_STEPS) -> np.ndarray:
    """Klasse 0..len(steps) je Wert (NaN -> Klasse „flach“)."""
    g = np.nan_to_num(np.asarray(grade, dtype=float), nan=0.0)
    return np.digitize(g, np.asarray(steps, dtype=float))


def grade_color(grade: float, steps=DEFAULT_STEPS, colors=DEFAULT_COLORS) -> str:
    return colors[int(grade_class([grade], steps)[0])]


def smooth_grade(tel, window_m: float = 100.0) -> np.ndarray:
    """Steigung über ein längeres Streckenfenster – ruhigere Farben auf der Route."""
    key = ("grade_smooth", window_m)
    if key not in tel.cache:
        g = gradient_over_distance(tel.channels["distance"], tel.channels["elevation"], window_m)
        tel.cache[key] = np.clip(np.nan_to_num(g, nan=0.0), -40, 40)
    return tel.cache[key]


def class_runs(classes: np.ndarray, mask: np.ndarray | None = None) -> list[tuple[int, int, int]]:
    """Zusammenhängende Abschnitte gleicher Klasse: (Klasse, Start, Ende einschließlich).

    Benachbarte Abschnitte teilen sich den Grenzpunkt, damit die Linie lückenlos bleibt.
    Punkte außerhalb von ``mask`` unterbrechen die Abschnitte.
    """
    n = classes.size
    runs: list[tuple[int, int, int]] = []
    if n == 0:
        return runs
    ok = np.ones(n, dtype=bool) if mask is None else mask
    i = 0
    while i < n:
        if not ok[i]:
            i += 1
            continue
        c = int(classes[i])
        j = i
        while j + 1 < n and ok[j + 1] and int(classes[j + 1]) == c:
            j += 1
        end = j + 1 if (j + 1 < n and ok[j + 1]) else j  # Grenzpunkt mitnehmen
        if end > i:
            runs.append((c, i, end))
        i = j + 1
    return runs


# --------------------------------------------------------------------------- Anstiege


@dataclass
class Climb:
    """Anstieg oder Abfahrt (``kind``). Bei Abfahrten sind Höhenmeter und Steigung negativ."""

    start_d: float  # Meter
    end_d: float
    start_ele: float
    end_ele: float
    max_grade: float  # % (über 100 m); bei Abfahrten das stärkste Gefälle (negativ)
    kind: str = "climb"  # "climb" | "descent"

    @property
    def is_descent(self) -> bool:
        return self.kind == "descent"

    @property
    def length(self) -> float:
        return self.end_d - self.start_d

    @property
    def gain(self) -> float:
        return self.end_ele - self.start_ele

    @property
    def avg_grade(self) -> float:
        return self.gain / self.length * 100 if self.length > 0 else 0.0


def detect_climbs(distance: np.ndarray, elevation: np.ndarray, *, min_length_m: float = 500.0,
                  min_gain_m: float = 20.0, min_grade: float = 3.0, step_m: float = 10.0,
                  max_drop_m: float = 10.0) -> list[Climb]:
    """Anstiege erkennen (ähnlich „ClimbPro“).

    Ein Anstieg beginnt an einem Tiefpunkt und endet am höchsten Punkt, bevor die Höhe
    um mehr als ``max_drop_m`` (bzw. 20 % des bisherigen Anstiegs) wieder abfällt.
    Kurze Zwischenabfahrten werden so toleriert.
    """
    ok = np.isfinite(distance) & np.isfinite(elevation)
    d, e = distance[ok], elevation[ok]
    keep = np.concatenate([[True], np.diff(d) > 1e-6]) if d.size else np.array([], dtype=bool)
    d, e = d[keep], e[keep]
    if d.size < 2 or d[-1] - d[0] < min_length_m:
        return []
    grid = np.arange(d[0], d[-1], step_m)
    eg = np.interp(grid, d, e)
    n = grid.size
    climbs: list[Climb] = []
    i = 0
    while i < n - 1:
        start = peak = i
        j = i
        while j < n - 1:
            j += 1
            if eg[j] > eg[peak]:
                peak = j
            elif eg[j] < eg[start] and peak == start:
                start = peak = j
            if eg[peak] - eg[j] > max(max_drop_m, 0.2 * (eg[peak] - eg[start])):
                break
        # Anfang auf den Beginn des eigentlichen Anstiegs kürzen (flache/wellige Vorlaufstrecke weg)
        w2 = max(int(round(400 / step_m)), 1)
        while peak - start > w2 and (eg[start + w2] - eg[start]) / (w2 * step_m) * 100 < min_grade * 0.6:
            start += 1
        w3 = max(int(round(100 / step_m)), 1)  # Feinabgleich auf ~100 m
        while peak - start > w3 and (eg[start + w3] - eg[start]) / (w3 * step_m) * 100 < min_grade * 0.6:
            start += 1
        while start + 1 < peak and eg[start + 1] <= eg[start]:
            start += 1
        # Ende ebenso: flache Nachlaufstrecke (z. B. Rauschen im Tal/auf dem Gipfel) abschneiden
        while peak - start > w2 and (eg[peak] - eg[peak - w2]) / (w2 * step_m) * 100 < min_grade * 0.6:
            peak -= 1
        while peak - start > w3 and (eg[peak] - eg[peak - w3]) / (w3 * step_m) * 100 < min_grade * 0.6:
            peak -= 1
        gain = eg[peak] - eg[start]
        length = grid[peak] - grid[start]
        if length >= min_length_m and gain >= min_gain_m and gain / length * 100 >= min_grade:
            w = max(int(round(100 / step_m)), 1)
            seg = eg[start:peak + 1]
            grades = (seg[w:] - seg[:-w]) / (w * step_m) * 100 if seg.size > w else np.array([gain / length * 100])
            climbs.append(Climb(float(grid[start]), float(grid[peak]), float(eg[start]), float(eg[peak]),
                                float(np.max(grades))))
        i = max(j if j > i else i + 1, peak + 1 if peak > start else i + 1)
    return climbs


def detect_descents(distance: np.ndarray, elevation: np.ndarray, **kw) -> list[Climb]:
    """Abfahrten = Anstiege des gespiegelten Höhenprofils."""
    out = []
    for c in detect_climbs(distance, -np.asarray(elevation, dtype=float), **kw):
        out.append(Climb(c.start_d, c.end_d, -c.start_ele, -c.end_ele, -c.max_grade, "descent"))
    return out


def climbs_for(tel, min_length_m: float = 500.0, min_gain_m: float = 20.0, min_grade: float = 3.0,
               mode: str = "climbs") -> list[Climb]:
    """Anstiege und/oder Abfahrten der Fahrt (nach Startdistanz sortiert, zwischengespeichert)."""
    key = ("climbs", min_length_m, min_gain_m, min_grade, mode)
    if key not in tel.cache:
        if not tel.available.get("elevation"):
            tel.cache[key] = []
        else:
            d, e = tel.channels["distance"], tel.channels["elevation"]
            kw = dict(min_length_m=min_length_m, min_gain_m=min_gain_m, min_grade=min_grade)
            found: list[Climb] = []
            if mode in ("climbs", "both"):
                found += detect_climbs(d, e, **kw)
            if mode in ("descents", "both"):
                found += detect_descents(d, e, **kw)
            tel.cache[key] = sorted(found, key=lambda c: c.start_d)
    return tel.cache[key]


def climb_status(climbs: list[Climb], dist: float, lookahead_m: float) -> tuple[str, Climb | None]:
    """("in", Anstieg) wenn gerade im Anstieg, ("ahead", nächster) wenn in Sichtweite, sonst ("none", None)."""
    if not math.isfinite(dist):
        return "none", None
    for c in sorted(climbs, key=lambda c: c.start_d):
        if c.start_d <= dist <= c.end_d:
            return "in", c
    for c in sorted(climbs, key=lambda c: c.start_d):
        if dist < c.start_d <= dist + lookahead_m:
            return "ahead", c
    return "none", None


def lowest_reference(tel, behind_m: float, ahead_m: float, smooth_s: int = 21) -> np.ndarray:
    """Tiefster Punkt im Umkreis (Strecke von ``behind_m`` zurück bis ``ahead_m`` voraus) je Sekunde.

    Geglättet, damit das Höhenband nicht springt, wenn ein neuer Tiefpunkt in Sicht kommt.
    """
    key = ("lowest_ref", round(behind_m), round(ahead_m), smooth_s)
    if key in tel.cache:
        return tel.cache[key]
    d = tel.channels["distance"]
    e = tel.channels["elevation"]
    lo = np.searchsorted(d, d - behind_m, side="left")
    hi = np.searchsorted(d, d + ahead_m, side="right")
    out = np.empty(d.size)
    finite = np.where(np.isfinite(e), e, np.inf)
    for k in range(d.size):
        m = finite[lo[k]:max(hi[k], lo[k] + 1)].min()
        out[k] = m if np.isfinite(m) else (e[k] if np.isfinite(e[k]) else 0.0)
    w = int(smooth_s) | 1
    out = np.convolve(np.pad(out, w // 2, mode="edge"), np.ones(w) / w, mode="valid")
    tel.cache[key] = out
    return out
