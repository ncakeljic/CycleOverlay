"""Qt-Grundlagen für das Rendering: App-Instanz, Schriften, Farben, Text, Pixel-Export.

Regeln (PRD 2.3): nur QImage/QPainter/QFont – kein QPixmap, keine Widgets.
So ist der Renderer im Worker-Thread und ohne Fenster (Tests, Kommandozeile) nutzbar.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
)

from ..app_paths import resource_path
from .layout import Style, parse_color

_APP: QGuiApplication | None = None
_FONTS_LOADED = False

WEIGHT_LABEL = QFont.Weight.DemiBold
WEIGHT_VALUE = QFont.Weight.Bold
WEIGHT_UNIT = QFont.Weight.Medium


def ensure_app() -> QGuiApplication:
    """Eine Qt-Application sicherstellen (nötig für Schriften).

    Es wird eine QApplication (mit Widgets) erzeugt, damit später auch die GUI im selben
    Prozess laufen kann (Qt erlaubt nur eine Application-Instanz).
    """
    global _APP
    from PySide6.QtWidgets import QApplication

    app = QGuiApplication.instance()
    if app is None:
        override = os.environ.get("CYCLE_OVERLAY_QPA")
        if override:
            os.environ["QT_QPA_PLATFORM"] = override
        elif sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication([sys.argv[0] if sys.argv else "cycleoverlay"])
    _APP = app  # Referenz halten
    load_bundled_fonts()
    return app


def load_bundled_fonts() -> list[str]:
    """Mitgelieferte Schriften (Inter, OFL) registrieren."""
    global _FONTS_LOADED
    families: set[str] = set()
    if _FONTS_LOADED:
        return []
    font_dir = resource_path("fonts")
    for f in sorted(font_dir.glob("*.otf")) + sorted(font_dir.glob("*.ttf")):
        fid = QFontDatabase.addApplicationFont(str(f))
        if fid >= 0:
            families.update(QFontDatabase.applicationFontFamilies(fid))
    _FONTS_LOADED = True
    return sorted(families)


def qcolor(value: str) -> QColor:
    r, g, b, a = parse_color(value)
    return QColor(r, g, b, a)


def make_font(family: str, pixel_size: float, weight: QFont.Weight) -> QFont:
    f = QFont(family)
    f.setPixelSize(max(1, int(round(pixel_size))))
    f.setWeight(weight)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    try:  # gleich breite Ziffern (Qt >= 6.7), damit Zahlen beim Wechsel nicht springen
        f.setFeature(QFont.Tag("tnum"), 1)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    return f


@lru_cache(maxsize=64)
def _cap_ratio(family: str, weight: QFont.Weight) -> float:
    fm = QFontMetricsF(make_font(family, 200, weight))
    cap = fm.capHeight()
    return cap / 200 if cap > 0 else 0.72


def font_for_cap(family: str, cap_px: float, weight: QFont.Weight) -> QFont:
    """Schrift so wählen, dass Großbuchstaben/Ziffern ``cap_px`` hoch sind."""
    return make_font(family, cap_px / _cap_ratio(family, weight), weight)


def text_width(font: QFont, text: str) -> float:
    return QFontMetricsF(font).horizontalAdvance(text) if text else 0.0


def draw_text(p: QPainter, text: str, font: QFont, x: float, baseline: float, color: str, style: Style) -> None:
    """Text als Pfad zeichnen – mit optionalem Schatten und Kontur für Lesbarkeit über Video."""
    if not text:
        return
    path = QPainterPath()
    path.addText(QPointF(x, baseline), font, text)
    size = font.pixelSize()
    if style.shadow:
        p.fillPath(path.translated(size * 0.035, size * 0.05), QBrush(qcolor(style.shadow_color)))
    if style.outline:
        pen = QPen(qcolor(style.outline_color), max(size * 0.08, 1.0))
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.strokePath(path, pen)
    p.fillPath(path, QBrush(qcolor(color)))


def round_pen(color: str, width: float) -> QPen:
    pen = QPen(qcolor(color), max(width, 0.5))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def new_frame(width: int, height: int) -> QImage:
    img = QImage(width, height, QImage.Format.Format_RGBA8888_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    return img


def begin_painter(img: QImage) -> QPainter:
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    return p


def _image_bytes(img: QImage) -> bytes:
    bits = img.constBits()
    try:
        mv = memoryview(bits)
    except TypeError:  # ältere Bindings liefern einen Zeiger ohne Größe
        bits.setsize(img.sizeInBytes())  # type: ignore[attr-defined]
        mv = memoryview(bits)
    return mv.tobytes()[: img.sizeInBytes()]


def image_to_rgba(img: QImage) -> np.ndarray:
    """QImage -> numpy (H, W, 4) uint8 mit *nicht vormultiplizierten* RGBA-Werten."""
    straight = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h, bpl = straight.width(), straight.height(), straight.bytesPerLine()
    buf = np.frombuffer(_image_bytes(straight), dtype=np.uint8)
    rows = buf[: bpl * h].reshape(h, bpl)
    return np.ascontiguousarray(rows[:, : w * 4].reshape(h, w, 4))


def image_to_rgba_bytes(img: QImage) -> bytes:
    straight = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, bpl = straight.width(), straight.bytesPerLine()
    data = _image_bytes(straight)
    if bpl == w * 4:
        return data
    return image_to_rgba(straight).tobytes()
