"""Balkenanzeige (PRD 3.4, Typ ``bar``)."""

from __future__ import annotations

import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QPainter, QPainterPath

from ..qt_support import WEIGHT_LABEL, WEIGHT_UNIT, WEIGHT_VALUE, draw_text, font_for_cap, qcolor, text_width
from .base import OverlayWidget


class BarGaugeWidget(OverlayWidget):
    def paint(self, p: QPainter, rect: QRectF, i: int) -> None:
        self.draw_badge(p, rect)
        if self.cfg.orientation == "vertical":
            self._paint_vertical(p, rect, i)
        else:
            self._paint_horizontal(p, rect, i)

    def _bar(self, p: QPainter, track: QRectF, frac: float, color: str, vertical: bool) -> None:
        st = self.cfg.style
        r = (track.width() if vertical else track.height()) / 2
        path = QPainterPath()
        path.addRoundedRect(track, r, r)
        p.setPen(Qt.PenStyle.NoPen)
        p.fillPath(path, QBrush(qcolor(st.track_color)))
        if math.isnan(frac) or frac <= 0:
            return
        if vertical:
            hh = track.height() * frac
            fill = QRectF(track.x(), track.bottom() - hh, track.width(), hh)
        else:
            fill = QRectF(track.x(), track.y(), track.width() * frac, track.height())
        p.save()
        p.setClipPath(path)
        p.fillRect(fill, qcolor(color))
        p.restore()

    def _paint_horizontal(self, p: QPainter, rect: QRectF, i: int) -> None:
        cfg, v, st = self.cfg, self.values, self.cfg.style
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        pad = 0.07 * h
        bar_h = max(cfg.stroke * 3.2 * h, 0.12 * h)
        number, unit = v.text(cfg, i)
        label = v.label(cfg) if cfg.label_position != "none" else ""

        value_cap = 0.30 * h
        f_value = font_for_cap(st.font_family, value_cap, WEIGHT_VALUE)
        f_unit = font_for_cap(st.font_family, 0.15 * h, WEIGHT_UNIT)
        f_label = font_for_cap(st.font_family, 0.16 * h, WEIGHT_LABEL)
        base = y + pad + value_cap
        gap = 0.05 * h

        wu = text_width(f_unit, unit)
        wv = text_width(f_value, number)
        right = x + w - pad
        if unit:
            draw_text(p, unit, f_unit, right - wu, base, st.label_color, st)
            right -= wu + gap
        draw_text(p, number, f_value, right - wv, base, v.text_color(cfg, i), st)
        if label:
            draw_text(p, label, f_label, x + pad, base, st.label_color, st)

        track = QRectF(x + pad, y + h - pad - bar_h, w - 2 * pad, bar_h)
        self._bar(p, track, v.fraction(cfg, i), v.fill_color(cfg, i), vertical=False)

    def _paint_vertical(self, p: QPainter, rect: QRectF, i: int) -> None:
        cfg, v, st = self.cfg, self.values, self.cfg.style
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        pad = 0.06 * w
        number, unit = v.text(cfg, i)
        label = v.label(cfg) if cfg.label_position != "none" else ""

        value_cap = 0.26 * w
        f_value = font_for_cap(st.font_family, value_cap, WEIGHT_VALUE)
        wv = text_width(f_value, number)
        if wv > w - 2 * pad > 0:
            value_cap *= (w - 2 * pad) / wv
            f_value = font_for_cap(st.font_family, value_cap, WEIGHT_VALUE)
            wv = text_width(f_value, number)
        cx = x + w / 2
        top = y + pad
        draw_text(p, number, f_value, cx - wv / 2, top + value_cap, v.text_color(cfg, i), st)
        top += value_cap + 0.03 * h
        if unit:
            f_unit = font_for_cap(st.font_family, 0.13 * w, WEIGHT_UNIT)
            uc = 0.13 * w
            draw_text(p, unit, f_unit, cx - text_width(f_unit, unit) / 2, top + uc, st.label_color, st)
            top += uc + 0.03 * h

        bottom = y + h - pad
        if label:
            lc = min(0.15 * w, 0.05 * h)
            f_label = font_for_cap(st.font_family, lc, WEIGHT_LABEL)
            lw = text_width(f_label, label)
            if lw > w - 2 * pad > 0:
                f_label = font_for_cap(st.font_family, lc * (w - 2 * pad) / lw, WEIGHT_LABEL)
                lw = text_width(f_label, label)
            draw_text(p, label, f_label, cx - lw / 2, bottom, st.label_color, st)
            bottom -= lc + 0.03 * h

        bar_w = max(min(cfg.stroke * 4.5 * w, 0.5 * w), 0.15 * w)
        track = QRectF(cx - bar_w / 2, top, bar_w, max(bottom - top, 1.0))
        self._bar(p, track, v.fraction(cfg, i), v.fill_color(cfg, i), vertical=True)
