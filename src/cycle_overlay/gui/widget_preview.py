"""Vorschaubild einer Widget-Vorlage für den Dialog „Widget hinzufügen“."""

from __future__ import annotations

import logging

import numpy as np
from PySide6.QtGui import QColor, QImage

from ..rendering.canvas_renderer import CanvasRenderer
from ..rendering.layout import Layout

log = logging.getLogger(__name__)

PREVIEW_SIZE = (440, 248)
#: Hintergrund ohne Video: dunkles Grau, damit weiße Anzeigen sichtbar sind
PREVIEW_BG = QColor(58, 64, 74)


def numpy_to_qimage(rgb: np.ndarray) -> QImage:
    h, w = rgb.shape[:2]
    data = np.ascontiguousarray(rgb[..., :3], dtype=np.uint8)
    return QImage(data.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


def template_preview(model, key: str, channel: str | None, track_t: float, background: np.ndarray | None = None,
                     size: tuple[int, int] = PREVIEW_SIZE) -> QImage:
    """Die Vorlage mit echten Werten der Fahrt zur Zeit ``track_t`` – mittig und groß genug zum Erkennen."""
    width, height = size
    cfg = model.widget_from_template(key, channel)
    cfg.anchor, cfg.x, cfg.y = "center", 0.0, 0.0
    aspect = cfg.effective_aspect()
    cfg.size = float(min(0.72, 0.86 * width / (height * aspect)))
    values = model.frame_values(np.array([track_t]), t0=track_t)
    layout = Layout("Vorschau", [cfg], language=model.project.layout.language)
    renderer = CanvasRenderer(layout, values, width, height, hide_unavailable=False)
    if background is not None:
        bg = numpy_to_qimage(background)
    else:
        bg = QImage(width, height, QImage.Format.Format_RGB32)
        bg.fill(PREVIEW_BG)
    return renderer.render_preview(0, bg, checker=False)


def preview_time(model, position_track_t: float) -> float:
    """Eine Stelle, an der man fährt: die aktuelle, sonst die nächste Stelle in Fahrt (nicht in einer
    Pause und nicht langsamer als die Hälfte des üblichen Tempos) – sonst zeigt die Vorschau „0.0“."""
    tel = model.telemetry
    if tel is None:
        return 0.0
    t = position_track_t if 5.0 < position_track_t < tel.duration else tel.duration * 0.4
    speed = tel.channels.get("speed")
    if speed is None or not tel.available.get("speed", True):
        return t
    ok = np.isfinite(speed)
    if getattr(tel, "pause_mask", None) is not None and len(tel.pause_mask) == len(speed):
        ok &= ~tel.pause_mask
    if not ok.any():
        return t
    typical = float(np.median(speed[ok]))
    ok &= speed >= typical * 0.5
    if not ok.any():
        return t
    idx = np.flatnonzero(ok)
    k = idx[np.argmin(np.abs(tel.t[idx] - t))]
    return float(tel.t[k])
