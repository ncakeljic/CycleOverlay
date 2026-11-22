"""Rendert alle Widgets eines Layouts für ein Frame (PRD 2.3).

Vorschau (Phase 3) und Export nutzen genau diesen Code.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter

from .layout import Layout
from .qt_support import begin_painter, ensure_app, image_to_rgba_bytes, new_frame
from .values import FrameValues
from .widgets import create_widget


class CanvasRenderer:
    def __init__(self, layout: Layout, values: FrameValues, width: int, height: int, hide_unavailable: bool = True):
        ensure_app()
        if width <= 0 or height <= 0:
            raise ValueError("Ungültige Auflösung")
        self.layout = layout
        self.values = values
        values.language = layout.language
        self.width = int(width)
        self.height = int(height)
        self.items = [
            (cfg, create_widget(cfg, values))
            for cfg in layout.widgets
            if cfg.visible and (values.is_available(cfg.channel) or not hide_unavailable)
        ]
        #: sichtbare Widgets, deren Kanal in der Datei fehlt
        self.skipped = [cfg.id for cfg in layout.widgets if cfg.visible and not any(c is cfg for c, _ in self.items)]

    def set_values(self, values: FrameValues) -> None:
        """Neue Werte (z. B. für ein anderes Vorschau-Frame) ohne Neuaufbau der Widgets."""
        self.values = values
        values.language = self.layout.language
        for _, widget in self.items:
            widget.values = values

    @property
    def n_frames(self) -> int:
        return self.values.n_frames

    def paint(self, p: QPainter, i: int) -> None:
        self.values.language = self.layout.language  # Sprache kann sich in der Vorschau ändern
        for cfg, widget in self.items:
            rect = QRectF(*cfg.rect(self.width, self.height))
            p.save()
            widget.paint(p, rect, i)
            p.restore()

    def render(self, i: int) -> QImage:
        """Frame i als QImage (vormultipliziert, transparenter Hintergrund)."""
        img = new_frame(self.width, self.height)
        p = begin_painter(img)
        try:
            self.paint(p, i)
        finally:
            p.end()
        return img

    def frame_bytes(self, i: int) -> bytes:
        """Frame i als rohe RGBA-Bytes mit nicht vormultipliziertem Alpha (für ffmpeg)."""
        return image_to_rgba_bytes(self.render(i))

    def render_preview(self, i: int, background: QImage | None = None, checker: bool = True) -> QImage:
        """Vorschau-Bild: Overlay über Hintergrundbild oder Schachbrettmuster (wird nie exportiert)."""
        out = QImage(self.width, self.height, QImage.Format.Format_RGB32)
        p = QPainter(out)
        if background is not None:
            p.drawImage(QRectF(0, 0, self.width, self.height), background)
        elif checker:
            tile = max(8, self.height // 54)
            light, dark = QColor(204, 204, 204), QColor(153, 153, 153)
            for ty in range(0, self.height, tile):
                for tx in range(0, self.width, tile):
                    p.fillRect(tx, ty, tile, tile, light if ((tx // tile + ty // tile) % 2 == 0) else dark)
        else:
            out.fill(QColor(0, 0, 0))
        p.drawImage(0, 0, self.render(i))
        p.end()
        return out
