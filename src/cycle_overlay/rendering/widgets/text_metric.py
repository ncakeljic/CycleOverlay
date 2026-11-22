"""Wert + Beschriftung (PRD 3.4, Typ ``text``)."""

from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QPainter

from ..qt_support import WEIGHT_LABEL, WEIGHT_UNIT, WEIGHT_VALUE, draw_text, font_for_cap, text_width
from .base import OverlayWidget


class TextMetricWidget(OverlayWidget):
    def paint(self, p: QPainter, rect: QRectF, i: int) -> None:
        cfg, v, st = self.cfg, self.values, self.cfg.style
        self.draw_badge(p, rect)
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        pad = 0.08 * h
        label = v.label(cfg)
        number, unit = v.text(cfg, i)
        lp = cfg.label_position if label else "none"

        if lp in ("top", "bottom"):
            label_cap, value_cap, unit_cap = 0.15 * h, 0.50 * h, 0.20 * h
            # größere Beschriftung nimmt der Zahl Höhe weg (und umgekehrt)
            grown = 0.15 * h * (st.label_scale - 1.0)
            label_cap *= st.label_scale
            value_cap = min(max(value_cap - grown, 0.28 * h), 0.62 * h)
        elif lp == "left":
            label_cap, value_cap, unit_cap = 0.17 * h * st.label_scale, 0.56 * h, 0.22 * h
        else:
            label_cap, value_cap, unit_cap = 0.0, 0.62 * h, 0.24 * h
        unit_cap *= st.unit_scale

        gap_unit = 0.06 * h
        gap_label = 0.10 * h

        def fonts(scale: float):
            return (
                font_for_cap(st, label_cap * (scale if lp == "left" else 1.0), WEIGHT_LABEL, scaled=False)
                if label_cap else None,
                font_for_cap(st, value_cap * scale, WEIGHT_VALUE),
                font_for_cap(st, unit_cap * scale, WEIGHT_UNIT, scaled=False),
            )

        icon = cfg.show_icon

        def icon_w(scale: float) -> float:
            return (value_cap * scale * 1.05 + gap_label * scale) if icon else 0.0

        def row_width(fl, fv, fu, scale: float) -> float:
            wv = text_width(fv, number)
            wu = (gap_unit * scale + text_width(fu, unit)) if unit else 0.0
            wl = (text_width(fl, label) + gap_label * scale) if (lp == "left" and fl) else 0.0
            return icon_w(scale) + wl + wv + wu

        # Zu breiter Text (z. B. "1234 W" in einem schmalen Widget) wird verkleinert
        scale = 1.0
        f_label, f_value, f_unit = fonts(scale)
        avail = w - 2 * pad
        total = row_width(f_label, f_value, f_unit, scale)
        if total > avail > 0:
            scale = avail / total
            f_label, f_value, f_unit = fonts(scale)
            total = row_width(f_label, f_value, f_unit, scale)
        v_cap = value_cap * scale

        align = cfg.horizontal_align

        def start_x(width: float) -> float:
            if align == "right":
                return x + w - pad - width
            if align == "center":
                return x + (w - width) / 2
            return x + pad

        if lp == "top":
            label_base = y + pad + label_cap
            value_base = y + h - pad
        elif lp == "bottom":
            value_base = y + pad + v_cap
            label_base = y + h - pad
        else:
            value_base = y + h / 2 + v_cap / 2
            label_base = y + h / 2 + label_cap * (scale if lp == "left" else 1.0) / 2

        cx = start_x(total)
        if icon:
            from .icons import draw_icon, icon_for

            s_icon = v_cap * 1.05
            draw_icon(p, icon_for(cfg.channel), QRectF(cx, value_base - v_cap / 2 - s_icon / 2, s_icon, s_icon),
                      v.fill_color(cfg, i) if cfg.zones else st.accent_color,
                      st.shadow_color if st.shadow else None)
            cx += icon_w(scale)
        if lp == "left" and f_label is not None:
            draw_text(p, label, f_label, cx, label_base, st.label_color, st)
            cx += text_width(f_label, label) + gap_label * scale
        elif lp in ("top", "bottom") and f_label is not None:
            draw_text(p, label, f_label, start_x(text_width(f_label, label)), label_base, st.label_color, st)

        draw_text(p, number, f_value, cx, value_base, v.text_color(cfg, i), st)
        if unit:
            cx += text_width(f_value, number) + gap_unit * scale
            draw_text(p, unit, f_unit, cx, value_base, st.label_color, st)
