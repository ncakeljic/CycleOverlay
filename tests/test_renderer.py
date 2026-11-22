"""Render-Engine mit Qt (PRD Phase 2). Wird übersprungen, wenn PySide6 fehlt."""

from __future__ import annotations

import subprocess
import threading
import time

import numpy as np
import pytest

pytest.importorskip("PySide6")

from cycle_overlay.cli import main  # noqa: E402
from cycle_overlay.core.presets import load_preset  # noqa: E402
from cycle_overlay.core.processor import frame_times  # noqa: E402
from cycle_overlay.export.ffmpeg_exporter import run_export  # noqa: E402
from cycle_overlay.export.profiles import PROFILES  # noqa: E402
from cycle_overlay.rendering.canvas_renderer import CanvasRenderer  # noqa: E402
from cycle_overlay.rendering.layout import Layout, Style, WidgetConfig  # noqa: E402
from cycle_overlay.rendering.qt_support import image_to_rgba, load_bundled_fonts  # noqa: E402
from cycle_overlay.rendering.values import FrameValues  # noqa: E402

from .conftest import SAMPLE_GPX, needs_ffmpeg  # noqa: E402

W, H = 1280, 720
PLAIN = Style(shadow=False, outline=False)


def renderer(telemetry, widgets, t=450.0, n=1, size=(W, H)):
    values = FrameValues(telemetry, frame_times(t, n, 30), tz="Europe/Berlin")
    return CanvasRenderer(Layout("t", widgets), values, *size)


def widget(**kw):
    base = dict(id="w", type="text", channel="speed", anchor="top_left", x=0.1, y=0.1, size=0.2, style=Style(**PLAIN.__dict__))
    base.update(kw)
    return WidgetConfig(**base)


def test_bundled_font_is_available(telemetry):
    renderer(telemetry, [])  # startet die App
    from PySide6.QtGui import QFontDatabase

    load_bundled_fonts()
    assert "Inter" in QFontDatabase.families()


def test_background_is_fully_transparent(telemetry):
    r = renderer(telemetry, load_preset().widgets)
    a = image_to_rgba(r.render(0))
    assert a.shape == (H, W, 4)
    assert a[H // 2, W // 2, 3] == 0  # Bildmitte frei
    assert a[0, 0, 3] == 0 and a[-1, -1, 3] == 0
    assert (a[..., 3] > 0).mean() > 0.005  # aber es wurde etwas gezeichnet


@pytest.mark.parametrize(
    "kw",
    [
        dict(type="text"),
        dict(type="text", label_position="left"),
        dict(type="text", label_position="bottom"),
        dict(type="arc", channel="power"),
        dict(type="arc", channel="power", sweep_deg=180, show_needle=True),
        dict(type="bar", channel="heart_rate"),
        dict(type="bar", channel="heart_rate", orientation="vertical"),
        dict(type="text", channel="time_of_day"),
    ],
)
def test_widget_draws_only_inside_its_rect(telemetry, kw):
    cfg = widget(**kw)
    r = renderer(telemetry, [cfg])
    a = image_to_rgba(r.render(0))[..., 3]
    x, y, w, h = (int(round(v)) for v in cfg.rect(W, H))
    inside = a[y : y + h, x : x + w]
    assert (inside > 0).sum() > 200, "Widget hat nichts gezeichnet"
    outside = a.copy()
    outside[max(y - 2, 0) : y + h + 2, max(x - 2, 0) : x + w + 2] = 0
    assert outside.max() == 0, "Widget zeichnet außerhalb seines Rechtecks"


def test_no_dark_fringes_on_antialiased_edges(telemetry):
    """Halbtransparente Kantenpixel weißer Schrift müssen weiß bleiben (straight alpha)."""
    cfg = widget(style=Style(text_color="#FFFFFF", label_color="#FFFFFF", shadow=False, outline=False))
    a = image_to_rgba(renderer(telemetry, [cfg]).render(0))
    edge = (a[..., 3] > 10) & (a[..., 3] < 245)
    assert edge.sum() > 50
    assert a[edge][:, :3].min() >= 235


def test_frame_bytes_match_image(telemetry):
    r = renderer(telemetry, [widget()])
    data = r.frame_bytes(0)
    assert len(data) == W * H * 4
    assert np.array_equal(np.frombuffer(data, np.uint8).reshape(H, W, 4), image_to_rgba(r.render(0)))


def test_unavailable_channel_is_skipped():
    import io

    from cycle_overlay.core.gpx_parser import parse_gpx
    from cycle_overlay.core.processor import process

    doc = ('<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
           + "".join(f'<trkpt lat="{48 + i * 1e-4}" lon="11"><time>2026-01-01T10:00:{i:02d}Z</time></trkpt>' for i in range(10))
           + "</trkseg></trk></gpx>")
    tel = process(parse_gpx(io.BytesIO(doc.encode())))
    r = renderer(tel, [widget(id="p", channel="power"), widget(id="s", channel="speed", y=0.5)], t=5)
    assert r.skipped == ["p"]


def test_values_change_between_frames(telemetry):
    cfg = widget(type="arc", channel="speed", min_value=0, max_value=50)
    r = renderer(telemetry, [cfg], t=295, n=300)  # Übergang 30 -> 18 km/h
    a0 = image_to_rgba(r.render(0))
    a1 = image_to_rgba(r.render(299))
    assert not np.array_equal(a0, a1)


def test_preview_with_checkerboard(telemetry):
    r = renderer(telemetry, [widget()])
    img = r.render_preview(0)
    assert (img.width(), img.height()) == (W, H)
    assert not img.hasAlphaChannel()


def test_render_in_worker_thread(telemetry):
    r = renderer(telemetry, load_preset().widgets, n=5)
    out = {}

    def job():
        out["bytes"] = [len(r.frame_bytes(i)) for i in range(5)]

    t = threading.Thread(target=job)
    t.start()
    t.join(60)
    assert out["bytes"] == [W * H * 4] * 5


def test_render_speed_1080p(telemetry):
    r = renderer(telemetry, load_preset().widgets, n=30, size=(1920, 1080))
    r.frame_bytes(0)
    t0 = time.perf_counter()
    for i in range(30):
        r.frame_bytes(i)
    per_frame = (time.perf_counter() - t0) / 30
    print(f"\n  Renderzeit 1080p: {per_frame * 1000:.1f} ms/Frame ({1 / per_frame:.0f} FPS)")
    assert per_frame < 0.5


@needs_ffmpeg
def test_end_to_end_prores_clip(telemetry, tmp_path):
    """PRD Phase 2: 2-Sekunden-Clip rendern, Alpha prüfen, Datei mit ffprobe prüfen."""
    r = renderer(telemetry, load_preset().widgets, t=440, n=60, size=(640, 360))
    res = run_export(r.frame_bytes, 60, 640, 360, 30, PROFILES["prores4444"], tmp_path / "clip.mov")
    assert res.pix_fmt.startswith("yuva444p")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(res.path), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgba", "-"], capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.uint8).reshape(360, 640, 4)
    assert a[180, 320, 3] == 0
    assert (a[..., 3] > 200).sum() > 500


@needs_ffmpeg
def test_cli_preview_and_render(tmp_path, capsys):
    png = tmp_path / "p.png"
    assert main(["preview", str(SAMPLE_GPX), "--at", "0:07:30", "--size", "960x540", "--out", str(png)]) == 0
    assert png.stat().st_size > 1000
    mov = tmp_path / "r.mov"
    assert main(["render", str(SAMPLE_GPX), "--start", "0:07:30", "--duration", "1", "--fps", "25",
                 "--size", "640x360", "--out", str(mov)]) == 0
    assert mov.exists()
    out = capsys.readouterr().out
    assert "mit Alpha" in out
    assert main(["render", str(SAMPLE_GPX), "--start", "0:07:30", "--duration", "1", "--out", str(mov)]) == 1  # existiert


@needs_ffmpeg
def test_cli_batch_render(tmp_path, capsys):
    from .conftest import make_clip

    a = make_clip(tmp_path / "GX010001.MP4", creation_time="2026-06-14T10:05:00.000000Z", handler="GoPro AVC",
                  seconds=1.0, size="320x180")
    b = make_clip(tmp_path / "GX020001.MP4", creation_time="2026-06-14T10:06:00.000000Z", handler="GoPro AVC",
                  seconds=1.0, size="320x180")
    far = make_clip(tmp_path / "x.mp4", creation_time="2026-07-01T10:00:00.000000Z", seconds=1.0, size="320x180")
    out = tmp_path / "overlays"
    code = main(["render", str(SAMPLE_GPX), "--video", str(a), str(b), str(far), "--tz", "Europe/Berlin",
                 "--out", str(out)])
    assert code == 2  # ein Clip übersprungen
    assert (out / "GX010001_overlay.mov").exists() and (out / "GX020001_overlay.mov").exists()
    assert "2 von 3 Clips exportiert" in capsys.readouterr().out


@pytest.mark.parametrize("preset", ["rennrad_minimal", "pro_cycling_cockpit"])
def test_presets_render(telemetry, preset, tmp_path):
    r = renderer(telemetry, load_preset(preset).widgets, t=600, n=3, size=(1920, 1080))
    a = image_to_rgba(r.render(1))
    assert a[540, 960, 3] == 0
    assert (a[..., 3] > 0).mean() > 0.01
    r.render_preview(1).save(str(tmp_path / f"{preset}.png"))


def test_map_and_profile_widgets(telemetry):
    cfgs = [
        widget(id="m", type="map", channel="position", size=0.4, scope="full"),
        widget(id="p", type="profile", channel="elevation", anchor="bottom_left", size=0.15, aspect=4.0),
    ]
    r = renderer(telemetry, cfgs, t=450, n=2)
    a = image_to_rgba(r.render(0))[..., 3]
    for cfg in cfgs:
        x, y, w, h = (int(round(v)) for v in cfg.rect(W, H))
        assert (a[y : y + h, x : x + w] > 0).sum() > 300, cfg.id
    clip = widget(id="m2", type="map", channel="position", size=0.4, scope="clip")
    rc = renderer(telemetry, [clip], t=450, n=30)
    assert (image_to_rgba(rc.render(15))[..., 3] > 0).sum() > 300


# --------------------------------------------------------------------------- Minimap

def _straight_telemetry(bearing_deg: float, n: int = 300, speed: float = 6.0):
    import io
    import math

    from cycle_overlay.core.gpx_parser import parse_gpx
    from cycle_overlay.core.processor import process

    lat0, lon0 = 48.0, 11.0
    pts = []
    for i in range(n):
        d = i * speed
        dn, de = d * math.cos(math.radians(bearing_deg)), d * math.sin(math.radians(bearing_deg))
        lat = lat0 + math.degrees(dn / 6_371_000)
        lon = lon0 + math.degrees(de / (6_371_000 * math.cos(math.radians(lat0))))
        pts.append(f'<trkpt lat="{lat:.8f}" lon="{lon:.8f}"><ele>100</ele>'
                   f"<time>2026-01-01T10:{i // 60:02d}:{i % 60:02d}Z</time></trkpt>")
    doc = '<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>' + "".join(pts) + "</trkseg></trk></gpx>"
    return process(parse_gpx(io.BytesIO(doc.encode())))


def _minimap(**mm):
    from cycle_overlay.rendering.layout import MinimapStyle

    base = dict(show_compass=False, auto_zoom=False, radius_m=300.0)
    base.update(mm)
    return widget(id="mm", type="minimap", channel="position", anchor="center", x=0.0, y=0.0, size=0.6,
                  minimap=MinimapStyle(**base))


def _render(tel, cfg, t=150.0, map_data=None, size=(W, H)):
    values = FrameValues(tel, frame_times(t, 1, 30), map_data=map_data)
    return image_to_rgba(CanvasRenderer(Layout("t", [cfg]), values, *size).render(0)).astype(int)


def test_qtransform_matches_camera():
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QTransform

    from cycle_overlay.rendering.minimap_geometry import Camera

    cam = Camera(120.0, 80.0, 33.0, True, 300.0, (10.0, 20.0, 400.0, 400.0), tilt_deg=50, anchor=0.66)
    qt = QTransform(*cam.qt_values())
    for wx, wy in ((120.0, 80.0), (200.0, 150.0), (50.0, 300.0)):
        p = qt.map(QPointF(wx, wy))
        sx, sy, _ = cam.project(wx, wy)
        assert (p.x(), p.y()) == pytest.approx((float(sx), float(sy)), abs=1e-6)


def test_minimap_round_shape_and_arrow():
    tel = _straight_telemetry(0)
    cfg = _minimap(shape="circle", theme="dark", arrow="triangle")
    a = _render(tel, cfg)
    x, y, w, h = cfg.rect(W, H)
    cx, cy, r = x + w / 2, y + h / 2, min(w, h) / 2
    yy, xx = np.mgrid[0:H, 0:W]
    outside = (xx - cx) ** 2 + (yy - cy) ** 2 > (r + 4) ** 2  # Randlinie ragt halb nach außen
    assert a[..., 3][outside].max() == 0  # nichts außerhalb des Kreises
    assert a[int(cy), int(cx - r * 0.7), 3] > 150  # Hintergrund innen
    px = a[int(cy), int(cx)]
    assert px[0] > 230 and px[1] > 230 and px[2] > 230  # weißer Pfeil in der Mitte


def _changed(a, b, cfg, threshold: int = 25) -> float:
    """Anteil der Pixel innerhalb des Widgets, die sich deutlich unterscheiden."""
    x, y, w, h = (int(round(v)) for v in cfg.rect(W, H))
    da = np.abs(a[y:y + h, x:x + w] - b[y:y + h, x:x + w]).max(axis=2)
    return float((da > threshold).mean())


def test_minimap_heading_up_is_direction_independent():
    north, east = _straight_telemetry(0), _straight_telemetry(90)
    cfg = _minimap(orientation="heading", background="route")
    assert _changed(_render(north, cfg), _render(east, cfg), cfg) < 0.003
    cfg_n = _minimap(orientation="north", background="route")
    assert _changed(_render(north, cfg_n), _render(east, cfg_n), cfg_n) > 0.01


def test_minimap_tilt_and_theme_change_image():
    tel = _straight_telemetry(45)
    cfg = _minimap(view="top")
    top = _render(tel, cfg)
    tilt = _render(tel, _minimap(view="tilt"))
    light = _render(tel, _minimap(theme="light"))
    assert _changed(top, tilt, cfg) > 0.01
    assert _changed(top, light, cfg) > 0.3


def test_minimap_draws_osm_roads_and_attribution():
    from cycle_overlay.core.osm import Feature, MapData

    tel = _straight_telemetry(0)
    lat, lon = tel.channels["lat"], tel.channels["lon"]
    # Querstraße und Parallelstraße in der Nähe der Position bei t=150
    roads = [Feature("primary", np.array([lat[150], lat[150]]), np.array([lon[150] - 0.004, lon[150] + 0.004])),
             Feature("minor", lat[100:200] * 1.0, lon[100:200] + 0.0015)]
    md = MapData(roads)
    cfg = _minimap(background="osm", theme="dark")
    with_map = _render(tel, cfg, map_data=md)
    without = _render(tel, _minimap(background="route", theme="dark"))
    assert _changed(with_map, without, cfg) > 0.005


def test_minimap_rounded_and_compass_and_scale():
    tel = _straight_telemetry(120)
    cfg = _minimap(shape="rounded", show_compass=True, show_scale=True, orientation="heading", arrow="dot")
    a = _render(tel, cfg)
    assert (a[..., 3] > 0).sum() > 1000


def test_minimap_render_speed_4k():
    from cycle_overlay.core.osm import Feature, MapData

    tel = _straight_telemetry(30, n=600)
    lat, lon = tel.channels["lat"], tel.channels["lon"]
    rng = np.random.default_rng(0)
    feats = []
    for k in range(3000):  # dichtes Straßennetz
        i = int(rng.integers(0, lat.size - 20))
        feats.append(Feature(["minor", "primary", "path", "cycle"][k % 4],
                             lat[i:i + 20] + rng.normal(0, 0.003), lon[i:i + 20] + rng.normal(0, 0.003)))
    md = MapData(feats)
    cfg = _minimap(view="tilt", background="osm", auto_zoom=True)
    values = FrameValues(tel, frame_times(200, 10, 30), map_data=md)
    r = CanvasRenderer(Layout("t", [cfg]), values, 3840, 2160)
    r.frame_bytes(0)
    t0 = time.perf_counter()
    for i in range(10):
        r.frame_bytes(i)
    per = (time.perf_counter() - t0) / 10
    print(f"\n  Minimap 4K: {per * 1000:.0f} ms/Frame")
    assert per < 0.6


# --------------------------------------------------------------------------- Höhe in der Karte

def test_minimap_gradient_coloring_uses_grade_colors(telemetry):
    from cycle_overlay.rendering.layout import DEFAULT_GRADE_COLORS, parse_color

    cfg = _minimap(route_coloring="gradient", background="route", orientation="north", radius_m=400.0)
    a = _render(telemetry, cfg, t=300.0)  # Beginn des 5-%-Anstiegs: voraus orange/gelb
    x, y, w, h = (int(round(v)) for v in cfg.rect(W, H))
    roi = a[y:y + h, x:x + w, :3].reshape(-1, 3)

    def near(color):
        r, g, b, _ = parse_color(color)
        return (np.abs(roi - np.array([r, g, b])).max(axis=1) < 30).sum()

    assert near(DEFAULT_GRADE_COLORS[2]) + near(DEFAULT_GRADE_COLORS[3]) > 50  # gelb/orange am Anstieg
    # gefahrener flacher Teil: grün, aber absichtlich abgedunkelt -> nur den Farbton prüfen
    greenish = (roi[:, 1] > roi[:, 0] + 25) & (roi[:, 1] > roi[:, 2] + 10)
    assert greenish.sum() > 20


def test_minimap_ribbon_raises_climb_ahead(telemetry):
    base = dict(view="tilt", background="route", orientation="heading", radius_m=500.0)
    flat = _render(telemetry, _minimap(**base), t=300.0)
    ribbon_cfg = _minimap(**base, elevation_ribbon=True, ribbon_exaggeration=6.0)
    ribbon = _render(telemetry, ribbon_cfg, t=300.0)
    assert _changed(flat, ribbon, ribbon_cfg) > 0.005
    # Höhenband in der Draufsicht: keine Wirkung
    top_a = _render(telemetry, _minimap(background="route"), t=300.0)
    top_b = _render(telemetry, _minimap(background="route", elevation_ribbon=True), t=300.0)
    assert _changed(top_a, top_b, ribbon_cfg) < 0.003


def test_minimap_grade_badge(telemetry):
    plain = _render(telemetry, _minimap(), t=450.0)
    cfg = _minimap(grade_badge=True)
    badge = _render(telemetry, cfg, t=450.0)
    assert _changed(plain, badge, cfg) > 0.002


def test_route_map_gradient(telemetry):
    from cycle_overlay.rendering.layout import MinimapStyle

    a = widget(id="m", type="map", channel="position", size=0.5, scope="full")
    b = widget(id="m", type="map", channel="position", size=0.5, scope="full",
               minimap=MinimapStyle(route_coloring="gradient"))
    assert _changed(_render(telemetry, a), _render(telemetry, b), a) > 0.002


@pytest.mark.parametrize("t,expect_info", [(0.0, "IN"), (450.0, "ANSTIEG"), (1100.0, "KEIN")])
def test_climb_preview(telemetry, t, expect_info):
    from cycle_overlay.rendering.layout import ClimbStyle
    from cycle_overlay.rendering.widgets.climb_preview import ClimbPreviewWidget

    cfg = widget(id="c", type="climb", channel="elevation", anchor="top_left", size=0.2, aspect=3.0,
                 climb=ClimbStyle(lookahead_m=3000))
    values = FrameValues(telemetry, frame_times(t, 1, 30))
    title, detail, _ = ClimbPreviewWidget(cfg, values).climb_info(0)
    assert title.startswith(expect_info)
    a = image_to_rgba(CanvasRenderer(Layout("t", [cfg]), values, W, H).render(0))
    x, y, w, h = (int(round(v)) for v in cfg.rect(W, H))
    assert (a[y:y + h, x:x + w, 3] > 0).sum() > 500
    hidden = widget(id="c", type="climb", channel="elevation", anchor="top_left", size=0.2,
                    climb=ClimbStyle(hide_when_none=True))
    empty = image_to_rgba(CanvasRenderer(Layout("t", [hidden]), FrameValues(telemetry, frame_times(1100.0, 1, 30)),
                                         W, H).render(0))
    assert empty[..., 3].max() == 0


@pytest.mark.parametrize("t,expect", [(650.0, "ABFAHRT"), (870.0, "ABFAHRT"), (450.0, "ANSTIEG"), (0.0, "IN")])
def test_climb_preview_descents(telemetry, t, expect):
    from cycle_overlay.rendering.layout import ClimbStyle
    from cycle_overlay.rendering.widgets.climb_preview import ClimbPreviewWidget

    cfg = widget(id="c", type="climb", channel="elevation", anchor="top_left", size=0.2, aspect=3.0,
                 climb=ClimbStyle(mode="both", lookahead_m=3000))
    values = FrameValues(telemetry, frame_times(t, 1, 30))
    title, detail, item = ClimbPreviewWidget(cfg, values).climb_info(0)
    assert title.startswith(expect)
    if item is not None and item.is_descent:
        assert "−" in detail
    image_to_rgba(CanvasRenderer(Layout("t", [cfg]), values, W, H).render(0))


@pytest.mark.parametrize("t,expect", [(650.0, "DESCENT"), (450.0, "CLIMB"), (0.0, "CLIMB IN")])
def test_climb_preview_english(telemetry, t, expect):
    from cycle_overlay.rendering.layout import ClimbStyle
    from cycle_overlay.rendering.widgets.climb_preview import ClimbPreviewWidget

    cfg = widget(id="c", type="climb", channel="elevation", anchor="top_left", size=0.2, aspect=3.0,
                 climb=ClimbStyle(mode="both", lookahead_m=3000))
    values = FrameValues(telemetry, frame_times(t, 1, 30))
    layout = Layout("t", [cfg], language="en")
    CanvasRenderer(layout, values, W, H).render(0)  # setzt die Sprache der Werte
    title, detail, _ = ClimbPreviewWidget(cfg, values).climb_info(0)
    assert title.startswith(expect)
    assert "noch" not in detail and "hm" not in detail


def test_renderer_follows_layout_language(telemetry):
    cfg = widget(id="s", type="text", channel="speed")
    values = FrameValues(telemetry, frame_times(10.0, 1, 30))
    layout = Layout("t", [cfg])
    r = CanvasRenderer(layout, values, W, H)
    assert values.label(cfg) == "TEMPO"
    de = image_to_rgba(r.render(0))
    layout.language = "en"
    en = image_to_rgba(r.render(0))
    assert values.label(cfg) == "SPEED"
    assert (de != en).any()


def test_ribbon_reference_lowest_differs(telemetry):
    base = dict(view="tilt", background="route", radius_m=500.0, elevation_ribbon=True, ribbon_exaggeration=4.0)
    cfg = _minimap(**base)
    pos = _render(telemetry, cfg, t=650.0)  # oben vor der Abfahrt
    low = _render(telemetry, _minimap(**base, ribbon_reference="lowest"), t=650.0)
    assert _changed(pos, low, cfg) > 0.005


def test_descent_scheme_colors_route(telemetry):
    from cycle_overlay.rendering.layout import GRADE_SCHEMES

    _, _, colors = GRADE_SCHEMES["both"]
    steps = [-12.0, -8.0, -7.0, -2.0, 2.0, 5.0, 8.0, 12.0]  # −6 % der Testfahrt fällt so in „hellblau“
    a = _minimap(route_coloring="gradient", background="route", orientation="north")
    b = widget(id="mm", type="minimap", channel="position", anchor="center", x=0.0, y=0.0, size=0.6,
               minimap=a.minimap, grade_steps=steps, grade_colors=list(colors))
    assert _changed(_render(telemetry, a, t=870.0), _render(telemetry, b, t=870.0), a) > 0.002
