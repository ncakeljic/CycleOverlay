"""Fortschritt (Typ ``progress``): „84,2 / 222 km“ bzw. „noch 138 km“ mit Fortschrittsbalken.

Für kumulierte Signale: Distanz, Höhenmeter (hoch/runter), Gesamt- und Fahrzeit.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QPainter, QPainterPath

from ..i18n import tr
from ..qt_support import WEIGHT_LABEL, WEIGHT_UNIT, WEIGHT_VALUE, draw_text, font_for_cap, qcolor, text_width
from .base import OverlayWidget


class ProgressWidget(OverlayWidget):
    def parts(self, i: int) -> list[tuple[str, str]]:
        """Textteile mit Rolle: ("value"|"small", Text) – z. B. [("value","84.2"),("small","/ 222"),("small","km")]."""
        cfg, v = self.cfg, self.values
        dec = v.decimals(cfg)
        total = v.total(cfg.channel)
        if cfg.progress_mode == "remaining":
            cur = v.counted(cfg.channel, i, cfg.update_rate_hz)
            left = max(total - cur, 0.0) if math.isfinite(total) and math.isfinite(cur) else math.nan
            num, unit = v.format_value(cfg.channel, left, dec)
            out = [("small", tr("remaining_prefix", v.language)), ("value", num)]
            if cfg.show_unit:
                out.append(("small", unit))
            out.append(("small", tr("remaining_suffix", v.language)))
        else:
            num, unit = v.text(cfg, i)
            tot, tunit = v.format_value(cfg.channel, total, dec)
            out = [("value", num), ("small", f"/ {tot}")]
            if cfg.show_unit:
                out.append(("small", tunit))
        return [(r, t) for r, t in out if t]

    def fraction(self, i: int) -> float:
        cfg, v = self.cfg, self.values
        total = v.total(cfg.channel)
        cur = v.raw(cfg.channel, i)
        if not (math.isfinite(total) and math.isfinite(cur)) or total <= 0:
            return math.nan
        return min(max(cur / total, 0.0), 1.0)

    def paint(self, p: QPainter, rect: QRectF, i: int) -> None:
        cfg, v, st = self.cfg, self.values, self.cfg.style
        self.draw_badge(p, rect)
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        pad = 0.08 * h
        bar_h = max(cfg.stroke * 2.6 * h, 0.1 * h)
        parts = self.parts(i)
        label = v.label(cfg) if cfg.label_position != "none" else ""
        gap = 0.05 * h

        def fonts(cap: float):
            return {"value": font_for_cap(st, cap, WEIGHT_VALUE), "small": font_for_cap(st, cap * 0.55, WEIGHT_UNIT),
                    "label": font_for_cap(st, cap * 0.42, WEIGHT_LABEL)}

        def width(fs) -> float:
            return sum(text_width(fs[r], t) for r, t in parts) + gap * max(len(parts) - 1, 0)

        cap = 0.34 * h
        fs = fonts(cap)
        avail = w - 2 * pad - (text_width(fs["label"], label) + gap * 2 if label else 0.0)
        if width(fs) > avail > 0:  # zu breit -> alles kleiner
            cap *= avail / width(fs)
            fs = fonts(cap)
        base = y + pad + 0.34 * h
        cx = x + w - pad - width(fs)
        for role, txt in parts:
            color = v.text_color(cfg, i) if role == "value" else st.label_color
            draw_text(p, txt, fs[role], cx, base, color, st)
            cx += text_width(fs[role], txt) + gap
        f_label = fs["label"]
        if label:
            draw_text(p, label, f_label, x + pad, base, st.label_color, st)

        track = QRectF(x + pad, y + h - pad - bar_h, w - 2 * pad, bar_h)
        path = QPainterPath()
        path.addRoundedRect(track, bar_h / 2, bar_h / 2)
        p.setPen(Qt.PenStyle.NoPen)
        p.fillPath(path, QBrush(qcolor(st.track_color)))
        frac = self.fraction(i)
        if math.isfinite(frac) and frac > 0:
            p.save()
            p.setClipPath(path)
            p.fillRect(QRectF(track.x(), track.y(), track.width() * frac, track.height()), qcolor(st.accent_color))
            p.restore()
