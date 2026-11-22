"""Logik der Sync-Zeitleiste (ohne Qt).

Die Zeitleiste zeigt die Fahrt in **Track-Zeit** (Sekunden ab Trackbeginn). Der Videoclip ist ein
Block von ``base + offset`` bis ``base + offset + Dauer``; Ziehen des Blocks ändert den Versatz.

- :class:`TimeAxis` – sichtbarer Ausschnitt, Pixel ↔ Zeit, Zoom um einen Punkt, Begrenzung.
- :func:`ticks` – Lineal-Striche passend zur Zoomstufe (Stunden … einzelne Frames).
- :class:`OffsetDrag` – Ziehweg in Versatz umrechnen (fein mit Shift, auf Frames eingerastet).
- :class:`OffsetHistory` – Rückgängig/Wiederherstellen für Versatzänderungen.
- :func:`minmax_envelope` – Kurve auf Pixelspalten verdichten (schnelles Zeichnen langer Fahrten).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

#: Feinste Zoomstufe: so viele Pixel je Frame
MAX_PX_PER_FRAME = 14.0
#: Rand links/rechts um Fahrt und Clip (Anteil der Gesamtlänge, mindestens 30 s)
MARGIN_FRACTION = 0.03


def snap_to_frame(seconds: float, fps: float) -> float:
    if fps <= 0:
        return float(seconds)
    return round(float(seconds) * fps) / fps


@dataclass
class TimeAxis:
    """Sichtbarer Zeitausschnitt einer Zeitleiste mit ``width`` Pixeln."""

    width: int = 800
    start: float = 0.0
    spp: float = 1.0  # Sekunden je Pixel
    lo: float = 0.0  # Grenzen, innerhalb derer gescrollt werden kann
    hi: float = 3600.0
    fps: float = 30.0

    # ---------------------------------------------------------------- Umrechnung

    @property
    def end(self) -> float:
        return self.start + self.spp * self.width

    @property
    def span(self) -> float:
        return self.spp * self.width

    def x_of(self, t):
        return (np.asarray(t, dtype=float) - self.start) / self.spp if isinstance(t, np.ndarray) \
            else (float(t) - self.start) / self.spp

    def t_of(self, x: float) -> float:
        return self.start + float(x) * self.spp

    # ---------------------------------------------------------------- Grenzen und Zoom

    @property
    def min_spp(self) -> float:
        return 1.0 / (max(self.fps, 1e-6) * MAX_PX_PER_FRAME)

    @property
    def max_spp(self) -> float:
        return max((self.hi - self.lo) / max(self.width, 1), self.min_spp)

    def set_bounds(self, lo: float, hi: float) -> None:
        if hi <= lo:
            hi = lo + 1.0
        self.lo, self.hi = float(lo), float(hi)
        self._clamp()

    def set_width(self, width: int) -> None:
        """Breite ändern, Mitte des Ausschnitts beibehalten."""
        width = max(int(width), 10)
        mid = self.start + self.span / 2
        self.width = width
        self.start = mid - self.span / 2
        self._clamp()

    def _clamp(self) -> None:
        self.spp = min(max(self.spp, self.min_spp), self.max_spp)
        span = self.span
        if span >= self.hi - self.lo:
            self.start = self.lo - (span - (self.hi - self.lo)) / 2
        else:
            self.start = min(max(self.start, self.lo), self.hi - span)

    def show(self, a: float, b: float, pad: float = 0.0) -> None:
        """Bereich [a, b] (mit Rand ``pad`` als Anteil) vollständig zeigen."""
        a, b = float(min(a, b)), float(max(a, b))
        extra = (b - a) * pad
        a, b = a - extra, b + extra
        self.spp = max(b - a, 1e-9) / max(self.width, 1)
        self.start = a
        self._clamp()
        if self.span > b - a + 1e-9:  # auf Mindestzoom begrenzt: zentrieren
            self.start = (a + b) / 2 - self.span / 2
            self._clamp()

    def show_all(self) -> None:
        self.show(self.lo, self.hi)

    def zoom(self, factor: float, anchor_x: float | None = None) -> None:
        """Zoomen um ``factor`` (>1 = hinein); die Zeit unter ``anchor_x`` bleibt stehen."""
        if anchor_x is None:
            anchor_x = self.width / 2
        t_anchor = self.t_of(anchor_x)
        self.spp = min(max(self.spp / factor, self.min_spp), self.max_spp)
        self.start = t_anchor - anchor_x * self.spp
        self._clamp()

    def scroll_px(self, dx: float) -> None:
        self.start += dx * self.spp
        self._clamp()

    def center_on(self, t: float) -> None:
        self.start = float(t) - self.span / 2
        self._clamp()

    def ensure_visible(self, t: float, margin_px: float = 40.0) -> None:
        x = self.x_of(t)
        if x < margin_px:
            self.start = t - margin_px * self.spp
        elif x > self.width - margin_px:
            self.start = t - (self.width - margin_px) * self.spp
        self._clamp()

    @property
    def zoom_level(self) -> float:
        """0 = alles sichtbar, 1 = feinste Stufe (logarithmisch) – für den Zoomregler."""
        lo, hi = math.log(self.min_spp), math.log(self.max_spp)
        if hi - lo < 1e-9:
            return 1.0
        return float(min(max((hi - math.log(self.spp)) / (hi - lo), 0.0), 1.0))

    def set_zoom_level(self, level: float, anchor_x: float | None = None) -> None:
        lo, hi = math.log(self.min_spp), math.log(self.max_spp)
        target = math.exp(hi - min(max(level, 0.0), 1.0) * (hi - lo))
        self.zoom(self.spp / target, anchor_x)


def timeline_bounds(track_duration: float, clip_start: float | None, clip_duration: float) -> tuple[float, float]:
    """Scrollbereich: Fahrt und Clip, mit etwas Rand."""
    lo, hi = 0.0, max(track_duration, 1.0)
    if clip_start is not None:
        lo, hi = min(lo, clip_start), max(hi, clip_start + clip_duration)
    margin = max((hi - lo) * MARGIN_FRACTION, 30.0)
    return lo - margin, hi + margin


# --------------------------------------------------------------------------- Lineal

_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 3 * 3600, 6 * 3600, 12 * 3600]


@dataclass
class Ticks:
    major: list[float]
    minor: list[float]
    step: float  # Abstand der Hauptstriche in Sekunden
    frames: bool  # Hauptstriche in Frames (feinste Zoomstufen)


def choose_step(spp: float, fps: float, min_px: float = 90.0) -> tuple[float, bool]:
    """Kleinster „runder“ Abstand der Hauptstriche, der mindestens ``min_px`` Pixel breit ist."""
    need = spp * min_px
    frame = 1.0 / fps if fps > 0 else 0.0
    if frame and need < 1.0:
        for n in (1, 2, 5, 10, 15):
            if n * frame >= need:
                return n * frame, True
        return 1.0, False
    for s in _STEPS:
        if s >= need:
            return float(s), False
    return float(math.ceil(need / 86400) * 86400), False


def _minor_count(step: float, frames: bool, fps: float) -> int:
    if frames:
        n = int(round(step * fps))
        return n if n > 1 else 1
    for s, k in ((3600, 4), (1800, 3), (900, 3), (600, 5), (300, 5), (120, 4), (60, 4), (30, 3),
                 (15, 3), (10, 5), (5, 5), (2, 4), (1, 1)):
        if step >= s:
            return k if step == s else 4
    return 1


def ticks(axis: TimeAxis, min_px: float = 90.0) -> Ticks:
    step, frames = choose_step(axis.spp, axis.fps, min_px)
    sub = _minor_count(step, frames, axis.fps)
    first = math.floor(axis.start / step)
    last = math.ceil(axis.end / step)
    major = [k * step for k in range(first, last + 1)]
    minor: list[float] = []
    if sub > 1 and step / sub / axis.spp >= 6:
        for k in range(first, last + 1):
            for j in range(1, sub):
                minor.append((k + j / sub) * step)
    return Ticks(major, minor, step, frames)


def tick_label(t: float, step: float, fps: float, frames: bool) -> str:
    """Beschriftung eines Hauptstrichs (Track-Zeit)."""
    sign = "-" if t < -1e-9 else ""
    a = abs(t)
    if frames:
        whole = math.floor(a + 1e-9)
        f = int(round((a - whole) * fps))
        if f >= round(fps):
            whole, f = whole + 1, 0
        h, m, s = whole // 3600, (whole % 3600) // 60, whole % 60
        return f"{sign}{h}:{m:02d}:{s:02d}.{f:02d}" if f else f"{sign}{h}:{m:02d}:{s:02d}"
    s = int(round(a))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    if step >= 60 and sec == 0:
        return f"{sign}{h}:{m:02d}"
    return f"{sign}{h}:{m:02d}:{sec:02d}"


# --------------------------------------------------------------------------- Ziehen und Rückgängig


class OffsetDrag:
    """Clip ziehen: neuer Versatz aus Mausweg (Pixel), auf Frames eingerastet.

    Mit ``fine=True`` (Shift) bewegt sich der Clip zehnmal langsamer; Umschalten mitten im
    Ziehen setzt an der aktuellen Stelle ohne Sprung fort.
    """

    def __init__(self, start_offset: float, start_x: float, fps: float, fine_factor: float = 0.1):
        self.start_offset = float(start_offset)
        self.fps = float(fps)
        self.fine_factor = fine_factor
        self._anchor_x = float(start_x)
        self._anchor_offset = float(start_offset)
        self._fine = False
        self._last_x = float(start_x)
        self._last_raw = float(start_offset)

    def offset_at(self, x: float, spp: float, fine: bool = False, snap: bool = True) -> float:
        if fine != self._fine:
            self._anchor_x, self._anchor_offset, self._fine = self._last_x, self._last_raw, fine
        factor = self.fine_factor if self._fine else 1.0
        raw = self._anchor_offset + (float(x) - self._anchor_x) * spp * factor
        self._last_x, self._last_raw = float(x), raw
        return snap_to_frame(raw, self.fps) if snap else raw


class OffsetHistory:
    """Rückgängig/Wiederherstellen; aufeinanderfolgende kleine Schritte können zusammengefasst werden."""

    def __init__(self, limit: int = 200):
        self._undo: list[float] = []
        self._redo: list[float] = []
        self.limit = limit

    def record(self, before: float, after: float) -> None:
        if abs(before - after) < 1e-9:
            return
        self._undo.append(before)
        del self._undo[: -self.limit]
        self._redo.clear()

    def undo(self, current: float) -> float | None:
        if not self._undo:
            return None
        self._redo.append(current)
        return self._undo.pop()

    def redo(self, current: float) -> float | None:
        if not self._redo:
            return None
        self._undo.append(current)
        return self._redo.pop()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()


# --------------------------------------------------------------------------- Kurven


def minmax_envelope(t: np.ndarray, v: np.ndarray, axis: TimeAxis, columns: int | None = None
                    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Kurve für das Zeichnen verdichten: je Pixelspalte Minimum und Maximum.

    Gibt (x, vmin, vmax) zurück; Spalten ohne Daten fehlen. Ist die Kurve dünner als die
    Pixel (stark hineingezoomt), werden die Punkte direkt zurückgegeben (vmin == vmax).
    """
    t = np.asarray(t, dtype=float)
    v = np.asarray(v, dtype=float)
    columns = int(columns or axis.width)
    if t.size == 0:
        e = np.empty(0)
        return e, e, e
    # sichtbaren Bereich (plus je ein Punkt Rand) ausschneiden
    i0 = max(int(np.searchsorted(t, axis.start, "left")) - 1, 0)
    i1 = min(int(np.searchsorted(t, axis.end, "right")) + 1, t.size)
    t, v = t[i0:i1], v[i0:i1]
    ok = np.isfinite(v)
    t, v = t[ok], v[ok]
    if t.size == 0:
        e = np.empty(0)
        return e, e, e
    x = (t - axis.start) / axis.spp
    if t.size <= columns * 2:
        return x, v, v
    col = np.clip(np.floor(x).astype(int), -1, columns)
    order = np.argsort(col, kind="stable")
    col, v = col[order], v[order]
    starts = np.flatnonzero(np.r_[True, col[1:] != col[:-1]])
    vmin = np.minimum.reduceat(v, starts)
    vmax = np.maximum.reduceat(v, starts)
    return col[starts].astype(float) + 0.5, vmin, vmax
