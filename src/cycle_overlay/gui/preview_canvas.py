"""Interaktive Vorschau: Overlay über Videobild oder Schachbrett, Auswahl und Drag-and-Drop.

Gerendert wird mit demselben ``CanvasRenderer`` wie beim Export – nur in Vorschaugröße.
Da alle Maße relativ sind, entspricht das Bild maßstäblich dem Export.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..rendering.canvas_renderer import CanvasRenderer
from .controller import Controller


def numpy_to_qimage(rgb: np.ndarray) -> QImage:
    h, w, _ = rgb.shape
    buf = np.ascontiguousarray(rgb).tobytes()
    return QImage(buf, w, h, w * 3, QImage.Format.Format_RGB888).copy()


class PreviewCanvas(QWidget):
    widgetMoved = Signal()

    def __init__(self, controller: Controller, parent=None):
        super().__init__(parent)
        self.c = controller
        self.show_video = True
        self.show_safe_area = True
        self.snap = True
        self.grid = 0.005  # Raster: 0,5 % der Bildbreite/-höhe
        self._bg_image: QImage | None = None
        self._drag: tuple[str, float, float] | None = None  # (id, dx, dy) in Export-Pixeln
        self._checker: QImage | None = None
        self.setMinimumSize(480, 270)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        c = controller
        c.positionChanged.connect(lambda _t: self.update())
        c.layoutChanged.connect(lambda _s: self.update())
        c.selectionChanged.connect(lambda _w: self.update())
        c.projectChanged.connect(self.update)
        c.backgroundChanged.connect(self._on_background)

    # ------------------------------------------------------------------ Geometrie

    def frame_rect(self) -> QRectF:
        """Bereich des Exportbildes im Widget (Seitenverhältnis wie Export)."""
        fw, fh = self.c.model.frame_size()
        m = 12
        aw, ah = max(self.width() - 2 * m, 1), max(self.height() - 2 * m, 1)
        s = min(aw / fw, ah / fh)
        w, h = fw * s, fh * s
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def scale(self) -> float:
        fw, _ = self.c.model.frame_size()
        return self.frame_rect().width() / fw

    def to_frame(self, pos: QPointF) -> tuple[float, float]:
        r = self.frame_rect()
        s = self.scale()
        return (pos.x() - r.x()) / s, (pos.y() - r.y()) / s

    # ------------------------------------------------------------------ Zeichnen

    def _on_background(self) -> None:
        bg = self.c.background
        self._bg_image = numpy_to_qimage(bg) if bg is not None else None
        self.update()

    def _checker_tile(self) -> QImage:
        if self._checker is None:
            t = 10
            img = QImage(2 * t, 2 * t, QImage.Format.Format_RGB32)
            p = QPainter(img)
            p.fillRect(0, 0, 2 * t, 2 * t, QColor(200, 200, 200))
            p.fillRect(0, 0, t, t, QColor(150, 150, 150))
            p.fillRect(t, t, t, t, QColor(150, 150, 150))
            p.end()
            self._checker = img
        return self._checker

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(38, 38, 42))
        model = self.c.model
        r = self.frame_rect()
        if self.show_video and self._bg_image is not None and model.has_video:
            p.drawImage(r, self._bg_image)
        else:
            p.save()
            p.setClipRect(r)
            tile = self._checker_tile()
            y = r.top()
            while y < r.bottom():
                x = r.left()
                while x < r.right():
                    p.drawImage(QPointF(x, y), tile)
                    x += tile.width()
                y += tile.height()
            p.restore()

        if model.ready:
            tw, th = max(int(round(r.width())), 1), max(int(round(r.height())), 1)
            try:
                values = model.frame_values([self.c.track_time()])
                renderer = CanvasRenderer(model.project.layout, values, tw, th)
                p.drawImage(QPointF(r.x(), r.y()), renderer.render(0))
            except Exception as exc:  # noqa: BLE001 – Vorschau darf nie abstürzen
                p.setPen(QColor(255, 120, 120))
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, f"Vorschau-Fehler: {exc}")
        else:
            p.setPen(QColor(230, 230, 230))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter,
                       "GPX-Datei öffnen (oder hierher ziehen),\noptional ein Video dazu.")

        if self.show_safe_area:
            pen = QPen(QColor(255, 255, 255, 90), 1, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            for f in (0.9, 0.8):
                w, h = r.width() * f, r.height() * f
                p.drawRect(QRectF(r.center().x() - w / 2, r.center().y() - h / 2, w, h))

        sel = model.widget(self.c.selected) if self.c.selected else None
        if sel is not None and sel.visible:
            fw, fh = model.frame_size()
            x, y, w, h = sel.rect(fw, fh)
            s = self.scale()
            box = QRectF(r.x() + x * s, r.y() + y * s, w * s, h * s)
            p.setPen(QPen(QColor(255, 255, 255, 230), 1.5, Qt.PenStyle.DashLine))
            p.drawRect(box)
            p.setPen(QPen(QColor(30, 144, 255), 1, Qt.PenStyle.SolidLine))
            p.drawRect(box.adjusted(-2, -2, 2, 2))
        p.end()

    # ------------------------------------------------------------------ Maus & Tastatur

    def mousePressEvent(self, e: QMouseEvent) -> None:
        self.setFocus()
        if e.button() != Qt.MouseButton.LeftButton or not self.c.model.ready:
            return
        fx, fy = self.to_frame(e.position())
        fw, fh = self.c.model.frame_size()
        cfg = self.c.model.widget_at(fx, fy, fw, fh)
        self.c.select(cfg.id if cfg else "")
        if cfg is not None:
            x, y, _, _ = cfg.rect(fw, fh)
            self._drag = (cfg.id, fx - x, fy - y)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        model = self.c.model
        if self._drag is None:
            if model.ready:
                fx, fy = self.to_frame(e.position())
                fw, fh = model.frame_size()
                hit = model.widget_at(fx, fy, fw, fh)
                self.setCursor(Qt.CursorShape.OpenHandCursor if hit else Qt.CursorShape.ArrowCursor)
            return
        wid, dx, dy = self._drag
        cfg = model.widget(wid)
        if cfg is None:
            return
        fw, fh = model.frame_size()
        fx, fy = self.to_frame(e.position())
        _, _, w, h = cfg.rect(fw, fh)
        left = min(max(fx - dx, 0.0), fw - w)
        top = min(max(fy - dy, 0.0), fh - h)
        if self.snap and not (e.modifiers() & Qt.KeyboardModifier.AltModifier):
            gx, gy = fw * self.grid, fh * self.grid
            left, top = round(left / gx) * gx, round(top / gy) * gy
        cfg.move_to(left, top, fw, fh)
        self.c.layout_edited(self)
        self.update()

    def mouseReleaseEvent(self, _e: QMouseEvent) -> None:
        if self._drag is not None:
            self._drag = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.widgetMoved.emit()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        key = e.key()
        cfg = self.c.model.widget(self.c.selected) if self.c.selected else None
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and cfg is not None:
            self.c.remove_selected()
            return
        if key == Qt.Key.Key_Space:
            self.c.toggle_play()
            return
        arrows = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if key in arrows:
            dx, dy = arrows[key]
            if cfg is None and self.c.sync_mode and self.c.model.has_video:
                # Sync-Tab: Video verschieben – ←/→ um 1 Frame, ↑/↓ um 1 s (Shift: ×10)
                big = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
                if dx:
                    self.c.nudge_offset(frames=dx * big)
                else:
                    self.c.nudge_offset(seconds=-dy * big)
                return
            if cfg is None:  # ohne Auswahl: Frame vor/zurück
                if dx:
                    self.c.step_frames(dx * (10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1))
                return
            fw, fh = self.c.model.frame_size()
            step = 0.02 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 0.002
            x, y, w, h = cfg.rect(fw, fh)
            cfg.move_to(min(max(x + dx * step * fw, 0), fw - w), min(max(y + dy * step * fh, 0), fh - h), fw, fh)
            self.c.layout_edited(self)
            return
        super().keyPressEvent(e)
