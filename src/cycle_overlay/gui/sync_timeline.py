"""Sync-Zeitleiste: Fahrt und Videoclip wie in einem Schnittprogramm, Clip zum Syncen verschieben.

Spuren (von oben): Übersicht · Lineal · GPX-Kurve · Bewegung im Video · Videoclip mit Filmstreifen.
Die Logik (Zoom, Lineal, Einrasten, Rückgängig) steckt Qt-frei in ``core.sync_timeline``.
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.sync_timeline import OffsetDrag, TimeAxis, minmax_envelope, tick_label, ticks, timeline_bounds
from ..core.units import CHANNELS, format_offset, format_value
from .controller import Controller
from .form_kit import ElidedLabel, SegmentedControl


def _vsep() -> QWidget:
    line = QWidget()
    line.setFixedSize(1, 20)
    line.setStyleSheet("background: rgba(128,128,128,90)")
    return line

CURVE_CHANNELS = ("speed", "elevation", "power", "heart_rate", "cadence")

# Höhen der Spuren (Pixel)
OV_H = 14
RULER_H = 24
GPX_H = 78
MOTION_H = 34
VIDEO_H = 58
GAP = 3

C_BG = QColor(30, 31, 34)
C_LANE = QColor(40, 42, 46)
C_OUTSIDE = QColor(24, 25, 27)
C_GRID = QColor(255, 255, 255, 22)
C_TEXT = QColor(205, 208, 214)
C_DIM = QColor(140, 144, 152)
C_CURVE = QColor(90, 170, 255)
C_MOTION = QColor(120, 210, 140)
C_WIND = QColor(255, 160, 60)
C_PAUSE = QColor(255, 200, 60, 40)
C_CLIP = QColor(70, 110, 170)
C_CLIP_EDGE = QColor(150, 190, 255)
C_PLAYHEAD = QColor(255, 106, 31)
C_GHOST = QColor(120, 230, 140)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Zusammenhängende True-Bereiche als (Anfang, Ende exkl.)."""
    if mask.size == 0 or not mask.any():
        return []
    d = np.diff(np.r_[0, mask.astype(np.int8), 0])
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


class SyncLanes(QWidget):
    """Die gezeichneten Spuren samt Maus- und Tastaturbedienung."""

    viewChanged = Signal()  # Zoom/Scroll geändert (für den Zoomregler)
    pickDone = Signal()

    def __init__(self, controller: Controller, parent=None):
        super().__init__(parent)
        self.c = controller
        self.axis = TimeAxis(width=800)
        self.channel = "speed"
        self.pick_mode = False
        self._data_key: tuple | None = None
        self._mode: str | None = None  # clip | scrub | pan | overview
        self._drag: OffsetDrag | None = None
        self._last_x = 0.0
        self._hover_x: float | None = None
        self._pending_offset: float | None = None
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(30)
        self._apply_timer.timeout.connect(self._flush_offset)
        self._thumb_images: list[QImage] = []
        self._thumb_src = None
        self._pauses: list[tuple[float, float]] = []
        self._pause_src = None
        self._ranges: dict[str, tuple[float, float]] = {}
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(self.total_height())
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip("")

    # ---------------------------------------------------------------- Geometrie

    @staticmethod
    def total_height() -> int:
        return OV_H + GAP + RULER_H + GPX_H + GAP + MOTION_H + GAP + VIDEO_H + 4

    def lane_rects(self) -> dict[str, QRectF]:
        w = float(self.width())
        y = 0.0
        out = {"overview": QRectF(0, y, w, OV_H)}
        y += OV_H + GAP
        out["ruler"] = QRectF(0, y, w, RULER_H)
        y += RULER_H
        out["gpx"] = QRectF(0, y, w, GPX_H)
        y += GPX_H + GAP
        out["motion"] = QRectF(0, y, w, MOTION_H)
        y += MOTION_H + GAP
        out["video"] = QRectF(0, y, w, VIDEO_H)
        return out

    def lane_at(self, y: float) -> str | None:
        for name, r in self.lane_rects().items():
            if r.top() <= y <= r.bottom():
                return name
        return None

    # ---------------------------------------------------------------- Daten

    def clip_range(self) -> tuple[float, float] | None:
        m = self.c.model
        if not m.has_video:
            return None
        a, b = m.sync.window()  # type: ignore[union-attr]
        return a, b

    def playhead_t(self) -> float:
        m = self.c.model
        return m.track_time(self.c.position)

    def refresh(self) -> None:
        m = self.c.model
        self.axis.fps = float(m.fps()) if m.ready else 30.0
        track_dur = m.telemetry.duration if m.telemetry is not None else 0.0
        clip = self.clip_range()
        lo, hi = timeline_bounds(track_dur, clip[0] if clip else None, (clip[1] - clip[0]) if clip else 0.0)
        if self._mode == "clip":  # beim Ziehen nicht schrumpfen, sonst springt die Ansicht
            lo, hi = min(lo, self.axis.lo), max(hi, self.axis.hi)
        self.axis.set_bounds(lo, hi)
        # Rohdaten statt Telemetrie: Neu-Berechnen mit anderen Filtern soll die Ansicht nicht zurücksetzen
        key = (id(m.raw), str(m.video.path) if m.video is not None else None)
        if id(m.telemetry) != getattr(self, "_tel_id", None):
            self._tel_id = id(m.telemetry)
            self._ranges.clear()  # Werte-Bereich der Kurve nach neuer Filterung neu bestimmen
        if key != self._data_key:
            self._data_key = key
            self.fit_clip() if clip else self.axis.show_all()
        self.viewChanged.emit()
        self.update()

    def _value_range(self, ch: str) -> tuple[float, float]:
        if ch not in self._ranges:
            tel = self.c.model.telemetry
            v = tel.channels.get(ch) if tel is not None else None
            if v is None or not np.isfinite(v).any():
                self._ranges[ch] = (0.0, 1.0)
            else:
                lo, hi = float(np.nanmin(v)), float(np.nanmax(v))
                if ch == "speed":
                    lo = 0.0
                if hi - lo < 1e-6:
                    hi = lo + 1.0
                pad = (hi - lo) * 0.06
                self._ranges[ch] = (lo - (0 if ch == "speed" else pad), hi + pad)
        return self._ranges[ch]

    def _pause_runs(self) -> list[tuple[float, float]]:
        tel = self.c.model.telemetry
        if tel is None:
            return []
        if self._pause_src is not tel:
            self._pause_src = tel
            self._pauses = [(float(tel.t[a]), float(tel.t[min(b, tel.t.size - 1)])) for a, b in _runs(tel.pause_mask)]
        return self._pauses

    def _thumbs(self) -> list[QImage]:
        an = self.c.analysis
        if an is None:
            return []
        if self._thumb_src is not an:
            self._thumb_src = an
            imgs = []
            for th in an.thumbs:
                arr = np.ascontiguousarray(th)
                h, w, _ = arr.shape
                imgs.append(QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy())
            self._thumb_images = imgs
        return self._thumb_images

    # ---------------------------------------------------------------- Ansicht

    def fit_all(self) -> None:
        self.axis.show_all()
        self.viewChanged.emit()
        self.update()

    def fit_clip(self) -> None:
        clip = self.clip_range()
        if clip is None:
            self.fit_all()
            return
        self.axis.show(clip[0], clip[1], pad=0.35)
        self.viewChanged.emit()
        self.update()

    def resizeEvent(self, e) -> None:
        first = self.axis.width <= 10 or self._data_key is None
        self.axis.set_width(max(self.width(), 10))
        if first:
            self.refresh()
        self.viewChanged.emit()
        super().resizeEvent(e)

    # ---------------------------------------------------------------- Zeichnen

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), C_BG)
        r = self.lane_rects()
        small = QFont(self.font())
        small.setPointSizeF(max(small.pointSizeF() * 0.82, 7.5))
        p.setFont(small)
        m = self.c.model
        if not m.ready:
            p.setPen(C_DIM)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Zuerst eine GPX/FIT-Datei öffnen.")
            p.end()
            return
        self._paint_overview(p, r["overview"])
        self._paint_ruler(p, r["ruler"])
        for name in ("gpx", "motion", "video"):
            self._paint_lane_bg(p, r[name])
        self._paint_gpx(p, r["gpx"])
        self._paint_motion(p, r["motion"])
        self._paint_video(p, r["video"])
        self._paint_playhead(p, r)
        self._paint_hover(p, r)
        if self.pick_mode:
            p.setPen(C_PLAYHEAD)
            p.drawText(r["gpx"].adjusted(8, 4, -8, -4), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                       "Sync-Punkt: in der Kurve auf den Moment klicken, der zum aktuellen Videobild passt")
        if self.hasFocus():
            p.setPen(QPen(QColor(255, 255, 255, 40), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))
        p.end()

    def _paint_overview(self, p: QPainter, r: QRectF) -> None:
        a = self.axis
        span = max(a.hi - a.lo, 1e-9)

        def ox(t: float) -> float:
            return r.left() + (t - a.lo) / span * r.width()

        p.fillRect(r, C_OUTSIDE)
        tel = self.c.model.telemetry
        if tel is not None:
            p.fillRect(QRectF(ox(0), r.top() + 3, max(ox(tel.duration) - ox(0), 2), r.height() - 6), QColor(90, 95, 105))
        clip = self.clip_range()
        if clip:
            p.fillRect(QRectF(ox(clip[0]), r.top() + 1, max(ox(clip[1]) - ox(clip[0]), 3), r.height() - 2), C_CLIP_EDGE)
        p.setPen(QPen(QColor(255, 255, 255, 200), 1))
        p.setBrush(QColor(255, 255, 255, 30))
        p.drawRect(QRectF(ox(a.start), r.top() + 0.5, max(ox(a.end) - ox(a.start), 4), r.height() - 1))

    def _paint_ruler(self, p: QPainter, r: QRectF) -> None:
        a = self.axis
        tk = ticks(a)
        p.fillRect(r, C_LANE.darker(115))
        p.setPen(QPen(C_DIM, 1))
        for t in tk.minor:
            x = a.x_of(t)
            p.drawLine(QPointF(x, r.bottom() - 5), QPointF(x, r.bottom()))
        bottom = self.lane_rects()["video"].bottom()
        for t in tk.major:
            x = a.x_of(t)
            p.setPen(QPen(C_GRID, 1))
            p.drawLine(QPointF(x, r.bottom()), QPointF(x, bottom))
            p.setPen(QPen(C_TEXT, 1))
            p.drawLine(QPointF(x, r.bottom() - 10), QPointF(x, r.bottom()))
            p.drawText(QPointF(x + 3, r.top() + 12), tick_label(t, tk.step, a.fps, tk.frames))

    def _paint_lane_bg(self, p: QPainter, r: QRectF) -> None:
        p.fillRect(r, C_LANE)
        tel = self.c.model.telemetry
        if tel is None:
            return
        a = self.axis
        x0, x1 = a.x_of(0.0), a.x_of(tel.duration)
        if x0 > r.left():
            p.fillRect(QRectF(r.left(), r.top(), min(x0, r.right()) - r.left(), r.height()), C_OUTSIDE)
        if x1 < r.right():
            p.fillRect(QRectF(max(x1, r.left()), r.top(), r.right() - max(x1, r.left()), r.height()), C_OUTSIDE)

    def _lane_label(self, p: QPainter, r: QRectF, text: str) -> None:
        p.setPen(C_DIM)
        p.drawText(QRectF(r.left() + 6, r.top() + 2, r.width() - 12, 14), Qt.AlignmentFlag.AlignLeft, text)

    def _paint_gpx(self, p: QPainter, r: QRectF) -> None:
        a = self.axis
        tel = self.c.model.telemetry
        # Pausen
        for t0, t1 in self._pause_runs():
            if t1 < a.start or t0 > a.end:
                continue
            x0, x1 = a.x_of(t0), a.x_of(t1)
            p.fillRect(QRectF(x0, r.top(), max(x1 - x0, 1.0), r.height()), C_PAUSE)
        v = tel.channels.get(self.channel)
        title = CHANNELS[self.channel].title if self.channel in CHANNELS else self.channel
        if v is None or not np.isfinite(v).any():
            self._lane_label(p, r, f"{title}: keine Daten")
            return
        x, vmin, vmax = minmax_envelope(tel.t, v, a)
        lo, hi = self._value_range(self.channel)
        inner = r.adjusted(0, 16, 0, -3)

        def y(val):
            return inner.bottom() - (np.asarray(val) - lo) / (hi - lo) * inner.height()

        if x.size:
            p.save()
            p.setClipRect(r)
            ylo, yhi = y(vmin), y(vmax)
            if np.array_equal(vmin, vmax):
                poly = QPolygonF([QPointF(float(xx), float(yy)) for xx, yy in zip(x, yhi)])
                p.setPen(QPen(C_CURVE, 1.6))
                p.drawPolyline(poly)
            else:
                path = QPainterPath()
                path.moveTo(float(x[0]), float(yhi[0]))
                for xx, yy in zip(x[1:], yhi[1:]):
                    path.lineTo(float(xx), float(yy))
                for xx, yy in zip(x[::-1], ylo[::-1]):
                    path.lineTo(float(xx), float(yy))
                path.closeSubpath()
                p.setPen(QPen(C_CURVE, 1.0))
                fill = QColor(C_CURVE)
                fill.setAlpha(110)
                p.setBrush(fill)
                p.drawPath(path)
            p.restore()
        self._lane_label(p, r, f"{title}   ·   Pausen gelb")

    def _paint_motion(self, p: QPainter, r: QRectF) -> None:
        c = self.c
        clip = self.clip_range()
        if clip is None:
            self._lane_label(p, r, "Bewegung im Video – kein Video geladen")
            return
        an = c.analysis
        if an is None:
            if c.analysis_progress is not None:
                txt = f"Bewegung im Video wird berechnet … {c.analysis_progress * 100:.0f} %"
            elif c.analysis_error:
                txt = "Bewegung im Video: Analyse fehlgeschlagen"
            else:
                txt = "Bewegung im Video"
            self._lane_label(p, r, txt)
            return
        a = self.axis
        inner = r.adjusted(0, 3, 0, -2)
        wind = self._wind_norm(an)
        p.save()
        p.setClipRect(r)
        # Fahrtwind (Ton) als Fläche – bei stabilisierten Kameras das verlässlichste Signal
        if wind is not None:
            x, _lo, hi = minmax_envelope(clip[0] + an.motion_t, wind, a)
            if x.size:
                path = QPainterPath()
                path.moveTo(float(x[0]), inner.bottom())
                for xx, vv in zip(x, hi):
                    path.lineTo(float(xx), inner.bottom() - float(vv) * inner.height())
                path.lineTo(float(x[-1]), inner.bottom())
                path.closeSubpath()
                fill = QColor(C_WIND)
                fill.setAlpha(130)
                p.setPen(QPen(C_WIND, 1.0))
                p.setBrush(fill)
                p.drawPath(path)
        # Bewegung im Bild als Linie (bzw. Fläche, wenn es keinen Ton gibt)
        x, _lo, hi = minmax_envelope(clip[0] + an.motion_t, an.motion, a)
        if x.size:
            path = QPainterPath()
            path.moveTo(float(x[0]), inner.bottom() - float(hi[0]) * inner.height())
            for xx, vv in zip(x, hi):
                path.lineTo(float(xx), inner.bottom() - float(vv) * inner.height())
            if wind is None:
                path.lineTo(float(x[-1]), inner.bottom())
                path.lineTo(float(x[0]), inner.bottom())
                path.closeSubpath()
                fill = QColor(C_MOTION)
                fill.setAlpha(150)
                p.setBrush(fill)
            else:
                p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(C_MOTION, 1.0))
            p.drawPath(path)
        p.restore()
        label = ("Fahrtwind im Ton (orange) · Bewegung im Bild (grün)" if wind is not None
                 else "Bewegung im Video (kein Ton)")
        self._lane_label(p, r, label + ("  ·  Analyse aus dem Zwischenspeicher" if an.from_cache else ""))

    def _wind_norm(self, an) -> np.ndarray | None:
        """Fahrtwind für die Anzeige: geglättet und auf 0…1 gebracht (zwischengespeichert)."""
        if an.wind is None:
            return None
        if getattr(self, "_wind_src", None) is not an:
            w = np.convolve(an.wind, np.ones(24) / 24, mode="same") if an.wind.size > 24 else an.wind
            lo, hi = np.percentile(w, 2), np.percentile(w, 98)
            self._wind_cache = np.clip((w - lo) / max(hi - lo, 1e-9), 0.0, 1.0)
            self._wind_src = an
        return self._wind_cache

    def _paint_video(self, p: QPainter, r: QRectF) -> None:
        clip = self.clip_range()
        a = self.axis
        if clip is None:
            self._lane_label(p, r, "Kein Video – über „Video öffnen …“ links laden")
            return
        current = self.c.suggestion
        for sug in self.c.suggestions:
            sel = sug is current
            gx0 = a.x_of(sug.track_start)
            gx1 = a.x_of(sug.track_start + (clip[1] - clip[0]))
            ghost = QColor(C_GHOST)
            if not sel:
                ghost.setAlpha(120)
            p.setPen(QPen(ghost, 2.0 if sel else 1.0, Qt.PenStyle.DashLine))
            p.setBrush(QColor(120, 230, 140, 40 if sel else 15))
            box = QRectF(gx0, r.top() + 2, max(gx1 - gx0, 3), r.height() - 4)
            p.drawRoundedRect(box, 5, 5)
            p.setPen(ghost)
            p.drawText(QRectF(box.left() + 4, box.bottom() - 16, 90, 14), Qt.AlignmentFlag.AlignLeft,
                       f"{sug.rank} · {max(sug.score, 0) * 100:.0f} %")
        x0, x1 = a.x_of(clip[0]), a.x_of(clip[1])
        block = QRectF(x0, r.top() + 3, max(x1 - x0, 3.0), r.height() - 6)
        p.save()
        path = QPainterPath()
        path.addRoundedRect(block, 5, 5)
        p.setClipPath(path)
        p.fillRect(block, C_CLIP)
        imgs = self._thumbs()
        an = self.c.analysis
        if imgs and an is not None and an.thumb_t.size:
            th = block.height()
            tw = th * imgs[0].width() / max(imgs[0].height(), 1)
            left = max(block.left(), 0.0)
            right = min(block.right(), float(self.width()))
            # Kacheln an einem festen Raster ausrichten, damit sie beim Scrollen nicht flimmern
            k0 = math.floor((left - block.left()) / tw)
            k1 = math.ceil((right - block.left()) / tw)
            for k in range(k0, k1 + 1):
                tx = block.left() + k * tw
                t_vid = a.t_of(tx + tw / 2) - clip[0]
                i = int(np.clip(np.searchsorted(an.thumb_t, t_vid) - 1, 0, len(imgs) - 1))
                if i + 1 < len(imgs) and abs(an.thumb_t[i + 1] - t_vid) < abs(an.thumb_t[i] - t_vid):
                    i += 1
                p.drawImage(QRectF(tx, block.top(), tw, th), imgs[i])
            p.fillRect(block, QColor(20, 40, 80, 70))
        p.restore()
        p.setPen(QPen(C_CLIP_EDGE, 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(block, 5, 5)
        v = self.c.model.video
        label = f"{v.path.name}   Versatz {format_offset(self.c.model.project.offset_s)}"
        tr = QRectF(max(block.left(), 0) + 6, block.top() + 3, max(min(block.right(), self.width()) - max(block.left(), 0) - 12, 0), 14)
        if tr.width() > 40:
            p.setPen(QColor(0, 0, 0, 160))
            p.drawText(tr.translated(1, 1), Qt.AlignmentFlag.AlignLeft, label)
            p.setPen(QColor(255, 255, 255))
            p.drawText(tr, Qt.AlignmentFlag.AlignLeft, label)
        # Clip liegt außerhalb der Ansicht: Pfeil am Rand
        if x1 < 0 or x0 > self.width():
            p.setPen(C_CLIP_EDGE)
            txt = "◀ Clip" if x1 < 0 else "Clip ▶"
            p.drawText(r.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter |
                       (Qt.AlignmentFlag.AlignLeft if x1 < 0 else Qt.AlignmentFlag.AlignRight), txt)

    def _paint_playhead(self, p: QPainter, r: dict[str, QRectF]) -> None:
        x = self.axis.x_of(self.playhead_t())
        if not (-2 <= x <= self.width() + 2):
            return
        top = r["ruler"].top()
        p.setPen(QPen(C_PLAYHEAD, 1.5))
        p.drawLine(QPointF(x, top), QPointF(x, r["video"].bottom()))
        p.setBrush(C_PLAYHEAD)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF([QPointF(x - 6, top), QPointF(x + 6, top), QPointF(x, top + 8)]))

    def _paint_hover(self, p: QPainter, r: dict[str, QRectF]) -> None:
        if self._hover_x is None or self._mode is not None:
            return
        x = self._hover_x
        t = self.axis.t_of(x)
        p.setPen(QPen(QColor(255, 255, 255, 70), 1))
        p.drawLine(QPointF(x, r["ruler"].bottom()), QPointF(x, r["video"].bottom()))
        tel = self.c.model.telemetry
        txt = tick_label(t, 1 / self.axis.fps, self.axis.fps, True)
        if tel is not None and 0 <= t <= tel.duration and self.channel in tel.channels:
            val = float(np.interp(t, tel.t, tel.channels[self.channel]))
            txt += "   " + format_value(self.channel, val, self.c.model.project.units)
        fm = p.fontMetrics()
        w = fm.horizontalAdvance(txt) + 10
        bx = min(x + 8, self.width() - w - 2)
        box = QRectF(bx, r["gpx"].bottom() - 18, w, 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 170))
        p.drawRoundedRect(box, 3, 3)
        p.setPen(C_TEXT)
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, txt)

    # ---------------------------------------------------------------- Maus

    def _on_clip(self, pos: QPointF) -> bool:
        clip = self.clip_range()
        if clip is None or self.lane_at(pos.y()) != "video":
            return False
        x0, x1 = self.axis.x_of(clip[0]), self.axis.x_of(clip[1])
        return x0 - 4 <= pos.x() <= max(x1, x0 + 3) + 4

    def _ghost_at(self, pos: QPointF) -> int | None:
        """Index des Vorschlags-Rahmens unter der Maus (Video-Spur)."""
        clip = self.clip_range()
        if clip is None or self.lane_at(pos.y()) != "video":
            return None
        dur = clip[1] - clip[0]
        for i, sug in enumerate(self.c.suggestions):
            x0, x1 = self.axis.x_of(sug.track_start), self.axis.x_of(sug.track_start + dur)
            if x0 - 4 <= pos.x() <= max(x1, x0 + 3) + 4:
                return i
        return None

    def _scrub_to(self, x: float) -> None:
        t = self.axis.t_of(x)
        m = self.c.model
        if m.has_video:
            self.c.set_position(m.timeline_time_for_track(t))
        else:
            self.c.set_position(t)

    def _overview_to(self, x: float) -> None:
        a = self.axis
        t = a.lo + x / max(self.width(), 1) * (a.hi - a.lo)
        a.center_on(t)
        self.viewChanged.emit()
        self.update()

    def mousePressEvent(self, e: QMouseEvent) -> None:
        self.setFocus()
        pos = e.position()
        self._last_x = pos.x()
        lane = self.lane_at(pos.y())
        mods = e.modifiers()
        if e.button() == Qt.MouseButton.MiddleButton or (
                e.button() == Qt.MouseButton.LeftButton and mods & Qt.KeyboardModifier.AltModifier):
            self._mode = "pan"
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if e.button() != Qt.MouseButton.LeftButton or not self.c.model.ready:
            return
        if lane == "overview":
            self._mode = "overview"
            self._overview_to(pos.x())
            return
        if self.pick_mode and lane in ("gpx", "motion", "ruler"):
            self.c.sync_point(self.axis.t_of(pos.x()))
            self.pick_mode = False
            self.pickDone.emit()
            self.update()
            return
        ghost = self._ghost_at(pos)
        if ghost is not None and not self._on_clip(pos):
            self.c.show_suggestion(ghost)
            return
        if self._on_clip(pos):
            self._mode = "clip"
            self.c.stop()
            self.c.begin_offset_drag()
            self._drag = OffsetDrag(self.c.model.project.offset_s, pos.x(), self.axis.fps)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        self._mode = "scrub"
        self._scrub_to(pos.x())

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        pos = e.position()
        dx = pos.x() - self._last_x
        self._last_x = pos.x()
        if self._mode == "pan":
            self.axis.scroll_px(-dx)
            self.viewChanged.emit()
            self.update()
        elif self._mode == "overview":
            self._overview_to(pos.x())
        elif self._mode == "clip" and self._drag is not None:
            fine = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self._pending_offset = self._drag.offset_at(pos.x(), self.axis.spp, fine=fine)
            if not self._apply_timer.isActive():
                self._apply_timer.start()
        elif self._mode == "scrub":
            self._scrub_to(pos.x())
        else:
            self._hover_x = pos.x() if self.lane_at(pos.y()) in ("ruler", "gpx", "motion", "video") else None
            if self.pick_mode:
                self.setCursor(Qt.CursorShape.CrossCursor)
            elif self._on_clip(pos):
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            elif self._ghost_at(pos) is not None:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.unsetCursor()
            self.update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if self._mode == "clip":
            self._flush_offset()
            self.c.end_offset_drag()
            self._drag = None
        self._mode = None
        self.unsetCursor()
        self.refresh()

    def leaveEvent(self, _e) -> None:
        self._hover_x = None
        self.update()

    def _flush_offset(self) -> None:
        self._apply_timer.stop()
        if self._pending_offset is not None:
            v, self._pending_offset = self._pending_offset, None
            self.c.set_offset(v, record=False)

    def wheelEvent(self, e: QWheelEvent) -> None:
        ad, pd = e.angleDelta(), e.pixelDelta()
        shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        dx = pd.x() if not pd.isNull() else ad.x() / 2
        dy = pd.y() if not pd.isNull() else ad.y() / 2
        if shift and abs(dx) < 1e-9:  # Shift+Rad (Maus) = seitlich
            dx, dy = dy, 0
        if abs(dx) > abs(dy):
            self.axis.scroll_px(-dx)
        elif dy:
            factor = 1.0025 ** (ad.y() if not ad.isNull() else dy * 2)
            self.axis.zoom(factor, e.position().x())
        self.viewChanged.emit()
        self.update()
        e.accept()

    def event(self, e) -> bool:
        if e.type() == QEvent.Type.NativeGesture and e.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
            self.axis.zoom(1.0 + e.value(), e.position().x())
            self.viewChanged.emit()
            self.update()
            return True
        return super().event(e)

    # ---------------------------------------------------------------- Tastatur

    def keyPressEvent(self, e: QKeyEvent) -> None:
        key, mods = e.key(), e.modifiers()
        big = 10 if mods & Qt.KeyboardModifier.ShiftModifier else 1
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if key == Qt.Key.Key_Z and ctrl:
            (self.c.redo_offset if mods & Qt.KeyboardModifier.ShiftModifier else self.c.undo_offset)()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.c.nudge_offset(frames=(-1 if key == Qt.Key.Key_Left else 1) * big)
        elif key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.c.nudge_offset(seconds=(1 if key == Qt.Key.Key_Up else -1) * big)
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.axis.zoom(1.5, self.axis.x_of(self.playhead_t()))
        elif key == Qt.Key.Key_Minus:
            self.axis.zoom(1 / 1.5, self.axis.x_of(self.playhead_t()))
        elif key == Qt.Key.Key_Space:
            self.c.toggle_play()
        elif key == Qt.Key.Key_Escape and self.pick_mode:
            self.pick_mode = False
            self.pickDone.emit()
        else:
            super().keyPressEvent(e)
            return
        self.viewChanged.emit()
        self.update()

    def follow_playhead(self) -> None:
        if self._mode is None and self.c.is_playing:
            self.axis.ensure_visible(self.playhead_t())
            self.viewChanged.emit()
        self.update()


class SyncTimeline(QWidget):
    """Werkzeugleiste + Spuren."""

    def __init__(self, controller: Controller, parent=None):
        super().__init__(parent)
        self.c = c = controller
        self.lanes = SyncLanes(controller)

        self.all_btn = QPushButton("Alles")
        self.all_btn.setToolTip("Ganze Fahrt zeigen")
        self.all_btn.clicked.connect(self.lanes.fit_all)
        self.clip_btn = QPushButton("Clip")
        self.clip_btn.setToolTip("Auf den Videoclip zoomen")
        self.clip_btn.clicked.connect(self.lanes.fit_clip)
        self.zoom = QSlider(Qt.Orientation.Horizontal)
        self.zoom.setRange(0, 1000)
        self.zoom.setFixedWidth(130)
        self.zoom.setToolTip("Zoom (auch Mausrad bzw. Pinch auf dem Trackpad; Shift+Rad scrollt)")
        self.zoom.valueChanged.connect(self._on_zoom_slider)
        self.curve = QComboBox()
        self.curve.currentIndexChanged.connect(self._on_curve)
        self.pick_btn = QPushButton("Sync-Punkt …")
        self.pick_btn.setCheckable(True)
        self.pick_btn.setToolTip("Abspielkopf auf einen markanten Moment im Video stellen (z. B. Anfahren),\n"
                                 "diesen Knopf drücken und in der Kurve auf denselben Moment klicken.")
        self.pick_btn.toggled.connect(self._on_pick)
        self.lanes.pickDone.connect(lambda: self.pick_btn.setChecked(False))
        self.here_btn = QPushButton("Clip hierher")
        self.here_btn.setToolTip("Clip in die Mitte der aktuellen Ansicht legen")
        self.here_btn.clicked.connect(self._clip_here)
        self.undo_btn = QToolButton()
        self.undo_btn.setText("↶")
        self.undo_btn.setToolTip("Versatz rückgängig (Strg/Cmd+Z)")
        self.undo_btn.clicked.connect(c.undo_offset)
        self.redo_btn = QToolButton()
        self.redo_btn.setText("↷")
        self.redo_btn.setToolTip("Wiederherstellen (Strg/Cmd+Shift+Z)")
        self.redo_btn.clicked.connect(c.redo_offset)
        self.signal_mode = SegmentedControl([("auto", "Auto"), ("audio", "Ton"), ("video", "Bild"), ("all", "Alle")],
                                        c.sync_signals)
        self.signal_mode.setToolTip(
            "Womit Video und Fahrt verglichen werden:\n"
            "Ton = Fahrtwind im Mikrofon ↔ Tempo (am verlässlichsten, auch bei stabilisierten Kameras)\n"
            "Bild = Bewegung und Drehung im Bild ↔ Tempo und Kurven\n"
            "Auto = Ton, wenn das Video Ton hat, sonst Bild")
        self.signal_mode.changed.connect(self._on_signals)
        self.auto_btn = QPushButton("Auto-Sync")
        self.auto_btn.setToolTip("Video mit der Fahrt vergleichen (siehe Auswahl links) – bis zu fünf Vorschläge,\n"
                                 "das Video wird probeweise auf den besten gelegt (grün gestrichelt)")
        self.auto_btn.clicked.connect(self._auto_sync)
        self.accept_btn = QPushButton("Behalten")
        self.accept_btn.setToolTip("Video an der vorgeschlagenen Stelle lassen (mit Strg/Cmd+Z rückgängig)")
        self.accept_btn.clicked.connect(c.apply_suggestion)
        self.dismiss_btn = QPushButton("Verwerfen")
        self.dismiss_btn.setToolTip("Vorschläge verwerfen – Video zurück an die alte Stelle")
        self.dismiss_btn.clicked.connect(self._dismiss)
        self.prev_btn = QToolButton()
        self.prev_btn.setText("◀")
        self.prev_btn.setToolTip("Vorheriger Vorschlag")
        self.prev_btn.clicked.connect(lambda: self._step_suggestion(-1))
        self.next_btn = QToolButton()
        self.next_btn.setText("▶")
        self.next_btn.setToolTip("Nächster Vorschlag")
        self.next_btn.clicked.connect(lambda: self._step_suggestion(1))
        self.info = ElidedLabel("")
        self.match = ElidedLabel("")
        self.match.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.match.setToolTip("Wie gut Bewegung und Drehung im Video an der aktuellen Stelle zur Fahrt passen\n"
                              "(Tempo: Bewegung ↔ Geschwindigkeit · Kurven: Drehung ↔ Richtungswechsel).\n"
                              "Bei sauberem Sync typisch über 60 %.")
        self._match_timer = QTimer(self)
        self._match_timer.setSingleShot(True)
        self._match_timer.setInterval(120)
        self._match_timer.timeout.connect(self._update_match)

        # zwei Zeilen, damit auch schmale Fenster nichts überlappen: Ansicht/Bearbeiten · Auto-Sync
        view_row = QHBoxLayout()
        view_row.setContentsMargins(0, 0, 0, 0)
        for w in (self.all_btn, self.clip_btn, self.zoom, QLabel("Kurve"), self.curve, _vsep(), self.pick_btn,
                  self.here_btn, self.undo_btn, self.redo_btn):
            view_row.addWidget(w)
        view_row.addWidget(self.match, 1)
        sync_row = QHBoxLayout()
        sync_row.setContentsMargins(0, 0, 0, 0)
        for w in (QLabel("Vergleich"), self.signal_mode, self.auto_btn, self.prev_btn, self.next_btn,
                  self.accept_btn, self.dismiss_btn):
            sync_row.addWidget(w)
        sync_row.addWidget(self.info, 1)
        for w in (self.all_btn, self.clip_btn, self.pick_btn, self.here_btn, self.auto_btn, self.accept_btn,
                  self.dismiss_btn, self.curve):
            w.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.signal_mode.setFixedWidth(230)
        hint = ElidedLabel("Clip ziehen (Shift = fein) · ←/→ 1 Frame · ↑/↓ 1 s (Shift ×10) · Rad/Pinch = Zoom · "
                      "Shift+Rad bzw. Wischen = scrollen · Alt+Ziehen = Ansicht verschieben")
        hint.setStyleSheet("color: gray")
        f = hint.font()
        f.setPointSizeF(f.pointSizeF() * 0.85)
        hint.setFont(f)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 2)
        lay.setSpacing(3)
        lay.addLayout(view_row)
        lay.addLayout(sync_row)
        lay.addWidget(self.lanes)
        lay.addWidget(hint)

        self._updating = False
        self.lanes.viewChanged.connect(self._sync_zoom_slider)
        c.projectChanged.connect(self.refresh)
        c.positionChanged.connect(lambda _t: self.lanes.follow_playhead())
        c.analysisChanged.connect(self._on_analysis)
        c.suggestionChanged.connect(self._on_suggestion)
        c.projectChanged.connect(self._match_timer.start)
        c.analysisChanged.connect(self._match_timer.start)
        self.refresh()

    # ------------------------------------------------------------------

    def refresh(self) -> None:
        m = self.c.model
        self._updating = True
        cur = self.curve.currentData() or self.lanes.channel
        self.curve.clear()
        tel = m.telemetry
        for ch in CURVE_CHANNELS:
            if tel is not None and tel.available.get(ch, ch in tel.channels):
                self.curve.addItem(CHANNELS[ch].title, ch)
        idx = self.curve.findData(cur)
        self.curve.setCurrentIndex(max(idx, 0))
        if self.curve.count():
            self.lanes.channel = self.curve.currentData()
        self._updating = False
        has_video = m.has_video
        for w in (self.pick_btn, self.here_btn, self.auto_btn, self.clip_btn):
            w.setEnabled(has_video)
        self.undo_btn.setEnabled(self.c.offset_history.can_undo)
        self.redo_btn.setEnabled(self.c.offset_history.can_redo)
        self.lanes.refresh()
        self._on_analysis()
        self._on_suggestion()

    def _sync_zoom_slider(self) -> None:
        self._updating = True
        self.zoom.setValue(int(round(self.lanes.axis.zoom_level * 1000)))
        self._updating = False

    def _on_zoom_slider(self, v: int) -> None:
        if self._updating:
            return
        a = self.lanes.axis
        a.set_zoom_level(v / 1000, a.x_of(self.lanes.playhead_t()) if 0 <= a.x_of(self.lanes.playhead_t()) <= a.width
                         else None)
        self.lanes.update()

    def _on_curve(self, _i: int) -> None:
        if not self._updating and self.curve.currentData():
            self.lanes.channel = self.curve.currentData()
            self.lanes.update()

    def _on_pick(self, on: bool) -> None:
        self.lanes.pick_mode = on
        if on:
            self.lanes.setFocus()
        self.lanes.update()

    def _clip_here(self) -> None:
        clip = self.lanes.clip_range()
        if clip is None:
            return
        a = self.lanes.axis
        mid = a.start + a.span / 2
        self.c.set_offset(self.c.model.project.offset_s + (mid - (clip[1] - clip[0]) / 2) - clip[0])

    def _auto_sync(self) -> None:
        if self.c.analysis is None:
            self.info.setText("Video wird noch analysiert – gleich noch einmal versuchen.")
            return
        if not self.c.compute_suggestions():
            self.info.setText("Kein Vorschlag möglich – zu wenig Tempo- oder Richtungswechsel im Clip, "
                              "oder keine passende Stelle ohne längeren Stillstand.")
        else:
            self._show_current_suggestion()

    def _show_current_suggestion(self) -> None:
        s = self.c.suggestion
        clip = self.lanes.clip_range()
        if s is None or clip is None:
            return
        dur = clip[1] - clip[0]
        a = self.lanes.axis
        if not (a.start <= s.track_start and s.track_start + dur <= a.end):
            a.show(s.track_start, s.track_start + dur, pad=0.6)
        self.lanes.viewChanged.emit()
        self.lanes.update()

    def _step_suggestion(self, step: int) -> None:
        self.c.select_suggestion(step)
        self._show_current_suggestion()

    def _dismiss(self) -> None:
        self.c.clear_suggestions()

    def _on_analysis(self) -> None:
        c = self.c
        if c.suggestion is None:
            if c.analysis_progress is not None:
                self.info.setText(f"Video wird analysiert … {c.analysis_progress * 100:.0f} %")
            elif c.analysis_error:
                self.info.setText("Videoanalyse fehlgeschlagen")
                self.info.setToolTip(c.analysis_error)
            else:
                self.info.setText("")
        self.lanes.update()

    def _on_signals(self, mode: str) -> None:
        self.c.sync_signals = mode
        self._update_match()

    def _update_match(self) -> None:
        sc = self.c.match_now()
        if sc is None:
            self.match.setText("")
            return
        col = "#2e9e4f" if sc.percent >= 60 else "#d08a00" if sc.percent >= 40 else "#999999"
        self.match.setStyleSheet(f"color: {col}; font-weight: 600")
        self.match.setText(f"Übereinstimmung {sc.text()}")

    def _on_suggestion(self) -> None:
        s = self.c.suggestion
        many = len(self.c.suggestions) > 1
        self.accept_btn.setVisible(s is not None)
        self.dismiss_btn.setVisible(s is not None)
        self.prev_btn.setVisible(many)
        self.next_btn.setVisible(many)
        if s is not None:
            warn = "" if s.usable else " – unsicher, bitte prüfen"
            nr = f"Vorschlag {s.rank}/{len(self.c.suggestions)}" if many else "Vorschlag"
            self.info.setText(f"{nr}: {max(s.score, 0) * 100:.0f} % · Versatz {format_offset(s.offset_s)} · "
                              f"Sicherheit {s.confidence}{warn} – Video liegt probeweise dort")
            self.info.setToolTip(s.basis)
        else:
            self._on_analysis()
        self.lanes.update()

