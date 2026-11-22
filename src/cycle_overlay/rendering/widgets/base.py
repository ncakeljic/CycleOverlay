"""Basisklasse für Overlay-Widgets."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QPainter

from ..layout import WidgetConfig
from ..qt_support import qcolor
from ..values import FrameValues


class OverlayWidget:
    """Ein Widget zeichnet sich für Frame ``i`` in das übergebene Rechteck.

    Alle Maße werden relativ zur Rechteckgröße berechnet (auflösungsunabhängig).
    """

    def __init__(self, cfg: WidgetConfig, values: FrameValues):
        self.cfg = cfg
        self.values = values

    def paint(self, p: QPainter, rect: QRectF, i: int) -> None:  # pragma: no cover - abstrakt
        raise NotImplementedError

    def draw_badge(self, p: QPainter, rect: QRectF, ellipse: bool = False) -> None:
        st = self.cfg.style
        if not st.badge:
            return
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(qcolor(st.badge_color)))
        if ellipse:
            p.drawEllipse(rect)
        else:
            r = min(rect.width(), rect.height()) * max(0.0, min(st.badge_radius, 0.5))
            p.drawRoundedRect(rect, r, r)
        p.restore()
