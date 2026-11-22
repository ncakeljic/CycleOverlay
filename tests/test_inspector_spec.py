"""Die Inspector-Beschreibung deckt alle Einstellungen ab (ohne Qt)."""

from dataclasses import fields

import pytest

from cycle_overlay.gui.inspector_spec import SEGMENT_MAX, covered_settings, sections_for, visible_sections
from cycle_overlay.rendering.layout import ClimbStyle, MinimapStyle, Style, WidgetConfig

ALWAYS_HIDDEN = {"id", "aspect"}  # intern
VALUE_ONLY = {"label", "label_position", "show_unit", "decimals", "update_rate_hz", "min_value", "max_value",
              "zones", "zones_color_text", "sweep_deg", "show_needle", "show_ticks", "orientation", "stroke",
              "scope", "show_ridden"}


def _cfg(wtype, channel="speed", **kw):
    if wtype in ("minimap", "map"):
        channel = "position"
    if wtype == "climb":
        channel = "elevation"
    from cycle_overlay.rendering.layout import MAP_CHANNEL

    if channel == "position":
        channel = MAP_CHANNEL
    return WidgetConfig(id="w", channel=channel, type=wtype, **kw)


def test_minimap_covers_every_minimap_setting():
    cov = covered_settings(_cfg("minimap"))
    missing = {f.name for f in fields(MinimapStyle)} - {k for t, k in cov if t == "minimap"}
    assert not missing, f"Minimap-Einstellungen ohne Bedienelement: {missing}"
    assert ("style", "font_family") in cov and ("style", "shadow") in cov


def test_climb_covers_every_climb_setting():
    cov = covered_settings(_cfg("climb"))
    missing = {f.name for f in fields(ClimbStyle)} - {k for t, k in cov if t == "climb"}
    assert not missing
    assert {("cfg", "grade_steps"), ("cfg", "grade_colors")} <= cov


@pytest.mark.parametrize("wtype", ["text", "arc", "bar", "map", "profile"])
def test_value_widgets_cover_their_settings(wtype):
    cov = covered_settings(_cfg(wtype))
    cfg_keys = {k for t, k in cov if t == "cfg"}
    base = {f.name for f in fields(WidgetConfig)} - ALWAYS_HIDDEN - {"minimap", "climb", "style", "grade_steps",
                                                                      "grade_colors"} - VALUE_ONLY
    assert base <= cfg_keys, base - cfg_keys
    style_keys = {k for t, k in cov if t == "style"}
    expected_style = {f.name for f in fields(Style)}
    if wtype == "text":
        expected_style -= {"accent_color", "track_color"}
    assert expected_style <= style_keys


def test_type_specific_fields_only_where_they_apply():
    def visible_keys(cfg):
        return {f.key for _s, fs in visible_sections(cfg) for f in fs}

    arc, bar, text = _cfg("arc"), _cfg("bar"), _cfg("text")
    assert "sweep_deg" in visible_keys(arc) and "sweep_deg" not in visible_keys(bar)
    assert "orientation" in visible_keys(bar) and "orientation" not in visible_keys(text)
    mm = _cfg("minimap")
    assert "tilt_deg" not in visible_keys(mm)
    mm.minimap.view = "tilt"
    assert "tilt_deg" in visible_keys(mm)
    assert "ribbon_exaggeration" not in visible_keys(mm)
    mm.minimap.elevation_ribbon = True
    assert {"ribbon_exaggeration", "ribbon_reference", "ribbon_curtain"} <= visible_keys(mm)
    assert "ribbon_hint" not in visible_keys(mm)  # Hinweis nur, wenn nicht 3D
    mm.minimap.view = "top"
    assert "ribbon_hint" in visible_keys(mm)
    # Steigungsfarben nur, wenn die Route danach gefärbt wird
    assert "grade" not in {s.key for s, _ in visible_sections(mm)}
    mm.minimap.route_coloring = "gradient"
    assert "grade" in {s.key for s, _ in visible_sections(mm)}


def test_small_choices_become_buttons():
    for wtype in ("text", "arc", "bar", "minimap", "climb", "map"):
        for sec in sections_for(_cfg(wtype)):
            for f in sec.fields:
                if f.kind == "choice":
                    assert f.segmented == (len(f.options) <= SEGMENT_MAX)
    mm = sections_for(_cfg("minimap"))
    view = next(s for s in mm if s.key == "view")
    assert all(f.segmented for f in view.fields if f.kind == "choice")


def test_summaries_render_for_all_types():
    for wtype in ("text", "arc", "bar", "minimap", "climb", "map", "profile"):
        cfg = _cfg(wtype)
        for sec, _f in visible_sections(cfg):
            if sec.summary is not None:
                assert isinstance(sec.summary(cfg), str)


def test_section_keys_unique():
    for wtype in ("text", "minimap", "climb"):
        keys = [s.key for s in sections_for(_cfg(wtype))]
        assert len(keys) == len(set(keys))
