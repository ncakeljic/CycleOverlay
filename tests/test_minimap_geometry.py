from __future__ import annotations

import math

import numpy as np
import pytest

from cycle_overlay.rendering.minimap_geometry import (
    Camera,
    auto_zoom_radius,
    compute_heading,
    heading_at,
    nice_scale,
    project_local,
)


def straight(angle_deg: float, n: int = 200, speed: float = 5.0):
    a = math.radians(angle_deg)
    d = np.arange(n) * speed
    return d * math.sin(a), d * math.cos(a), d


@pytest.mark.parametrize("angle", [0, 45, 90, 180, 270, 359])
def test_heading_straight_lines(angle):
    x, y, d = straight(angle)
    h = compute_heading(x, y, d)
    assert np.allclose(np.mod(h, 360), angle % 360, atol=0.5) or np.allclose(np.mod(h + 180, 360) - 180, angle - 360, atol=0.5)


def test_heading_wraps_without_jump():
    # Rechtskurve über Norden: 340° -> 20°
    ang = np.radians(np.linspace(340, 380, 120))
    step = 5.0
    x = np.cumsum(np.sin(ang) * step)
    y = np.cumsum(np.cos(ang) * step)
    d = np.arange(120) * step
    h = compute_heading(x, y, d)
    assert np.max(np.abs(np.diff(h))) < 3.0  # kein Sprung um 360°
    assert np.mod(h[10], 360) == pytest.approx(343, abs=3)
    assert np.mod(h[-10], 360) == pytest.approx(17, abs=3)
    t = np.arange(120.0)
    mid = heading_at(59.5, t, h)
    assert 0 <= mid < 360


def test_heading_holds_at_standstill():
    x, y, d = straight(90, 100)
    # 30 Punkte Stillstand mit GPS-Drift, Distanz bleibt gleich
    x = np.concatenate([x, x[-1] + np.random.default_rng(1).normal(0, 1.5, 30)])
    y = np.concatenate([y, y[-1] + np.random.default_rng(2).normal(0, 1.5, 30)])
    d = np.concatenate([d, np.full(30, d[-1])])
    h = compute_heading(x, y, d)
    assert np.allclose(np.mod(h[100:], 360), 90, atol=1.0)


def test_u_turn_is_smooth():
    x1, y1, d1 = straight(0, 60)
    x2 = np.full(60, x1[-1] + 4.0)
    y2 = y1[-1] - np.arange(60) * 5.0
    x, y = np.concatenate([x1, x2]), np.concatenate([y1, y2])
    d = np.concatenate([[0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])
    h = compute_heading(x, y, d)
    assert np.mod(h[10], 360) == pytest.approx(0, abs=1) or np.mod(h[10], 360) == pytest.approx(360, abs=1)
    assert np.mod(h[-10], 360) == pytest.approx(180, abs=1)
    assert np.max(np.abs(np.diff(h))) < 70


def cam(**kw):
    base = dict(center_x=100.0, center_y=200.0, heading_deg=0.0, rotate=True, radius_m=400.0,
                rect=(0.0, 0.0, 300.0, 300.0), tilt_deg=0.0, anchor=0.5)
    base.update(kw)
    return Camera(**base)


@pytest.mark.parametrize("tilt", [0, 30, 55])
@pytest.mark.parametrize("heading", [0, 77, 200])
def test_position_lands_on_arrow(tilt, heading):
    c = cam(tilt_deg=tilt, heading_deg=heading, anchor=0.68 if tilt else 0.5)
    sx, sy, w = c.project(100.0, 200.0)
    assert (float(sx), float(sy)) == pytest.approx(c.anchor_xy, abs=1e-6)
    assert w > 0


@pytest.mark.parametrize("heading", [0, 90, 135, 270])
def test_heading_up_puts_road_ahead_above_arrow(heading):
    a = math.radians(heading)
    c = cam(heading_deg=heading, rotate=True)
    ahead = (100 + 100 * math.sin(a), 200 + 100 * math.cos(a))
    sx, sy, _ = c.project(*ahead)
    ax, ay = c.anchor_xy
    assert float(sx) == pytest.approx(ax, abs=1e-6)
    assert float(sy) == pytest.approx(ay - 100 * (150 / 400), abs=1e-6)  # 150 px bis oben = 400 m


def test_north_up_ignores_heading():
    a = cam(heading_deg=0, rotate=False).project(150.0, 260.0)
    b = cam(heading_deg=123, rotate=False).project(150.0, 260.0)
    assert np.allclose(a, b)
    sx, sy, _ = cam(rotate=False).project(100.0, 300.0)  # 100 m nördlich
    assert float(sy) < 150 and float(sx) == pytest.approx(150)


def test_tilt_compresses_distance_ahead():
    flat, tilt = cam(), cam(tilt_deg=55, anchor=0.5)
    ys_flat = [float(flat.project(100.0, 200.0 + v)[1]) for v in (100, 200, 300)]
    ys_tilt = [float(tilt.project(100.0, 200.0 + v)[1]) for v in (100, 200, 300)]
    assert np.allclose(np.diff(ys_flat), np.diff(ys_flat)[0])  # Draufsicht: gleichmäßig
    gaps = -np.diff(ys_tilt)
    assert gaps[0] > gaps[1] > 0  # gekippt: weiter entfernte Abschnitte werden kürzer
    # Breite: Querabstand schrumpft mit der Entfernung
    near = abs(float(tilt.project(150.0, 250.0)[0]) - float(tilt.project(50.0, 250.0)[0]))
    far = abs(float(tilt.project(150.0, 450.0)[0]) - float(tilt.project(50.0, 450.0)[0]))
    assert far < near


def test_unproject_roundtrip():
    c = cam(tilt_deg=45, heading_deg=33, anchor=0.7)
    for wx, wy in ((120.0, 260.0), (40.0, 180.0), (300.0, 500.0)):
        sx, sy, _ = c.project(wx, wy)
        assert c.unproject(float(sx), float(sy)) == pytest.approx((wx, wy), abs=1e-6)


def test_visible_bbox_contains_view():
    c = cam(tilt_deg=55, heading_deg=45, anchor=0.68)
    x0, y0, x1, y1 = c.visible_bbox()
    assert x0 < 100 < x1 and y0 < 200 < y1
    assert (x1 - x0) < 400 * 3 * 2 + 400  # durch max_ahead begrenzt


def test_qt_values_match_matrix():
    c = cam(tilt_deg=40, heading_deg=10)
    m11, m12, m13, m21, m22, m23, m31, m32, m33 = c.qt_values()
    x, y = 130.0, 260.0
    X = m11 * x + m21 * y + m31
    Y = m12 * x + m22 * y + m32
    W = m13 * x + m23 * y + m33
    sx, sy, _ = c.project(x, y)
    assert (X / W, Y / W) == pytest.approx((float(sx), float(sy)))


def test_north_vector():
    nx, ny = cam(rotate=False).north_vector()
    assert (nx, ny) == pytest.approx((0, -1), abs=1e-6)
    nx, ny = cam(rotate=True, heading_deg=90).north_vector()  # nach Osten fahren: Norden links
    assert (nx, ny) == pytest.approx((-1, 0), abs=1e-6)


def test_auto_zoom_and_scale():
    r = auto_zoom_radius(np.array([0.0] * 20 + [20.0] * 40), 300)  # 0 und 72 km/h
    assert r[0] == pytest.approx(240) and r[-1] == pytest.approx(300 * 2.0, rel=0.01)
    assert nice_scale(730) == 500 and nice_scale(180) == 100 and nice_scale(0.3) == pytest.approx(0.2)


def test_project_local():
    x, y = project_local([48.001], [11.0], 48.0, 11.0)
    assert float(y[0]) == pytest.approx(111.19, abs=0.1) and float(x[0]) == pytest.approx(0)


@pytest.mark.parametrize("tilt", [0, 35, 60])
def test_project3d_matches_flat_projection_at_ground(tilt):
    c = cam(tilt_deg=tilt, heading_deg=40, anchor=0.66 if tilt else 0.5)
    xs, ys = np.array([100.0, 160.0, 30.0]), np.array([200.0, 260.0, 300.0])
    a = c.project(xs, ys)
    b = c.project3d(xs, ys, np.zeros(3))
    assert np.allclose(a[0], b[0]) and np.allclose(a[1], b[1])


def test_project3d_height_moves_up_on_screen():
    c = cam(tilt_deg=55, anchor=0.66)
    _, y0, d0 = c.project3d(100.0, 300.0, 0.0)
    _, y1, d1 = c.project3d(100.0, 300.0, 50.0)
    _, y2, _ = c.project3d(100.0, 300.0, -30.0)
    assert float(y1) < float(y0) < float(y2)  # höher = weiter oben, tiefer = weiter unten
    assert float(d0) > 0 and float(d1) > 0
    # Punkt direkt an der Position bleibt bei z = 0 am Pfeil
    sx, sy, _ = c.project3d(100.0, 200.0, 0.0)
    assert (float(sx), float(sy)) == pytest.approx(c.anchor_xy)


def test_route_mask_reaches_beyond_edge_and_keeps_hairpin():
    from cycle_overlay.rendering.minimap_geometry import route_mask

    c = cam()
    # Kehre: 600 m hinaus (außerhalb der Sichtweite 400 m) und zurück
    out = np.arange(0, 601, 20.0)
    x = np.concatenate([np.full(out.size, 100.0), np.full(out.size, 140.0)])
    y = np.concatenate([200 + out, 200 + out[::-1]])
    tight = route_mask(c, x, y, factor=1.0)
    wide = route_mask(c, x, y, factor=2.0)
    assert not tight.all()  # Spitze der Kehre fehlt bei 1×
    assert wide.all()  # bei 2× wird die ganze Kehre gezeichnet
    # je ein Nachbarpunkt außerhalb, damit die Linie bis an den Rand reicht
    first_out = int(np.argmin(np.r_[True, (y[1:out.size] - 200) <= 400 * 1.15 + 1]))
    assert tight[: out.size].sum() >= first_out


def test_route_fades_towards_range_end():
    from cycle_overlay.rendering.minimap_geometry import (
        ROUTE_FADE_LEVELS,
        route_fade_levels,
        segment_levels,
        segment_runs,
    )

    c = cam()  # Sichtweite 400 m, Mitte (100, 200)
    d = np.arange(0, 1001, 10.0)
    lv = route_fade_levels(c, np.full(d.size, 100.0), 200 + d, factor=2.0)  # Reichweite 800 m
    assert (lv[d <= 560] == ROUTE_FADE_LEVELS).all()  # bis 70 % voll
    assert (lv[d >= 800] == 0).all()
    fading = lv[(d > 560) & (d < 800)]
    assert (np.diff(fading) <= 0).all() and fading.min() >= 1  # stetig blasser
    seg = segment_levels(lv)
    last_vis = int(np.flatnonzero(lv > 0)[-1])
    assert seg[last_vis] > 0  # blasser Ausläufer bis in den ersten unsichtbaren Punkt
    runs = segment_runs(seg, seg > 0)
    assert runs[0][0] == 0 and runs[-1][1] == last_vis
    assert all(seg[a] == seg[b] for a, b in runs)


def test_segment_runs_breaks_on_key_and_visibility():
    from cycle_overlay.rendering.minimap_geometry import segment_runs

    keys = np.array([1, 1, 2, 2, 2, 1, 1])
    vis = np.array([1, 1, 1, 0, 0, 1, 1], bool)
    assert segment_runs(keys, vis) == [(0, 1), (2, 2), (5, 6)]
    assert segment_runs(np.array([]), np.array([], bool)) == []
