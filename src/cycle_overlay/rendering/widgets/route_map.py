"""Streckenkarte (PRD 3.4, Typ ``map``): Route als Linie, gefahrener Teil, aktuelle Position."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QPainter, QPolygonF

from ..grade import class_runs, grade_class, smooth_grade
from ..qt_support import qcolor, round_pen
from .base import OverlayWidget


class RouteMapWidget(OverlayWidget):
    def __init__(self, cfg, values):
        super().__init__(cfg, values)
        self._cache: dict = {}

    def _geometry(self, rect: QRectF):
        key = (round(rect.width(), 1), round(rect.height(), 1), self.cfg.scope, id(self.values))
        if key in self._cache:
            return self._cache[key]
        r = self.values.route(self.cfg.scope)
        x, y = r["x"], r["y"]
        ok = np.isfinite(x) & np.isfinite(y)
        x, y, t = x[ok], y[ok], r["t"][ok]
        pad = 0.08 * min(rect.width(), rect.height())
        if x.size < 2:
            geo = None
        else:
            xc, yc = (x.max() + x.min()) / 2, (y.max() + y.min()) / 2
            span = max(x.max() - x.min(), y.max() - y.min(), 1.0)
            s = min(rect.width() - 2 * pad, rect.height() - 2 * pad) / span
            cx, cy = rect.center().x(), rect.center().y()
            sx = cx + (x - xc) * s
            sy = cy - (y - yc) * s
            pts = [QPointF(float(a), float(b)) for a, b in zip(sx, sy)]
            cls = None
            if self.cfg.minimap.route_coloring == "gradient" and self.values.tel.available.get("elevation"):
                cls = grade_class(smooth_grade(self.values.tel)[r["idx"][ok]], self.cfg.grade_steps)
            geo = (pts, t, (xc, yc, s, cx, cy), cls)
        self._cache = {key: geo}
        return geo

    def paint(self, p: QPainter, rect: QRectF, i: int) -> None:
        cfg, v, st = self.cfg, self.values, self.cfg.style
        self.draw_badge(p, rect)
        geo = self._geometry(rect)
        if geo is None:
            return
        pts, t, (xc, yc, s, cx, cy), cls = geo
        lw = max(cfg.stroke * 0.35 * min(rect.width(), rect.height()), 1.2)
        p.setBrush(Qt.BrushStyle.NoBrush)
        full = QPolygonF(pts)
        if st.shadow:
            p.setPen(round_pen(st.shadow_color, lw * 1.6))
            p.drawPolyline(full.translated(lw * 0.4, lw * 0.6))
        if cls is None:
            p.setPen(round_pen(st.track_color, lw))
            p.drawPolyline(full)
        else:  # nach Steigung eingefärbt
            for c, a, b in class_runs(cls):
                p.setPen(round_pen(self.cfg.grade_colors[c], lw))
                p.drawPolyline(QPolygonF(pts[a : b + 1]))

        now = v.track_time(i)
        px, py = v.position_xy(cfg.scope, i)
        if not (math.isfinite(px) and math.isfinite(py)):
            return
        cur = QPointF(cx + (px - xc) * s, cy - (py - yc) * s)
        if cfg.show_ridden and cls is None:
            k = int(np.searchsorted(t, now, side="right"))
            if k > 0:
                ridden = QPolygonF(pts[:k] + [cur])
                p.setPen(round_pen(v.fill_color(cfg, i), lw))
                p.drawPolyline(ridden)
        if not rect.adjusted(-lw, -lw, lw, lw).contains(cur):
            return  # außerhalb des Kartenausschnitts (Bereich "Clip")
        radius = lw * 1.9
        p.setPen(round_pen(st.text_color, lw * 0.7))
        p.setBrush(QBrush(qcolor(v.fill_color(cfg, i))))
        p.drawEllipse(cur, radius, radius)
