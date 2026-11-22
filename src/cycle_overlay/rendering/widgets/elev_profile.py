"""Höhenprofil (PRD 3.4, Typ ``profile``): Kanal über der Distanz, Positionsmarke.

Standardmäßig die Höhe; jeder andere Kanal (z. B. Herzfrequenz) ist ebenfalls möglich.
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QPainter, QPainterPath

from ..qt_support import (
    WEIGHT_LABEL,
    WEIGHT_UNIT,
    WEIGHT_VALUE,
    draw_text,
    font_for_cap,
    qcolor,
    round_pen,
    text_width,
)
from .base import OverlayWidget


class ElevationProfileWidget(OverlayWidget):
    def __init__(self, cfg, values):
        super().__init__(cfg, values)
        self._cache: dict = {}

    def _data(self):
        key = (self.cfg.scope, self.cfg.channel, id(self.values))
        if key not in self._cache:
            r = self.values.route(self.cfg.scope)
            d, val = r["distance"], r[self.cfg.channel]
            ok = np.isfinite(d) & np.isfinite(val)
            self._cache = {key: (d[ok], val[ok], r["t"][ok])}
        return self._cache[key]

    def paint(self, p: QPainter, rect: QRectF, i: int) -> None:
        cfg, v, st = self.cfg, self.values, self.cfg.style
        self.draw_badge(p, rect)
        d, val, t = self._data()
        if d.size < 2 or d[-1] <= d[0]:
            return
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        pad = 0.06 * h
        at_marker = cfg.value_at_marker
        show_text = cfg.label_position != "none" and not at_marker
        text_h = 0.26 * h if show_text else 0.0
        top, bottom = y + pad + text_h, y + h - pad
        left, right = x + pad, x + w - pad

        vmin, vmax = float(val.min()), float(val.max())
        if vmax - vmin < 1e-6:
            vmin, vmax = vmin - 1, vmax + 1
        margin = (vmax - vmin) * 0.08
        vmin, vmax = vmin - margin, vmax + margin

        def sx(dist):
            return left + (dist - d[0]) / (d[-1] - d[0]) * (right - left)

        def sy(value):
            return bottom - (value - vmin) / (vmax - vmin) * (bottom - top)

        xs, ys = sx(d), sy(val)
        area = QPainterPath(QPointF(float(xs[0]), bottom))
        for a, b in zip(xs, ys):
            area.lineTo(float(a), float(b))
        area.lineTo(float(xs[-1]), bottom)
        area.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.fillPath(area, QBrush(qcolor(st.track_color)))

        cur_d = v.raw("distance", i)
        cur_x = sx(cur_d) if math.isfinite(cur_d) else None
        if cfg.show_ridden and cur_x is not None:
            fill = qcolor(v.fill_color(cfg, i))
            fill.setAlpha(min(fill.alpha(), 150))
            p.save()
            p.setClipRect(QRectF(left, top - 1, max(cur_x - left, 0.0), bottom - top + 2))
            p.fillPath(area, QBrush(fill))
            p.restore()

        line = QPainterPath(QPointF(float(xs[0]), float(ys[0])))
        for a, b in zip(xs[1:], ys[1:]):
            line.lineTo(float(a), float(b))
        lw = max(cfg.stroke * 0.25 * h, 1.0)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if st.shadow:
            p.setPen(round_pen(st.shadow_color, lw * 1.5))
            p.drawPath(line.translated(lw * 0.4, lw * 0.6))
        p.setPen(round_pen(st.label_color, lw))
        p.drawPath(line)

        if cur_x is not None and left - 1 <= cur_x <= right + 1:
            cur_v = v.raw(cfg.channel, i)
            p.setPen(round_pen(st.text_color, max(lw * 0.8, 1.0)))
            p.drawLine(QPointF(cur_x, top), QPointF(cur_x, bottom))
            if math.isfinite(cur_v):
                p.setBrush(QBrush(qcolor(v.fill_color(cfg, i))))
                p.setPen(round_pen(st.text_color, max(lw * 0.7, 1.0)))
                r = max(lw * 2.2, 2.5)
                p.drawEllipse(QPointF(cur_x, sy(cur_v)), r, r)
                if at_marker:
                    self._marker_text(p, rect, i, cur_x, sy(cur_v), r)

        if show_text:
            number, unit = v.text(cfg, i)
            label = v.label(cfg)
            cap = 0.15 * h
            f_label = font_for_cap(st, cap * 0.75, WEIGHT_LABEL)
            f_value = font_for_cap(st, cap, WEIGHT_VALUE)
            f_unit = font_for_cap(st, cap * 0.7, WEIGHT_UNIT)
            base = y + pad + cap
            cx = left
            if label:
                draw_text(p, label, f_label, cx, base, st.label_color, st)
                cx += text_width(f_label, label) + 0.04 * h
            draw_text(p, number, f_value, cx, base, v.text_color(cfg, i), st)
            if unit:
                cx += text_width(f_value, number) + 0.02 * h
                draw_text(p, unit, f_unit, cx, base, st.label_color, st)
                cx += text_width(f_unit, unit)
            alt = v.alt_text(cfg, i) if cfg.alt_unit else None
            if alt:
                txt = f"{alt[0]} {alt[1]}"
                draw_text(p, txt, f_unit, cx + 0.06 * h, base, st.label_color, st)

    def _marker_text(self, p: QPainter, rect: QRectF, i: int, mx: float, my: float, r: float) -> None:
        """Wert direkt neben der Positionsmarke – auf Wunsch zweizeilig in beiden Einheiten (355 M / 1165 FT)."""
        cfg, v, st = self.cfg, self.values, self.cfg.style
        number, unit = v.text(cfg, i)
        lines = [f"{number} {unit}".strip()]
        alt = v.alt_text(cfg, i) if cfg.alt_unit else None
        if alt:
            lines.append(f"{alt[0]} {alt[1]}")
        if st.label_uppercase:
            lines = [s.upper() for s in lines]
        cap = 0.11 * rect.height()
        f = font_for_cap(st, cap, WEIGHT_VALUE)
        widths = [text_width(f, s) for s in lines]
        gap = cap * 0.55
        block_h = cap * len(lines) + gap * (len(lines) - 1)
        # rechts neben die Marke, am rechten Rand nach links ausweichen; über der Linie
        x0 = mx + r * 2.2
        if x0 + max(widths) > rect.right() - 2:
            x0 = mx - r * 2.2 - max(widths)
        top = my - r * 1.5 - block_h
        if top < rect.top() + 2:
            top = my + r * 1.5
        for k, (s, w_) in enumerate(zip(lines, widths)):
            color = v.text_color(cfg, i) if k == 0 else st.label_color
            draw_text(p, s, f, x0, top + cap * (k + 1) + gap * k, color, st)
