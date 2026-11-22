"""Höhenmeter hoch und runter (Typ ``gainloss``): ▲ 1840 m / ▼ 1790 m in einem Widget."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QRectF
from PySide6.QtGui import QPainter

from ..qt_support import WEIGHT_LABEL, WEIGHT_UNIT, WEIGHT_VALUE, draw_text, font_for_cap, text_width
from .base import OverlayWidget
from .icons import draw_icon


class GainLossWidget(OverlayWidget):
    def rows(self, i: int) -> list[tuple[str, str, str]]:
        """[(Symbol, Zahl, Einheit)] für Anstieg und Abstieg."""
        cfg, v = self.cfg, self.values
        loss_cfg = replace(cfg, channel="elevation_loss", zones=[])  # gleiche Einstellungen, anderes Signal
        out = []
        for kind, c in (("up", cfg), ("down", loss_cfg)):
            num, unit = v.text(c, i)
            out.append((kind, num, unit))
        return out

    def paint(self, p: QPainter, rect: QRectF, i: int) -> None:
        cfg, v, st = self.cfg, self.values, self.cfg.style
        self.draw_badge(p, rect)
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        pad = 0.08 * h
        label = v.label(cfg) if cfg.label_position != "none" else ""
        label_cap = 0.13 * h * st.label_scale if label else 0.0
        top = y + pad
        if label:
            f_label = font_for_cap(st, label_cap, WEIGHT_LABEL, scaled=False)
            draw_text(p, label, f_label, x + pad, top + label_cap, st.label_color, st)
            top += label_cap + 0.08 * h
        rows = self.rows(i)
        row_h = (y + h - pad - top) / len(rows)
        cap = row_h * 0.62
        gap = 0.25 * cap

        def fonts(c: float):
            return font_for_cap(st, c, WEIGHT_VALUE), font_for_cap(st, c * 0.5 * st.unit_scale, WEIGHT_UNIT,
                                                                   scaled=False)

        f_val, f_unit = fonts(cap)
        widest = max(cap + gap + text_width(f_val, n) + (gap * 0.6 + text_width(f_unit, u) if u else 0.0)
                     for _, n, u in rows)
        if widest > w - 2 * pad > 0:
            cap *= (w - 2 * pad) / widest
            gap = 0.25 * cap
            f_val, f_unit = fonts(cap)
        colors = {"up": st.accent_color, "down": st.label_color}
        for k, (kind, num, unit) in enumerate(rows):
            base = top + row_h * k + row_h / 2 + cap / 2
            draw_icon(p, kind, QRectF(x + pad, base - cap, cap, cap), colors[kind],
                      st.shadow_color if st.shadow else None)
            cx = x + pad + cap + gap
            draw_text(p, num, f_val, cx, base, st.text_color if kind == "up" else st.label_color, st)
            if unit and cfg.show_unit:
                draw_text(p, unit, f_unit, cx + text_width(f_val, num) + gap * 0.6, base, st.label_color, st)
