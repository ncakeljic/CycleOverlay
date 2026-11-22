"""Schrift-Einstellungen (ohne Qt)."""

from __future__ import annotations


import pytest

from cycle_overlay.app_paths import resource_path
from cycle_overlay.rendering.layout import Layout, Style, WidgetConfig
from cycle_overlay.rendering.typography import (
    BUNDLED_FAMILIES,
    TYPE_PRESETS,
    WEIGHTS,
    apply_preset,
    preset_of,
    summary,
)


def test_default_style_is_classic_preset():
    assert preset_of(Style()) == "classic"
    assert summary(Style()).startswith("Klassisch")


@pytest.mark.parametrize("key", list(TYPE_PRESETS))
def test_presets_apply_and_validate(key):
    st = Style(text_color="#FF0000")
    apply_preset(st, key)
    st.validate()
    assert preset_of(st) == key and st.text_color == "#FF0000"  # Farben bleiben
    assert TYPE_PRESETS[key].font_family in BUNDLED_FAMILIES


def test_presets_are_distinct():
    sigs = {(p.font_family, p.value_weight, p.font_width, p.italic) for p in TYPE_PRESETS.values()}
    assert len(sigs) == len(TYPE_PRESETS)


def test_every_bundled_family_has_files_and_license():
    fonts = resource_path("fonts")
    names = " ".join(p.name for p in fonts.iterdir())
    for fam in BUNDLED_FAMILIES:
        assert fam.replace(" ", "") in names, fam
    assert len(list(fonts.glob("*LICENSE*"))) >= 6


def test_style_validation():
    for bad in (dict(value_weight="ultra"), dict(font_width=3.0), dict(tracking=-50), dict(label_scale=0.1),
                dict(shadow_distance=9), dict(badge_radius=0.9)):
        with pytest.raises(ValueError):
            Style(**bad).validate()
    Style(font_width=0.6, tracking=40, label_scale=2.0, unit_scale=0.5, italic=True).validate()
    assert set(WEIGHTS) >= {"light", "regular", "bold", "black"}


def test_new_style_fields_roundtrip_and_old_layouts():
    w = WidgetConfig(id="a", type="text", channel="speed")
    apply_preset(w.style, "poster")
    w.style.label_uppercase = True
    w.style.badge_radius = 0.5
    lay = Layout.from_dict(Layout("x", [w]).to_dict())
    st = lay.widgets[0].style
    assert preset_of(st) == "poster" and st.label_uppercase and st.badge_radius == 0.5
    old = {"name": "alt", "widgets": [{"id": "a", "type": "text", "channel": "speed",
                                       "style": {"font_family": "Inter", "shadow": True}}]}
    assert preset_of(Layout.from_dict(old).widgets[0].style) == "classic"
