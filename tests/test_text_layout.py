"""Texte eines Widgets dürfen sich nicht überlappen und nicht aus dem Widget ragen.

Zeichnet jede Vorlage in mehreren Größen, Schriftstilen und mit extremen Einstellungen und prüft die
genauen Umrisse aller Texte (``TextRecorder``).
"""

from __future__ import annotations

import copy

import pytest

pytest.importorskip("PySide6")

from pathlib import Path  # noqa: E402

from cycle_overlay.core.project import ProjectModel  # noqa: E402
from cycle_overlay.rendering.canvas_renderer import CanvasRenderer  # noqa: E402
from cycle_overlay.rendering.layout import Layout  # noqa: E402
from cycle_overlay.rendering.qt_support import TextRecorder, ensure_app  # noqa: E402
from cycle_overlay.rendering.templates import TEMPLATES  # noqa: E402
from cycle_overlay.rendering.typography import apply_preset  # noqa: E402

SAMPLE_GPX = Path(__file__).parent / "fixtures" / "sample_ride.gpx"
W, H = 1920, 1080
TOL = 1.0  # Pixel Spielraum (Kantenglättung)


@pytest.fixture(scope="module")
def model():
    ensure_app()
    m = ProjectModel()
    m.load_gpx(SAMPLE_GPX)
    return m


def problems(m, cfg, t) -> list[str]:
    values = m.frame_values([t], t0=t)
    with TextRecorder() as rec:
        CanvasRenderer(Layout("t", [cfg], language=m.project.layout.language), values, W, H,
                       hide_unavailable=False).render(0)
    x, y, w, h = cfg.rect(W, H)
    out = []
    for k, (ta, ra) in enumerate(rec.items):
        if ra.left() < x - 2 or ra.top() < y - 2 or ra.right() > x + w + 2 or ra.bottom() > y + h + 2:
            out.append(f"„{ta}“ ragt aus dem Widget")
        for tb, rb in rec.items[k + 1:]:
            inter = ra.intersected(rb)
            if inter.width() > TOL and inter.height() > TOL:
                out.append(f"„{ta}“ überlappt „{tb}“")
    return out


def _variants(m, key):
    base = m.widget_from_template(key, None)
    for size_k in (0.6, 1.0, 1.8):
        for preset in ("classic", "poster", "compact_bold", "race"):
            c = copy.deepcopy(base)
            c.size = min(base.size * size_k, 0.9)
            apply_preset(c.style, preset)
            yield f"{preset} ×{size_k}", c
    for name, extra in (("Beschriftung 200 %", {"label_scale": 2.0}), ("Einheit 200 %", {"unit_scale": 2.0}),
                        ("Breite 140 %", {"font_width": 1.4}), ("Abstand 40 %", {"tracking": 40.0}),
                        ("alles groß", {"label_scale": 2.0, "unit_scale": 2.0, "font_width": 1.4})):
        c = copy.deepcopy(base)
        for a, val in extra.items():
            setattr(c.style, a, val)
        yield name, c


@pytest.mark.parametrize("key", list(TEMPLATES))
def test_template_texts_do_not_collide(model, key):
    dur = model.telemetry.duration
    found = []
    for name, cfg in _variants(model, key):
        for t in (dur * 0.3, dur * 0.7):
            found += [f"{name}: {p}" for p in problems(model, cfg, t)]
    assert not found, "\n".join(sorted(set(found))[:15])


@pytest.mark.parametrize("label_position", ["top", "left", "bottom"])
@pytest.mark.parametrize("aspect", [1.2, 4.0])
def test_narrow_and_wide_text_widgets(model, label_position, aspect):
    found = []
    for ch in ("speed", "time_of_day", "elapsed_time", "distance"):
        c = model.widget_from_template("text", ch)
        c.aspect, c.label_position = aspect, label_position
        found += problems(model, c, model.telemetry.duration * 0.5)
    assert not found, "\n".join(found)


def test_arc_variants(model):
    found = []
    for sweep in (180.0, 270.0, 360.0):
        for needle in (False, True):
            c = model.widget_from_template("needle", None)
            c.sweep_deg, c.show_needle = sweep, needle
            found += [f"{sweep:.0f}° Zeiger={needle}: {p}" for p in problems(model, c, model.telemetry.duration * 0.5)]
    assert not found, "\n".join(found)


def test_english_imperial(model):
    m = model
    m.project.layout.language, m.project.units = "en", "imperial"
    try:
        found = []
        for key in TEMPLATES:
            found += [f"{key}: {p}" for p in problems(m, m.widget_from_template(key, None), m.telemetry.duration * 0.5)]
    finally:
        m.project.layout.language, m.project.units = "de", "metric"
    assert not found, "\n".join(found)
