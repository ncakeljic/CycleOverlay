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

        # Bereiche, die der Text in der Mitte nicht berühren darf (Skalenzahlen, Zeigerachse)
        occupied: list[QRectF] = []

        # Skala
        if cfg.show_ticks:
            n = 7 if sweep >= 200 else 5
            full = sweep >= 359.0  # geschlossener Kreis: letzter Strich fiele auf den ersten
            steps = n if full else n - 1
            p.setPen(round_pen(st.label_color, side * 0.012))
            r1 = radius - sw / 2 - side * 0.035
            r2 = r1 - side * 0.045
            for k in range(n):
                if full and k == n - 1:
                    break
                a = math.radians(start - sweep * k / steps)
                p.drawLine(QPointF(cx + r1 * math.cos(a), cy - r1 * math.sin(a)),
                           QPointF(cx + r2 * math.cos(a), cy - r2 * math.sin(a)))
            if cfg.show_tick_labels:
                lo, hi = v.range(cfg)
                f_tick = font_for_cap(st, side * 0.045, WEIGHT_LABEL, scaled=False)
                rt = r2 - side * 0.07
                for k in range(n - 1 if full else n):
                    a = math.radians(start - sweep * k / steps)
                    val = lo + (hi - lo) * k / steps
                    txt = f"{val:.0f}" if abs(hi - lo) >= n - 1 else f"{val:.1f}"
                    tw = text_width(f_tick, txt)
                    tx, ty = cx + rt * math.cos(a), cy - rt * math.sin(a)
                    draw_text(p, txt, f_tick, tx - tw / 2, ty + side * 0.0225, st.label_color, st)
                    pad = side * 0.012
                    occupied.append(QRectF(tx - tw / 2 - pad, ty - side * 0.0225 - pad, tw + 2 * pad,
                                           side * 0.045 + 2 * pad))

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

        # Text in der Mitte – als Block, der so lange verkleinert wird, bis er nichts berührt
        if cfg.show_needle:
            hub = side * 0.05
            occupied.append(QRectF(cx - hub, cy - hub, 2 * hub, 2 * hub))
        self._paint_center_text(p, i, side, cx, cy, radius - sw, sweep, square, occupied)

    def _paint_center_text(self, p: QPainter, i: int, side: float, cx: float, cy: float, inner: float,
                           sweep: float, square: QRectF, occupied: list[QRectF]) -> None:
        cfg, v, st = self.cfg, self.values, self.cfg.style
        number, unit = v.text(cfg, i)
        if not cfg.show_unit:
            unit = ""
        label = v.label(cfg) if cfg.label_position != "none" else ""
        needle = cfg.show_needle

        def layout(k: float):
            """Fonts und Rechtecke für Skalierung k: [(Text, Font, x, Grundlinie, Farbe)], Rechtecke."""
            vc, uc, lc = 0.20 * side * k, 0.065 * side * k * st.unit_scale, 0.06 * side * k * st.label_scale
            fv = font_for_cap(st, vc, WEIGHT_VALUE)
            fu = font_for_cap(st, uc, WEIGHT_UNIT, scaled=False)
            fl = font_for_cap(st, lc, WEIGHT_LABEL, scaled=False)
            wv, wu, wl = text_width(fv, number), text_width(fu, unit), text_width(fl, label)
            gap = 0.035 * side * k
            items = []
            if needle:
                # unter der Zeigerachse: Zahl mit Einheit dahinter, darunter die Beschriftung
                top = cy + side * 0.075
                base_v = top + vc
                ug = max(gap * 0.8, 2.0)
                row = wv + (ug + wu if unit else 0.0)
                x = cx - row / 2
                items.append((number, fv, x, base_v, v.text_color(cfg, i), vc))
                if unit:
                    items.append((unit, fu, x + wv + ug, base_v, st.label_color, uc))
                if label:
                    items.append((label, fl, cx - wl / 2, base_v + gap * 1.4 + lc, st.label_color, lc))
            else:
                # Zahl mittig; halber Bogen: etwas höher. Einheit darunter, Beschriftung darüber
                # (halber Bogen: Beschriftung unter der Einheit, oben ist der Bogen)
                base_v = cy - side * 0.02 if sweep <= 200 else cy + vc / 2
                items.append((number, fv, cx - wv / 2, base_v, v.text_color(cfg, i), vc))
                below = base_v
                if unit:
                    below = base_v + gap + uc
                    items.append((unit, fu, cx - wu / 2, below, st.label_color, uc))
                if label:
                    if sweep <= 200:
                        items.append((label, fl, cx - wl / 2, below + gap + lc, st.label_color, lc))
                    else:
                        items.append((label, fl, cx - wl / 2, base_v - vc - gap * 1.3, st.label_color, lc))
            # Rechteck je Text: Großbuchstabenhöhe, Unterlänge nur wo nötig (g, p, y …)
            rects = [QRectF(x, b - c, text_width(f, t), c * (1.25 if any(ch in "gjpqy,()" for ch in t) else 1.04))
                     for t, f, x, b, _col, c in items]
            return items, rects

        start = 270.0 - (360.0 - sweep) / 2

        def covered(px: float, py: float) -> bool:
            """Liegt die Richtung (vom Mittelpunkt aus) im Bereich des Bogens?"""
            ang = math.degrees(math.atan2(cy - py, px - cx)) % 360.0
            return (start - ang) % 360.0 <= sweep + 4.0

        def fits(rects: list[QRectF]) -> bool:
            limit = square.adjusted(side * 0.02, side * 0.02, -side * 0.02, -side * 0.02)
            for k, r in enumerate(rects):
                if not limit.contains(r):
                    return False
                # nicht über den Bogen: Ecken innerhalb des Innenradius – außer in der Lücke des Bogens
                for px, py in ((r.left(), r.top()), (r.right(), r.top()), (r.left(), r.bottom()),
                               (r.right(), r.bottom())):
                    if math.hypot(px - cx, py - cy) > inner * 0.98 and covered(px, py):
                        return False
                if any(r.intersects(o) for o in occupied):
                    return False
                if any(r.intersects(o) for o in rects[k + 1:]):
                    return False
            return True

        k = 1.0
        items, rects = layout(k)
        while not fits(rects) and k > 0.12:
            k *= 0.9
            items, rects = layout(k)
        for t, f, x, b, col, _c in items:
            draw_text(p, t, f, x, b, col, st)
