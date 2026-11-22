"""Rundinstrument / Tacho (PRD 3.4, Typ ``arc``)."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QPainter, QPolygonF

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


class ArcGaugeWidget(OverlayWidget):
    def paint(self, p: QPainter, rect: QRectF, i: int) -> None:
        cfg, v, st = self.cfg, self.values, self.cfg.style
        side = min(rect.width(), rect.height())
        cx, cy = rect.center().x(), rect.center().y()
        square = QRectF(cx - side / 2, cy - side / 2, side, side)
        self.draw_badge(p, square, ellipse=True)

        sw = cfg.stroke * side
        margin = sw / 2 + side * 0.03
        arc_rect = square.adjusted(margin, margin, -margin, -margin)
        radius = arc_rect.width() / 2

        sweep = min(max(cfg.sweep_deg, 30.0), 360.0)
        start = 270.0 - (360.0 - sweep) / 2  # Lücke unten, Qt-Winkel gegen den Uhrzeigersinn ab 3 Uhr
        frac = v.fraction(cfg, i)

        # Spur
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(round_pen(st.track_color, sw))
        p.drawArc(arc_rect, int(round(start * 16)), int(round(-sweep * 16)))

        # Skala
        if cfg.show_ticks:
            n = 7 if sweep >= 200 else 5
            p.setPen(round_pen(st.label_color, side * 0.012))
            r1 = radius - sw / 2 - side * 0.035
            r2 = r1 - side * 0.045
            for k in range(n):
                a = math.radians(start - sweep * k / (n - 1))
                p.drawLine(QPointF(cx + r1 * math.cos(a), cy - r1 * math.sin(a)),
                           QPointF(cx + r2 * math.cos(a), cy - r2 * math.sin(a)))
            if cfg.show_tick_labels:
                lo, hi = v.range(cfg)
                f_tick = font_for_cap(st, side * 0.045, WEIGHT_LABEL, scaled=False)
                rt = r2 - side * 0.07
                for k in range(n):
                    a = math.radians(start - sweep * k / (n - 1))
                    val = lo + (hi - lo) * k / (n - 1)
                    txt = f"{val:.0f}" if abs(hi - lo) >= n - 1 else f"{val:.1f}"
                    tw = text_width(f_tick, txt)
                    tx, ty = cx + rt * math.cos(a), cy - rt * math.sin(a)
                    draw_text(p, txt, f_tick, tx - tw / 2, ty + side * 0.0225, st.label_color, st)

        # Füllung
        if not math.isnan(frac) and frac > 0:
            span = int(round(-sweep * frac * 16))
            if span != 0:
                if st.shadow:
                    p.setPen(round_pen(st.shadow_color, sw))
                    p.drawArc(arc_rect.translated(side * 0.006, side * 0.009), int(round(start * 16)), span)
                p.setPen(round_pen(v.fill_color(cfg, i), sw))
                p.drawArc(arc_rect, int(round(start * 16)), span)

        # Zeiger
        if cfg.show_needle and not math.isnan(frac):
            a = math.radians(start - sweep * frac)
            r = radius - sw
            tip = QPointF(cx + r * math.cos(a), cy - r * math.sin(a))
            if cfg.needle_style == "pointer":
                # spitzer Zeiger: breit an der Achse, spitz außen, kurzes Gegengewicht
                wdt = side * 0.035
                nx, ny = -math.sin(a), -math.cos(a)  # Normale zur Zeigerrichtung (Bildschirmkoordinaten)
                tail = QPointF(cx - r * 0.18 * math.cos(a), cy + r * 0.18 * math.sin(a))
                poly = QPolygonF([tip, QPointF(cx + nx * wdt, cy + ny * wdt), tail,
                                  QPointF(cx - nx * wdt, cy - ny * wdt)])
                if st.shadow:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QBrush(qcolor(st.shadow_color)))
                    p.drawPolygon(poly.translated(side * 0.008, side * 0.012))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(qcolor(v.fill_color(cfg, i))))
                p.drawPolygon(poly)
            else:
                p.setPen(round_pen(st.text_color, side * 0.022))
                p.drawLine(QPointF(cx, cy), tip)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcolor(st.text_color)))
            p.drawEllipse(QPointF(cx, cy), side * 0.035, side * 0.035)

        # Text in der Mitte
        number, unit = v.text(cfg, i)
        label = v.label(cfg) if cfg.label_position != "none" else ""
        value_cap = 0.20 * side
        f_value = font_for_cap(st, value_cap, WEIGHT_VALUE)
        max_w = (radius - sw) * 1.5
        wv = text_width(f_value, number)
        if wv > max_w > 0:
            value_cap *= max_w / wv
            f_value = font_for_cap(st, value_cap, WEIGHT_VALUE)
            wv = text_width(f_value, number)

        if cfg.show_needle:
            base = cy + side * 0.30  # unter dem Zeigerdrehpunkt
        elif sweep <= 200:
            base = cy - side * 0.02
        else:
            base = cy + value_cap / 2
        draw_text(p, number, f_value, cx - wv / 2, base, v.text_color(cfg, i), st)

        if unit:
            f_unit = font_for_cap(st, 0.065 * side, WEIGHT_UNIT)
            draw_text(p, unit, f_unit, cx - text_width(f_unit, unit) / 2, base + side * 0.065 + side * 0.06, st.label_color, st)
        if label:
            f_label = font_for_cap(st, 0.06 * side, WEIGHT_LABEL)
            if sweep <= 200 or cfg.show_needle:
                lbase = base + side * 0.23 if not unit else base + side * 0.25
                lbase = min(lbase, square.bottom() - side * 0.04)
            else:
                lbase = base - value_cap - side * 0.07
            draw_text(p, label, f_label, cx - text_width(f_label, label) / 2, lbase, st.label_color, st)
