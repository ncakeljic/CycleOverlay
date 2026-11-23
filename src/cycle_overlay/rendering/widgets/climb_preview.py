"""Anstiegs-Vorschau (Typ ``climb``) – ähnlich „ClimbPro“.

Zeigt das Höhenprofil der nächsten Kilometer (nach Steigung eingefärbt), die aktuelle
Position und eine Infozeile zum aktuellen bzw. nächsten Anstieg.
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QPainter, QPainterPath, QPolygonF

from ...core.units import convert
from ..i18n import tr
from ..grade import climb_status, climbs_for, grade_class, smooth_grade
from ..qt_support import WEIGHT_LABEL, WEIGHT_VALUE, draw_text, font_for_cap, qcolor, round_pen, text_width
from .base import OverlayWidget


def _fmt_dist(m: float, units: str) -> str:
    if units == "imperial":
        mi = m / 1609.344
        return f"{mi:.1f} mi" if mi >= 0.1 else f"{m / 0.3048:.0f} ft"
    return f"{m / 1000:.1f} km" if m >= 1000 else f"{m:.0f} m"


def _fmt_gain(m: float, units: str, lang: str = "de") -> str:
    v, unit = convert("elevation", m, units)
    sign = "−" if round(v) < 0 else ""
    return f"{sign}{abs(v):.0f} {tr('gain_unit_m', lang) if unit == 'm' else unit}"


def _fmt_pct(g: float) -> str:
    return f"{'−' if g < 0 else ''}{abs(g):.1f} %"


class ClimbPreviewWidget(OverlayWidget):
    def climb_info(self, i: int) -> tuple[str, str, object]:
        """(Überschrift, Detailtext, Anstieg/Abfahrt oder None) für Frame i."""
        cfg, v = self.cfg, self.values
        cs = cfg.climb
        climbs = climbs_for(v.tel, cs.min_length_m, cs.min_gain_m, cs.min_grade, cs.mode)
        d = v.raw("distance", i, 2.0)
        status, c = climb_status(climbs, d, cs.lookahead_m)
        u, lang = v.units, getattr(v, "language", "de")
        if status == "in":
            remaining = c.end_d - d
            left = c.end_ele - v.raw("elevation", i, 2.0)
            left = min(left, 0.0) if c.is_descent else max(left, 0.0)
            return (tr("descent" if c.is_descent else "climb", lang),
                    f"{tr('remaining', lang, dist=_fmt_dist(remaining, u))} · {_fmt_gain(left, u, lang)} · "
                    f"Ø {_fmt_pct(c.avg_grade)}", c)
        if status == "ahead":
            title = tr("descent_ahead" if c.is_descent else "climb_ahead", lang, dist=_fmt_dist(c.start_d - d, u))
            return (title, f"{_fmt_dist(c.length, u)} · {_fmt_gain(c.gain, u, lang)} · Ø {_fmt_pct(c.avg_grade)}", c)
        none = {"climbs": "no_climb", "descents": "no_descent"}.get(cs.mode, "flat")
        return (tr(none, lang), "", None)

    def paint(self, p: QPainter, rect: QRectF, i: int) -> None:
        cfg, v, st = self.cfg, self.values, self.cfg.style
        cs = cfg.climb
        tel = v.tel
        if not tel.available.get("elevation"):
            return
        title, detail, climb = self.climb_info(i)
        if cs.hide_when_none and climb is None:
            return
        self.draw_badge(p, rect)
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        pad = 0.06 * h
        info_h = 0.3 * h if cs.show_info else 0.0
        top, bottom = y + pad + info_h, y + h - pad
        left, right = x + pad, x + w - pad

        cur_d = v.raw("distance", i)
        if not math.isfinite(cur_d):
            return
        dist = tel.channels["distance"]
        ele = tel.channels["elevation"]
        a, b = cur_d - cs.behind_m, cur_d + cs.lookahead_m
        dd = np.linspace(a, b, 240)
        keep = np.concatenate([[True], np.diff(dist) > 1e-6])
        dk, ek = dist[keep], ele[keep]
        ok = np.isfinite(ek)
        dk, ek = dk[ok], ek[ok]
        if dk.size < 2:
            return
        inside = (dd >= dk[0]) & (dd <= dk[-1])
        ee = np.interp(dd, dk, ek)
        gg = np.interp(dd, dk, smooth_grade(tel)[keep][ok])
        e_vis = ee[inside]
        if e_vis.size < 2:
            return
        lo, hi = float(e_vis.min()), float(e_vis.max())
        if hi - lo < 30:  # flache Abschnitte nicht dramatisieren
            mid = (hi + lo) / 2
            lo, hi = mid - 15, mid + 15
        span = hi - lo
        lo -= span * 0.08
        hi += span * 0.08

        def sx(d):
            return left + (d - a) / (b - a) * (right - left)

        def sy(e):
            return bottom - (e - lo) / (hi - lo) * (bottom - top)

        xs, ys = sx(dd), sy(ee)
        p.setPen(Qt.PenStyle.NoPen)
        if cs.colored:
            cls = grade_class(gg, cfg.grade_steps)
            for n in range(dd.size - 1):
                if not (inside[n] and inside[n + 1]):
                    continue
                col = qcolor(cfg.grade_colors[int(cls[n])])
                if dd[n + 1] <= cur_d:
                    col.setAlpha(110)  # schon gefahren
                p.setBrush(QBrush(col))
                p.drawPolygon(QPolygonF([QPointF(xs[n], bottom), QPointF(xs[n], ys[n]),
                                         QPointF(xs[n + 1], ys[n + 1]), QPointF(xs[n + 1], bottom)]))
        else:
            area = QPainterPath()
            first = True
            for n in np.flatnonzero(inside):
                if first:
                    area.moveTo(QPointF(xs[n], bottom))
                    first = False
                area.lineTo(QPointF(xs[n], ys[n]))
            area.lineTo(QPointF(xs[np.flatnonzero(inside)[-1]], bottom))
            area.closeSubpath()
            p.fillPath(area, QBrush(qcolor(st.track_color)))

        line = QPainterPath()
        started = False
        for n in np.flatnonzero(inside):
            pt = QPointF(xs[n], ys[n])
            line.lineTo(pt) if started else line.moveTo(pt)
            started = True
        lw = max(0.018 * h, 1.0)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(round_pen(st.label_color, lw))
        p.drawPath(line)

        if climb is not None:  # Anstieg markieren: Klammer über dem Profil
            ca, cb = max(climb.start_d, a), min(climb.end_d, b)
            if cb > ca:
                yb = top - 0.02 * h
                p.setPen(round_pen(st.text_color, lw * 1.3))
                p.drawLine(QPointF(sx(ca), yb), QPointF(sx(cb), yb))
                for xx in (sx(ca), sx(cb)):
                    if left <= xx <= right:
                        p.drawLine(QPointF(xx, yb), QPointF(xx, yb + 0.06 * h))

        cx = sx(cur_d)
        cy = sy(float(np.interp(cur_d, dk, ek)))
        p.setPen(round_pen(st.text_color, lw))
        p.drawLine(QPointF(cx, top), QPointF(cx, bottom))
        p.setBrush(QBrush(qcolor(st.accent_color)))
        p.drawEllipse(QPointF(cx, cy), lw * 2.6, lw * 2.6)

        if cs.show_info:
            cap = 0.13 * h
            f_title = font_for_cap(st, cap, WEIGHT_VALUE)
            base = y + pad + cap
            # Titel und Details müssen nebeneinander zwischen left und right passen
            while text_width(f_title, title) > (right - left) * 0.6 and cap > 2:
                cap *= 0.92
                f_title = font_for_cap(st, cap, WEIGHT_VALUE)
            draw_text(p, title, f_title, left, base, st.text_color if climb else st.label_color, st)
            if detail:
                tx = left + text_width(f_title, title) + 0.05 * h
                maxw = right - tx
                dcap = cap * 0.85
                fd = font_for_cap(st, dcap, WEIGHT_LABEL)
                while text_width(fd, detail) > maxw and dcap > 2:
                    dcap *= 0.93
                    fd = font_for_cap(st, dcap, WEIGHT_LABEL)
                if maxw > 0:
                    draw_text(p, detail, fd, tx, base, st.label_color, st)
