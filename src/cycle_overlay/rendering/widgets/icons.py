"""Einfache Vektor-Symbole für die Mini-Anzeige (``show_icon``) – mit QPainter gezeichnet,
damit sie in jeder Größe scharf sind und keine Bilddateien brauchen.

Alle Symbole werden in ein Quadrat ``r`` gezeichnet; Strichstärke relativ zur Größe.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QPainter, QPainterPath, QPolygonF

from ..qt_support import qcolor, round_pen

#: Signal -> Symbol
ICON_FOR_CHANNEL = {
    "heart_rate": "heart",
    "power": "bolt",
    "speed": "gauge",
    "cadence": "crank",
    "elevation": "mountain",
    "elevation_gain": "up",
    "elevation_loss": "down",
    "gradient": "slope",
    "vam": "up",
    "distance": "flag",
    "temperature": "thermo",
    "elapsed_time": "clock",
    "moving_time": "clock",
    "time_of_day": "clock",
}


def icon_for(channel: str) -> str:
    return ICON_FOR_CHANNEL.get(channel, "dot")


def draw_icon(p: QPainter, kind: str, r: QRectF, color: str, shadow: str | None = None) -> None:
    """Symbol ``kind`` in das Quadrat ``r`` zeichnen (optional mit Schatten)."""
    if shadow:
        off = r.width() * 0.04
        _draw(p, kind, r.translated(off, off * 1.3), shadow)
    _draw(p, kind, r, color)


def _draw(p: QPainter, kind: str, r: QRectF, color: str) -> None:
    s = r.width()
    x, y = r.x(), r.y()
    lw = max(s * 0.11, 1.0)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = round_pen(color, lw)
    brush = QBrush(qcolor(color))

    def pt(fx: float, fy: float) -> QPointF:
        return QPointF(x + fx * s, y + fy * s)

    if kind == "heart":
        path = QPainterPath(pt(0.5, 0.88))
        path.cubicTo(pt(0.05, 0.58), pt(0.0, 0.22), pt(0.27, 0.15))
        path.cubicTo(pt(0.42, 0.11), pt(0.5, 0.25), pt(0.5, 0.3))
        path.cubicTo(pt(0.5, 0.25), pt(0.58, 0.11), pt(0.73, 0.15))
        path.cubicTo(pt(1.0, 0.22), pt(0.95, 0.58), pt(0.5, 0.88))
        p.fillPath(path, brush)
    elif kind == "bolt":
        poly = QPolygonF([pt(0.58, 0.05), pt(0.18, 0.56), pt(0.46, 0.56), pt(0.38, 0.95), pt(0.82, 0.4),
                          pt(0.53, 0.4)])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(brush)
        p.drawPolygon(poly)
    elif kind == "gauge":
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        arc = QRectF(x + 0.08 * s, y + 0.18 * s, 0.84 * s, 0.84 * s)
        p.drawArc(arc, 200 * 16, -220 * 16)
        c = pt(0.5, 0.6)
        a = math.radians(50)
        p.drawLine(c, QPointF(c.x() + 0.32 * s * math.cos(a), c.y() - 0.32 * s * math.sin(a)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(brush)
        p.drawEllipse(c, 0.08 * s, 0.08 * s)
    elif kind == "crank":
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(pt(0.5, 0.5), 0.3 * s, 0.3 * s)
        p.drawLine(pt(0.5, 0.5), pt(0.82, 0.86))
        p.drawLine(pt(0.7, 0.86), pt(0.94, 0.86))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(brush)
        p.drawEllipse(pt(0.5, 0.5), 0.09 * s, 0.09 * s)
    elif kind == "mountain":
        poly = QPolygonF([pt(0.02, 0.88), pt(0.36, 0.3), pt(0.52, 0.56), pt(0.66, 0.4), pt(0.98, 0.88)])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(brush)
        p.drawPolygon(poly)
    elif kind in ("up", "down"):
        up = kind == "up"
        tip, base = (0.1, 0.62) if up else (0.9, 0.38)
        poly = QPolygonF([pt(0.5, tip), pt(0.12, base), pt(0.88, base)])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(brush)
        p.drawPolygon(poly)
        p.setPen(round_pen(color, lw * 1.3))
        p.drawLine(pt(0.5, base), pt(0.5, 0.92 if up else 0.08))
    elif kind == "slope":
        poly = QPolygonF([pt(0.05, 0.85), pt(0.95, 0.85), pt(0.95, 0.25)])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(brush)
        p.drawPolygon(poly)
    elif kind == "flag":
        p.setPen(pen)
        p.drawLine(pt(0.22, 0.95), pt(0.22, 0.08))
        poly = QPolygonF([pt(0.22, 0.1), pt(0.86, 0.26), pt(0.22, 0.46)])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(brush)
        p.drawPolygon(poly)
    elif kind == "thermo":
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(pt(0.5, 0.12), pt(0.5, 0.6))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(brush)
        p.drawEllipse(pt(0.5, 0.75), 0.17 * s, 0.17 * s)
    elif kind == "clock":
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(pt(0.5, 0.5), 0.4 * s, 0.4 * s)
        p.drawLine(pt(0.5, 0.5), pt(0.5, 0.24))
        p.drawLine(pt(0.5, 0.5), pt(0.7, 0.6))
    else:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(brush)
        p.drawEllipse(pt(0.5, 0.5), 0.3 * s, 0.3 * s)
    p.restore()
