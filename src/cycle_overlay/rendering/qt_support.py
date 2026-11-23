"""Qt-Grundlagen für das Rendering: App-Instanz, Schriften, Farben, Text, Pixel-Export.

Regeln (PRD 2.3): nur QImage/QPainter/QFont – kein QPixmap, keine Widgets.
So ist der Renderer im Worker-Thread und ohne Fenster (Tests, Kommandozeile) nutzbar.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
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
    QTransform,
)

from ..app_paths import resource_path
from .layout import Style, parse_color

_APP: QGuiApplication | None = None
_FONTS_LOADED = False

#: Rollen eines Textes – die tatsächliche Stärke kommt aus dem Style (value_weight/label_weight)
WEIGHT_LABEL = QFont.Weight.DemiBold
WEIGHT_VALUE = QFont.Weight.Bold
WEIGHT_UNIT = QFont.Weight.Medium

_QT_WEIGHT = {300: QFont.Weight.Light, 400: QFont.Weight.Normal, 500: QFont.Weight.Medium,
              600: QFont.Weight.DemiBold, 700: QFont.Weight.Bold, 900: QFont.Weight.Black}


class StyledFont(QFont):
    """QFont mit zusätzlicher horizontaler Streckung (``xscale``).

    Die Streckung wird beim Zeichnen als Transformation angewendet und funktioniert damit mit jeder
    Schrift und auf jedem System gleich (Qt kann Breiten sonst nur, wenn die Schrift sie mitbringt).
    """

    xscale: float = 1.0


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


def make_font(family: str, pixel_size: float, weight: QFont.Weight, *, italic: bool = False,
              xscale: float = 1.0, tracking: float = 0.0) -> StyledFont:
    f = StyledFont(family)
    f.setPixelSize(max(1, int(round(pixel_size))))
    f.setWeight(weight)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    if italic:
        f.setItalic(True)  # ohne Kursivschnitt neigt Qt die Schrift selbst
    if tracking:
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 100.0 + float(tracking))
    f.xscale = float(xscale) if xscale > 0 else 1.0
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


def font_for_cap(style: "Style | str", cap_px: float, role: QFont.Weight, *, scaled: bool = True) -> StyledFont:
    """Schrift so wählen, dass Großbuchstaben/Ziffern ``cap_px`` hoch sind.

    ``style``: der Style des Widgets (Schrift, Stärken, Breite, Laufweite, Kursiv, Größen) oder nur
    ein Familienname. ``role``: ``WEIGHT_VALUE`` (Zahl), ``WEIGHT_LABEL`` (Beschriftung) oder
    ``WEIGHT_UNIT`` (Einheit). Mit ``scaled`` werden Beschriftung/Einheit um ``label_scale``/
    ``unit_scale`` vergrößert – ``False``, wenn der Aufrufer die Größe schon eingerechnet hat.
    """
    if isinstance(style, str):
        return make_font(style, cap_px / _cap_ratio(style, role), role)
    from .typography import WEIGHTS

    if role == WEIGHT_VALUE:
        key, factor = style.value_weight, 1.0
    elif role == WEIGHT_UNIT:
        key, factor = style.label_weight, style.unit_scale
    else:
        key, factor = style.label_weight, style.label_scale
    weight = _QT_WEIGHT.get(WEIGHTS.get(key, ("", 700))[1], role)
    cap = cap_px * (factor if scaled else 1.0)
    return make_font(style.font_family, cap / _cap_ratio(style.font_family, weight), weight,
                     italic=style.italic, xscale=style.font_width, tracking=style.tracking)


def text_width(font: QFont, text: str) -> float:
    """Breite des Textes inkl. Streckung; kursiv zusätzlich der Überhang der geneigten Zeichen."""
    if not text:
        return 0.0
    w = QFontMetricsF(font).horizontalAdvance(text) * getattr(font, "xscale", 1.0)
    if font.italic():
        w += 0.2 * QFontMetricsF(font).capHeight()  # Qt neigt ca. 11° -> oben ragt das letzte Zeichen über
    return w


class TextRecorder:
    """Merkt sich, wo Texte gezeichnet wurden (genaue Umrisse der Zeichen, in Bildkoordinaten).

    Nur für Tests: prüft, dass sich Texte eines Widgets nicht überlappen und im Widget bleiben.
    """

    active: "TextRecorder | None" = None

    def __init__(self) -> None:
        self.items: list[tuple[str, QRectF]] = []

    def __enter__(self) -> "TextRecorder":
        TextRecorder.active = self
        return self

    def __exit__(self, *_exc) -> None:
        TextRecorder.active = None


def text_path(text: str, font: QFont, x: float, baseline: float) -> QPainterPath:
    """Umriss des Textes an (x, Grundlinie) – mit horizontaler Streckung der Schrift."""
    path = QPainterPath()
    path.addText(QPointF(0.0, 0.0), font, text)
    xs = getattr(font, "xscale", 1.0)
    return QTransform().translate(x, baseline).scale(xs, 1.0).map(path)


def _record(p: QPainter, text: str, path: QPainterPath) -> None:
    rec = TextRecorder.active
    if rec is not None and text.strip():
        rec.items.append((text, p.worldTransform().mapRect(path.boundingRect())))


def draw_plain_text(p: QPainter, text: str, font: QFont, x: float, baseline: float, color) -> None:
    """Text ohne Schatten/Kontur (Kompass, Maßstab …) – berücksichtigt die Schriftbreite wie draw_text."""
    if not text:
        return
    path = text_path(text, font, x, baseline)
    _record(p, text, path)
    p.fillPath(path, QBrush(color if isinstance(color, QColor) else qcolor(color)))


def draw_text(p: QPainter, text: str, font: QFont, x: float, baseline: float, color: str, style: Style) -> None:
    """Text als Pfad zeichnen – mit optionalem Schatten und Kontur für Lesbarkeit über Video."""
    if not text:
        return
    path = text_path(text, font, x, baseline)
    _record(p, text, path)
    size = font.pixelSize()
    if style.shadow:
        d = getattr(style, "shadow_distance", 1.0)
        p.fillPath(path.translated(size * 0.035 * d, size * 0.05 * d), QBrush(qcolor(style.shadow_color)))
    if style.outline:
        pen = QPen(qcolor(style.outline_color), max(size * 0.08 * getattr(style, "outline_width", 1.0), 1.0))
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


def draw_text_row(p: QPainter, parts, x: float, baseline: float, avail: float, style: Style,
                  max_cap: float | None = None, align: str = "left") -> float:
    """Mehrere Texte nebeneinander auf einer Grundlinie, gemeinsam verkleinert, bis sie in ``avail`` passen.

    ``parts``: [(Text, Cap-Höhe, Rolle WEIGHT_*, Farbe, Abstand davor)]. Beschriftung und Einheit werden
    mit ``label_scale``/``unit_scale`` vergrößert, aber nie höher als ``max_cap`` (die Zeilenhöhe).
    Gibt die tatsächliche Breite zurück.
    """
    items = [pt for pt in parts if pt[0]]
    if not items:
        return 0.0

    def caps(k: float) -> list[float]:
        out = []
        for _t, cap, role, _c, _g in items:
            factor = style.unit_scale if role == WEIGHT_UNIT else style.label_scale if role == WEIGHT_LABEL else 1.0
            c = cap * factor
            if max_cap is not None:
                c = min(c, max_cap)
            out.append(c * k)
        return out

    k = 1.0
    while True:
        cs = caps(k)
        fonts = [font_for_cap(style, c, role, scaled=False) for c, (_t, _cap, role, _col, _g) in zip(cs, items)]
        widths = [text_width(f, t) for f, (t, *_r) in zip(fonts, items)]
        gaps = [g * k if n else 0.0 for n, (*_r, g) in enumerate(items)]
        total = sum(widths) + sum(gaps)
        if total <= avail or k < 0.2:
            break
        k *= 0.92
    cx = x + (avail - total if align == "right" else (avail - total) / 2 if align == "center" else 0.0)
    for f, wdt, g, (t, _cap, _role, col, _g) in zip(fonts, widths, gaps, items):
        cx += g
        draw_text(p, t, f, cx, baseline, col, style)
        cx += wdt
    return total
