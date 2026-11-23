"""Balkenanzeige (PRD 3.4, Typ ``bar``)."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QPainter, QPainterPath, QPolygonF

from ..qt_support import WEIGHT_LABEL, WEIGHT_UNIT, WEIGHT_VALUE, draw_text, font_for_cap, qcolor, text_width
from .base import OverlayWidget


class BarGaugeWidget(OverlayWidget):
    def paint(self, p: QPainter, rect: QRectF, i: int) -> None:
        self.draw_badge(p, rect)
        if self.cfg.orientation == "vertical":
            self._paint_vertical(p, rect, i)
        else:
            self._paint_horizontal(p, rect, i)

    def _zone_segments(self, frac: float) -> list[tuple[float, float, str]]:
        """Abschnitte (von, bis, Farbe) als Anteile 0..1 – jede Zone in ihrer Farbe bis zum Füllstand."""
        cfg, v = self.cfg, self.values
        lo, hi = v.range(cfg)
        bounds = [(0.0, cfg.style.accent_color)]
        for z in sorted(cfg.zones, key=lambda z: z.from_value):
            f = min(max((z.from_value - lo) / (hi - lo), 0.0), 1.0)
            bounds.append((f, z.color))
        out = []
        for k, (a, color) in enumerate(bounds):
            b = bounds[k + 1][0] if k + 1 < len(bounds) else 1.0
            b = min(b, frac)
            if b > a:
                out.append((a, b, color))
        return out

    def _bar(self, p: QPainter, track: QRectF, frac: float, color: str, vertical: bool) -> None:
        st = self.cfg.style
        if self.cfg.bar_style == "marker":
            self._marker(p, track, frac, color, vertical)
            return
        r = (track.width() if vertical else track.height()) / 2
        path = QPainterPath()
        path.addRoundedRect(track, r, r)
        p.setPen(Qt.PenStyle.NoPen)
        p.fillPath(path, QBrush(qcolor(st.track_color)))
        if math.isnan(frac) or frac <= 0:
            return
        if self.cfg.zone_fill == "segments" and self.cfg.zones:
            parts = self._zone_segments(frac)
        else:
            parts = [(0.0, frac, color)]
        p.save()
        p.setClipPath(path)
        for a, b, c in parts:
            if vertical:
                fill = QRectF(track.x(), track.bottom() - track.height() * b, track.width(), track.height() * (b - a))
            else:
                fill = QRectF(track.x() + track.width() * a, track.y(), track.width() * (b - a), track.height())
            p.fillRect(fill, qcolor(c))
        p.restore()

    def _marker(self, p: QPainter, track: QRectF, frac: float, color: str, vertical: bool) -> None:
        """Dünne Skala, darauf eine Marke an der aktuellen Stelle (Wert im Bereich)."""
        st = self.cfg.style
        thin = (track.width() if vertical else track.height()) * 0.28
        if vertical:
            line = QRectF(track.center().x() - thin / 2, track.y(), thin, track.height())
        else:
            line = QRectF(track.x(), track.center().y() - thin / 2, track.width(), thin)
        path = QPainterPath()
        path.addRoundedRect(line, thin / 2, thin / 2)
        p.setPen(Qt.PenStyle.NoPen)
        if self.cfg.zones:  # Zonen als farbige Abschnitte der Skala
            p.save()
            p.setClipPath(path)
            for a, b, c in self._zone_segments(1.0):
                c_col = qcolor(c)
                c_col.setAlpha(min(c_col.alpha(), 170))
                if vertical:
                    p.fillRect(QRectF(line.x(), line.bottom() - line.height() * b, line.width(),
                                      line.height() * (b - a)), c_col)
                else:
                    p.fillRect(QRectF(line.x() + line.width() * a, line.y(), line.width() * (b - a),
                                      line.height()), c_col)
            p.restore()
        else:
            p.fillPath(path, QBrush(qcolor(st.track_color)))
        if math.isnan(frac):
            return
        size = (track.width() if vertical else track.height()) * 0.95
        if vertical:
            yy = track.bottom() - track.height() * frac
            x0 = line.x() - size * 0.15
            tri = QPolygonF([QPointF(x0, yy), QPointF(x0 - size * 0.8, yy - size * 0.5),
                             QPointF(x0 - size * 0.8, yy + size * 0.5)])
        else:
            xx = track.x() + track.width() * frac
            y0 = line.y() - size * 0.15
            tri = QPolygonF([QPointF(xx, y0), QPointF(xx - size * 0.5, y0 - size * 0.8),
                             QPointF(xx + size * 0.5, y0 - size * 0.8)])
        if st.shadow:
            p.setBrush(QBrush(qcolor(st.shadow_color)))
            p.drawPolygon(tri.translated(size * 0.06, size * 0.09))
        p.setBrush(QBrush(qcolor(color)))
        p.drawPolygon(tri)

    def _paint_horizontal(self, p: QPainter, rect: QRectF, i: int) -> None:
        cfg, v, st = self.cfg, self.values, self.cfg.style
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        pad = 0.07 * h
        bar_h = max(cfg.stroke * 3.2 * h, 0.12 * h)
        number, unit = v.text(cfg, i)
        label = v.label(cfg) if cfg.label_position != "none" else ""

        value_cap = 0.30 * h
        base = y + pad + value_cap
        avail = w - 2 * pad

        def fonts(k: float):
            uc = min(0.15 * h * st.unit_scale, value_cap) * k
            lc = min(0.16 * h * st.label_scale, value_cap) * k
            return (font_for_cap(st, value_cap * k, WEIGHT_VALUE), font_for_cap(st, uc, WEIGHT_UNIT, scaled=False),
                    font_for_cap(st, lc, WEIGHT_LABEL, scaled=False))

        # Beschriftung links, Zahl und Einheit rechts – alles in einer Zeile, sonst gemeinsam kleiner
        k = 1.0
        while True:
            f_value, f_unit, f_label = fonts(k)
            gap = 0.05 * h * k
            wu, wv = text_width(f_unit, unit), text_width(f_value, number)
            wl = text_width(f_label, label)
            need = wv + (gap + wu if unit else 0.0) + (wl + 2 * gap if label else 0.0)
            if need <= avail or k < 0.25:
                break
            k *= 0.92
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
        f_value = font_for_cap(st, value_cap, WEIGHT_VALUE)
        wv = text_width(f_value, number)
        if wv > w - 2 * pad > 0:
            value_cap *= (w - 2 * pad) / wv
            f_value = font_for_cap(st, value_cap, WEIGHT_VALUE)
            wv = text_width(f_value, number)
        cx = x + w / 2
        top = y + pad
        draw_text(p, number, f_value, cx - wv / 2, top + value_cap, v.text_color(cfg, i), st)
        top += value_cap + 0.03 * h
        if unit:
            f_unit = font_for_cap(st, 0.13 * w, WEIGHT_UNIT)
            uc = 0.13 * w
            draw_text(p, unit, f_unit, cx - text_width(f_unit, unit) / 2, top + uc, st.label_color, st)
            top += uc + 0.03 * h

        bottom = y + h - pad
        if label:
            lc = min(0.15 * w, 0.05 * h)
            f_label = font_for_cap(st, lc, WEIGHT_LABEL)
            lw = text_width(f_label, label)
            if lw > w - 2 * pad > 0:
                f_label = font_for_cap(st, lc * (w - 2 * pad) / lw, WEIGHT_LABEL)
                lw = text_width(f_label, label)
            draw_text(p, label, f_label, cx - lw / 2, bottom, st.label_color, st)
            bottom -= lc + 0.03 * h

        bar_w = max(min(cfg.stroke * 4.5 * w, 0.5 * w), 0.15 * w)
        track = QRectF(cx - bar_w / 2, top, bar_w, max(bottom - top, 1.0))
        self._bar(p, track, v.fraction(cfg, i), v.fill_color(cfg, i), vertical=True)
