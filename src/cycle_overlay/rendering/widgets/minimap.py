"""Navigations-Minimap (Typ ``minimap``) – wie bei GTA oder im Navi.

Die Karte bewegt sich unter einem Pfeil. Ausrichtung (Norden oben / Fahrtrichtung oben),
Ansicht (von oben / 3D-gekippt), Hintergrund (OSM-Straßen / nur Route), Thema
(dunkel / hell), Form (rund / abgerundet) und Pfeil sind wählbar.

Die Weltkoordinaten (Meter) werden über eine perspektivische ``QTransform`` direkt
gezeichnet; Strichbreiten in Metern werden dadurch in der Ferne automatisch dünner.
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF, QTransform

from ..grade import grade_class, grade_color, lowest_reference, smooth_grade
from ..minimap_geometry import ROUTE_FADE_LEVELS, nice_scale, route_fade_levels, segment_levels, segment_runs
from ..minimap_theme import THEMES
from ..qt_support import WEIGHT_LABEL, WEIGHT_VALUE, draw_plain_text, font_for_cap, qcolor, text_width
from .base import OverlayWidget

#: Breite einer Nebenstraße = Sichtweite / ROAD_DIV (bleibt so bei jedem Zoom gleich breit)
ROAD_DIV = 60.0


def _pen(color: str, width: float) -> QPen:
    pen = QPen(qcolor(color), max(width, 0.01))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _feature_path(f) -> QPainterPath:
    path = QPainterPath()
    pts = [QPointF(float(a), float(b)) for a, b in zip(f.x, f.y)]
    if f.is_area:
        path.addPolygon(QPolygonF(pts))
        path.closeSubpath()
    else:
        path.moveTo(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)
    return path



class MinimapWidget(OverlayWidget):
    def shape(self, rect: QRectF) -> tuple[QPainterPath, QRectF]:
        mm = self.cfg.minimap
        path = QPainterPath()
        if mm.shape == "circle":
            side = min(rect.width(), rect.height())
            r = QRectF(rect.center().x() - side / 2, rect.center().y() - side / 2, side, side)
            path.addEllipse(r)
        else:
            r = QRectF(rect.x(), rect.y(), rect.width(), rect.height())
            rad = min(r.width(), r.height()) * 0.12
            path.addRoundedRect(r, rad, rad)
        return path, r

    # ------------------------------------------------------------------ Zeichnen

    def paint(self, p: QPainter, rect: QRectF, i: int) -> None:
        cfg, v, mm = self.cfg, self.values, self.cfg.minimap
        theme = THEMES[mm.theme]
        shape, sr = self.shape(rect)
        cam = v.minimap_camera(cfg, i, (sr.x(), sr.y(), sr.width(), sr.height()))
        unit = cam.radius_m / ROAD_DIV
        size = min(sr.width(), sr.height())

        p.save()
        p.setClipPath(shape)
        p.fillPath(shape, QBrush(qcolor(theme.background)))

        # --- Welt (Meter) über die Kamera-Transformation
        p.save()
        p.setTransform(QTransform(*cam.qt_values()), True)
        p.setBrush(Qt.BrushStyle.NoBrush)
        drew_osm = False
        idx = v.map_index() if mm.background == "osm" else None
        if idx is not None:
            drew_osm = self._draw_map(p, idx, cam, theme, unit)
        ribbon = (mm.show_route and mm.elevation_ribbon and cam.tilt_deg > 0
                  and bool(v.tel.available.get("elevation")))
        if mm.show_route:
            self._draw_route(p, cam, i, theme, unit, ground_only=ribbon)
        p.restore()
        if ribbon:
            self._draw_ribbon(p, cam, i, theme, unit)

        if cam.tilt_deg > 0:  # Horizont weich ausblenden
            g = QLinearGradient(QPointF(0, sr.top()), QPointF(0, sr.top() + sr.height() * 0.3))
            bg = qcolor(theme.background)
            g.setColorAt(0.0, bg)
            transparent = QColor(bg)
            transparent.setAlpha(0)
            g.setColorAt(1.0, transparent)
            p.fillRect(QRectF(sr.left(), sr.top(), sr.width(), sr.height() * 0.3), QBrush(g))

        self._draw_arrow(p, cam, i, theme, size)
        if mm.grade_badge and v.tel.available.get("gradient"):
            self._draw_grade_badge(p, cam, i, theme, size)
        # Kompass wandert am Rand mit der Nordrichtung: Maßstab und Quellenangabe weichen ihm aus
        compass = self._compass_pos(cam, sr, size) if mm.show_compass else None
        bottom_blocked = compass is not None and compass[1] > sr.center().y() + sr.height() * 0.2 \
            and abs(compass[0] - sr.center().x()) < sr.width() * 0.35
        if mm.show_scale and cam.tilt_deg == 0:
            self._draw_scale(p, cam, sr, theme, size, at_top=bottom_blocked)
        if drew_osm and mm.show_attribution:
            self._draw_attribution(p, idx.data.attribution if idx is not None else "", sr, theme, size,
                                   at_top=bottom_blocked)
        p.restore()

        if compass is not None:
            self._draw_compass(p, compass, theme, size)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_pen(theme.border, max(size * 0.012, 1.0)))
        p.drawPath(shape)

    def _draw_map(self, p: QPainter, idx, cam, theme, unit: float) -> bool:
        ids = idx.query(cam.visible_bbox())
        if not ids:
            return False
        paths = idx.cache.setdefault("qt_paths", {})
        feats = []
        for k in ids:
            if k not in paths:
                paths[k] = _feature_path(idx.features[k])
            feats.append((idx.features[k], paths[k]))
        # Flächen
        for f, path in feats:
            if f.is_area:
                p.fillPath(path, QBrush(qcolor(theme.water if f.cls == "water" else theme.green)))
        # Ränder (helles Thema), dann Straßen
        for f, path in feats:
            rs = theme.roads.get(f.cls)
            if rs is not None and rs.casing and not f.is_area:
                p.strokePath(path, _pen(rs.casing, rs.width * unit * 1.35))
        for f, path in feats:
            rs = theme.roads.get(f.cls)
            if rs is not None and not f.is_area:
                p.strokePath(path, _pen(rs.color, rs.width * unit))
        return True

    def _route_classes(self) -> np.ndarray | None:
        if self.cfg.minimap.route_coloring != "gradient" or not self.values.tel.available.get("elevation"):
            return None
        return grade_class(smooth_grade(self.values.tel), self.cfg.grade_steps)

    def _draw_route(self, p: QPainter, cam, i: int, theme, unit: float, ground_only: bool = False) -> None:
        """Route zeichnen: gefahrener Teil dezent, kommender Teil mit Kontur; zum Ende der
        Routen-Sichtweite hin weich ausgeblendet."""
        v, mm = self.values, self.cfg.minimap
        x, y = v.world_route()
        n = x.size
        if n < 2:
            return
        lv = route_fade_levels(cam, x, y, mm.route_range)
        if not lv.any():
            return
        L = ROUTE_FADE_LEVELS
        now = v.track_time(i)
        k = int(np.clip(np.searchsorted(v.tel.t, now, side="right"), 1, n))
        cx, cy = cam.center_x, cam.center_y
        cls = None if ground_only else self._route_classes()
        ahead_color = mm.route_color or theme.route_ahead
        outline = theme.arrow_outline if mm.theme == "dark" else "#FFFFFFFF"

        # zwei Linienzüge, jeweils bis zur bzw. ab der eigenen Position
        rx, ry = np.r_[x[:k], cx], np.r_[y[:k], cy]
        r_lv = segment_levels(np.r_[lv[:k], L])
        ax_, ay_ = np.r_[cx, x[k:]], np.r_[cy, y[k:]]
        a_lv = segment_levels(np.r_[L, lv[k:]])
        if cls is not None:
            cls = np.asarray(cls, dtype=int)
            r_cls = cls[:k]  # Strecke j: Punkt j → j+1 (die letzte endet an der Position)
            a_cls = cls[k - 1: n - 1]  # erste Strecke: Position → Punkt k (liegt in Abschnitt k-1)
        else:
            r_cls = np.zeros(r_lv.size, int)
            a_cls = np.zeros(a_lv.size, int)

        def stroke(px, py, levels, classes, color_of, width, alpha=255) -> None:
            if levels.size == 0:
                return
            keys = levels * 1000 + classes[: levels.size]
            for s0, s1 in segment_runs(keys, levels > 0):
                pts = [QPointF(float(px[j]), float(py[j])) for j in range(s0, s1 + 2)
                       if math.isfinite(px[j]) and math.isfinite(py[j])]
                if len(pts) < 2:
                    continue
                path = QPainterPath()
                path.addPolygon(QPolygonF(pts))
                col = qcolor(color_of(int(classes[s0])))
                col.setAlpha(int(round(col.alpha() * alpha / 255 * levels[s0] / L)))
                pen = _pen("#FFFFFFFF", width)
                pen.setColor(col)
                p.strokePath(path, pen)

        if ground_only:  # unter dem 3D-Höhenband nur eine dezente Spur am Boden
            px, py = np.r_[rx, x[k:]], np.r_[ry, y[k:]]
            levels = np.r_[r_lv, a_lv]
            stroke(px, py, levels, np.zeros(levels.size, int), lambda _c: theme.route_ridden, unit * 1.2)
            return
        if cls is None:
            stroke(rx, ry, r_lv, r_cls, lambda _c: theme.route_ridden, unit * 1.6)
            stroke(ax_, ay_, a_lv, a_cls, lambda _c: outline, unit * 3.0)
            stroke(ax_, ay_, a_lv, a_cls, lambda _c: ahead_color, unit * 2.2)
            return
        colors = self.cfg.grade_colors
        stroke(rx, ry, r_lv, r_cls, lambda c: colors[c], unit * 1.6, alpha=110)
        stroke(ax_, ay_, a_lv, np.zeros(a_lv.size, int), lambda _c: outline, unit * 3.0)
        stroke(ax_, ay_, a_lv, a_cls, lambda c: colors[c], unit * 2.2)

    def _draw_ribbon(self, p: QPainter, cam, i: int, theme, unit: float) -> None:
        """Route als 3D-Höhenband: über der Kartenebene entsprechend ihrer Höhe (überhöht)."""
        v, mm = self.values, self.cfg.minimap
        e_cur = v.raw("elevation", i)
        if not math.isfinite(e_cur):
            return
        x, y = v.world_route()
        ele = v.tel.channels["elevation"]
        lv = route_fade_levels(cam, x, y, mm.route_range)
        vis = lv > 0
        vis[1:] |= lv[:-1] > 0
        vis[:-1] |= lv[1:] > 0
        inside = vis & np.isfinite(x) & np.isfinite(y) & np.isfinite(ele)
        if inside.sum() < 2:
            return
        idx = np.flatnonzero(inside)
        now = v.track_time(i)
        k = int(np.searchsorted(v.tel.t, now, side="right"))
        if mm.ribbon_reference == "lowest":
            ref = lowest_reference(v.tel, mm.radius_m * 2, mm.radius_m * 4)
            base = float(np.interp(now, v.tel.t, ref))
        else:
            base = e_cur
        z = (ele[idx] - base) * mm.ribbon_exaggeration
        z_cur = (e_cur - base) * mm.ribbon_exaggeration
        tx, ty, td = cam.project3d(x[idx], y[idx], z)
        gx, gy, gd = cam.project3d(x[idx], y[idx], np.zeros(idx.size))
        near = cam.camera_distance * 0.08
        F = cam._scale() * cam.camera_distance
        cls = self._route_classes()
        ahead_color = mm.route_color or theme.route_ahead
        segs = []
        for n in range(idx.size - 1):
            a, b = idx[n], idx[n + 1]
            if b != a + 1 or min(td[n], td[n + 1], gd[n], gd[n + 1]) < near:
                continue
            segs.append(((gd[n] + gd[n + 1]) / 2, n, a))
        segs.sort(key=lambda s: -s[0])  # von hinten nach vorne zeichnen
        for depth, n, a in segs:
            ridden = a < k
            fade = max(lv[a], lv[min(a + 1, lv.size - 1)]) / ROUTE_FADE_LEVELS
            if fade <= 0:
                continue
            color = self.cfg.grade_colors[int(cls[a])] if cls is not None else (
                theme.route_ridden if ridden else ahead_color)
            if mm.ribbon_curtain:
                fill = qcolor(color)
                fill.setAlpha(int((45 if ridden else 85) * fade))
                poly = QPolygonF([QPointF(gx[n], gy[n]), QPointF(gx[n + 1], gy[n + 1]),
                                  QPointF(tx[n + 1], ty[n + 1]), QPointF(tx[n], ty[n])])
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(fill))
                p.drawPolygon(poly)
            width = max(unit * (1.6 if ridden else 2.2) * F / depth, 1.0)
            line = qcolor(color)
            line.setAlpha(int((140 if ridden else line.alpha()) * fade))
            pen = _pen(color, width)
            pen.setColor(line)
            p.setPen(pen)
            p.drawLine(QPointF(tx[n], ty[n]), QPointF(tx[n + 1], ty[n + 1]))
        if z_cur > 0.5:  # Bezug „tiefster Punkt“: eigene Position auf dem Band markieren
            px, py, pd = cam.project3d(cam.center_x, cam.center_y, z_cur)
            if float(pd) > near:
                ax, ay = cam.anchor_xy
                stem = qcolor(theme.text)
                stem.setAlpha(150)
                pen = _pen(theme.text, max(unit * 0.6 * F / float(pd), 1.0))
                pen.setColor(stem)
                p.setPen(pen)
                p.drawLine(QPointF(ax, ay), QPointF(float(px), float(py)))
                r = max(unit * 2.0 * F / float(pd), 2.5)
                p.setPen(_pen(theme.arrow_outline, max(r * 0.3, 1.0)))
                p.setBrush(QBrush(qcolor(mm.arrow_color or theme.arrow)))
                p.drawEllipse(QPointF(float(px), float(py)), r, r)

    def _draw_grade_badge(self, p: QPainter, cam, i: int, theme, size: float) -> None:
        g = self.values.raw("gradient", i, 2.0)
        if not math.isfinite(g):
            return
        color = grade_color(g, self.cfg.grade_steps, self.cfg.grade_colors)
        ax, ay = cam.anchor_xy
        s = self.cfg.minimap.arrow_size * size
        cap = s * 0.32
        f = font_for_cap(self.cfg.style, cap, WEIGHT_VALUE)
        txt = f"{abs(g):.0f} %"
        tri = cap * 1.1
        w = text_width(f, txt) + tri + cap * 1.4
        h = cap * 2.2
        box = QRectF(ax + s * 0.6, ay - h / 2, w, h)
        p.setPen(_pen(color, max(cap * 0.18, 1.0)))
        p.setBrush(QBrush(qcolor(theme.background)))
        p.drawRoundedRect(box, h / 2, h / 2)
        # Dreieck: hoch = Anstieg, runter = Abfahrt, flach = Strich
        tx, ty = box.left() + cap * 0.6, box.center().y()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(qcolor(color)))
        if g >= 1:
            p.drawPolygon(QPolygonF([QPointF(tx, ty + tri / 2), QPointF(tx + tri, ty + tri / 2), QPointF(tx + tri / 2, ty - tri / 2)]))
        elif g <= -1:
            p.drawPolygon(QPolygonF([QPointF(tx, ty - tri / 2), QPointF(tx + tri, ty - tri / 2), QPointF(tx + tri / 2, ty + tri / 2)]))
        else:
            p.drawRect(QRectF(tx, ty - tri * 0.12, tri, tri * 0.24))
        draw_plain_text(p, txt, f, tx + tri + cap * 0.35, ty + cap / 2, qcolor(theme.text))

    def _draw_arrow(self, p: QPainter, cam, i: int, theme, size: float) -> None:
        mm = self.cfg.minimap
        ax, ay = cam.anchor_xy
        # Pfeilrichtung auf dem Bildschirm = projizierte Fahrtrichtung
        h = math.radians(cam.heading_deg)
        sx, sy, _ = cam.project(cam.center_x + math.sin(h) * cam.radius_m * 0.2,
                                cam.center_y + math.cos(h) * cam.radius_m * 0.2)
        angle = math.degrees(math.atan2(float(sx) - ax, -(float(sy) - ay)))
        s = mm.arrow_size * size
        color = qcolor(mm.arrow_color or theme.arrow)
        p.save()
        p.translate(ax, ay)
        p.rotate(angle)
        outline = QPen(qcolor(theme.arrow_outline), max(s * 0.08, 1.0))
        outline.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        if mm.arrow == "dot":
            p.setPen(outline)
            p.setBrush(QBrush(color))
            wedge = QPolygonF([QPointF(0, -s * 0.62), QPointF(s * 0.2, -s * 0.3), QPointF(-s * 0.2, -s * 0.3)])
            p.drawPolygon(wedge)
            p.drawEllipse(QPointF(0, 0), s * 0.3, s * 0.3)
        else:
            if mm.arrow == "chevron":
                pts = [(0, -0.55), (0.42, 0.45), (0, 0.22), (-0.42, 0.45)]
            else:
                pts = [(0, -0.55), (0.4, 0.45), (-0.4, 0.45)]
            poly = QPolygonF([QPointF(a * s, b * s) for a, b in pts])
            if self.cfg.style.shadow:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(qcolor(self.cfg.style.shadow_color)))
                p.drawPolygon(poly.translated(s * 0.04, s * 0.07))
            p.setPen(outline)
            p.setBrush(QBrush(color))
            p.drawPolygon(poly)
        p.restore()

    def _compass_pos(self, cam, sr: QRectF, size: float) -> tuple[float, float, float]:
        """(x, y, Radius) des Kompasses: am Rand in Nordrichtung."""
        nx, ny = cam.north_vector()
        r = size * 0.075
        inset = r * 1.15  # ganz innerhalb der Karte, damit „N“ am Bildrand nicht abgeschnitten wird
        if self.cfg.minimap.shape == "circle":
            dist = size / 2 - inset
            return sr.center().x() + nx * dist, sr.center().y() + ny * dist, r
        cx0, cy0 = sr.center().x(), sr.center().y()
        tx = (sr.width() / 2 - inset) / abs(nx) if abs(nx) > 1e-6 else math.inf
        ty = (sr.height() / 2 - inset) / abs(ny) if abs(ny) > 1e-6 else math.inf
        d = min(tx, ty)
        return cx0 + nx * d, cy0 + ny * d, r

    def _draw_compass(self, p: QPainter, pos: tuple[float, float, float], theme, size: float) -> None:
        cx, cy, r = pos
        p.setPen(_pen(theme.border, max(size * 0.008, 1.0)))
        p.setBrush(QBrush(qcolor(theme.background)))
        p.drawEllipse(QPointF(cx, cy), r, r)
        f = font_for_cap(self.cfg.style, r * 0.9, WEIGHT_VALUE, scaled=False)
        draw_plain_text(p, "N", f, cx - text_width(f, "N") / 2, cy + r * 0.45, qcolor(theme.text))

    def _chord(self, sr: QRectF, y: float) -> float:
        """Nutzbare Breite auf Höhe y (beim Kreis die Sehne, sonst die Breite)."""
        if self.cfg.minimap.shape != "circle":
            return sr.width()
        r = min(sr.width(), sr.height()) / 2
        dy = abs(y - sr.center().y())
        return 2 * math.sqrt(max(r * r - dy * dy, 0.0))

    def _draw_attribution(self, p: QPainter, txt: str, sr: QRectF, theme, size: float, at_top: bool) -> None:
        """Quellenangabe (Pflicht bei OSM-Daten) – verkleinert, bis sie in die Form passt."""
        if not txt:
            return
        circle = self.cfg.minimap.shape == "circle"
        cap = size * 0.03
        off = size * (0.05 if circle else 0.03)
        base = sr.top() + off + cap if at_top else sr.bottom() - off
        f = font_for_cap(self.cfg.style, cap, WEIGHT_LABEL, scaled=False)
        avail = self._chord(sr, base - cap if not at_top else base) * 0.92
        w = text_width(f, txt)
        if w > avail > 0:
            cap *= avail / w
            f = font_for_cap(self.cfg.style, cap, WEIGHT_LABEL, scaled=False)
            w = text_width(f, txt)
        c = qcolor(theme.text)
        c.setAlpha(170)
        draw_plain_text(p, txt, f, sr.center().x() - w / 2, base, c)

    def _draw_scale(self, p: QPainter, cam, sr: QRectF, theme, size: float, at_top: bool = False) -> None:
        mpp = cam.meters_per_pixel()
        imperial = self.values.units == "imperial"
        target_m = sr.width() * 0.28 * mpp
        if imperial:
            ft = nice_scale(target_m / 0.3048)
            meters, label = ft * 0.3048, f"{ft:g} ft"
        else:
            meters = nice_scale(target_m)
            label = f"{meters / 1000:g} km" if meters >= 1000 else f"{meters:g} m"
        px = meters / mpp
        x0 = sr.center().x() - px / 2
        off = size * (0.14 if self.cfg.minimap.shape == "circle" else 0.07)
        y0 = sr.top() + off + size * 0.05 if at_top else sr.bottom() - off
        p.setPen(_pen(theme.text, max(size * 0.01, 1.0)))
        p.drawLine(QPointF(x0, y0), QPointF(x0 + px, y0))
        for xx in (x0, x0 + px):
            p.drawLine(QPointF(xx, y0 - size * 0.015), QPointF(xx, y0 + size * 0.015))
        f = font_for_cap(self.cfg.style, size * 0.03, WEIGHT_LABEL, scaled=False)
        draw_plain_text(p, label, f, sr.center().x() - text_width(f, label) / 2, y0 - size * 0.025,
                        qcolor(theme.text))
