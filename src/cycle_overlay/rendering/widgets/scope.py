"""Scope (Typ ``scope``): laufender Graph der letzten Sekunden – wie ein Oszilloskop.

Rechts ist „jetzt“, nach links die letzten ``scope_window_s`` Sekunden. Die Höhe folgt dem
Bereich des Widgets (Min/Max, sonst Standard des Signals), damit der Graph ruhig bleibt.
"""

from __future__ import annotations

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


class ScopeWidget(OverlayWidget):
    def paint(self, p: QPainter, rect: QRectF, i: int) -> None:
        cfg, v, st = self.cfg, self.values, self.cfg.style
        self.draw_badge(p, rect)
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        pad = 0.07 * h
        show_text = cfg.label_position != "none"
        cap = 0.16 * h
        text_h = cap + 0.1 * h if show_text else 0.0
        left, right = x + pad, x + w - pad
        top, bottom = y + pad + text_h, y + h - pad
        if right - left < 4 or bottom - top < 4:
            return

        now = v.track_time(i)
        ts, vals = v.series(cfg.channel, now - cfg.scope_window_s, now, n=max(int(w / 3), 40))
        lo, hi = v.range(cfg)

        def sy(value):
            return bottom - (np.clip(value, lo, hi) - lo) / (hi - lo) * (bottom - top)

        xs = left + (ts - ts[0]) / (ts[-1] - ts[0]) * (right - left)
        ys = sy(vals)
        lw = max(cfg.stroke * 0.3 * h, 1.2)

        # Grundlinie
        p.setPen(round_pen(st.track_color, max(lw * 0.5, 1.0)))
        p.drawLine(QPointF(left, bottom), QPointF(right, bottom))

        # Kurve in zusammenhängenden Stücken (Lücken ohne Werte bleiben frei)
        ok = np.isfinite(ys)
        runs: list[tuple[int, int]] = []
        start = None
        for k, good in enumerate(ok):
            if good and start is None:
                start = k
            elif not good and start is not None:
                runs.append((start, k - 1))
                start = None
        if start is not None:
            runs.append((start, len(ok) - 1))
        line_color = v.fill_color(cfg, i)
        for a, b in runs:
            if b <= a:
                continue
            area = QPainterPath(QPointF(float(xs[a]), bottom))
            line = QPainterPath(QPointF(float(xs[a]), float(ys[a])))
            for k in range(a, b + 1):
                area.lineTo(float(xs[k]), float(ys[k]))
                if k > a:
                    line.lineTo(float(xs[k]), float(ys[k]))
            area.lineTo(float(xs[b]), bottom)
            area.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen)
            p.fillPath(area, QBrush(qcolor(st.track_color)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            if st.shadow:
                p.setPen(round_pen(st.shadow_color, lw * 1.5))
                p.drawPath(line.translated(lw * 0.4, lw * 0.6))
            p.setPen(round_pen(line_color, lw))
            p.drawPath(line)

        if ok[-1]:
            r = lw * 2.0
            p.setPen(round_pen(st.text_color, lw * 0.7))
            p.setBrush(QBrush(qcolor(line_color)))
            p.drawEllipse(QPointF(float(xs[-1]), float(ys[-1])), r, r)

        if show_text:
            number, unit = v.text(cfg, i)
            label = v.label(cfg)
            f_label = font_for_cap(st, cap * 0.75, WEIGHT_LABEL)
            f_value = font_for_cap(st, cap, WEIGHT_VALUE)
            f_unit = font_for_cap(st, cap * 0.7, WEIGHT_UNIT)
            base = y + pad + cap
            cx = left
            if label:
                draw_text(p, label, f_label, cx, base, st.label_color, st)
                cx += text_width(f_label, label) + 0.05 * h
            draw_text(p, number, f_value, cx, base, v.text_color(cfg, i), st)
            if unit:
                draw_text(p, unit, f_unit, cx + text_width(f_value, number) + 0.03 * h, base, st.label_color, st)

