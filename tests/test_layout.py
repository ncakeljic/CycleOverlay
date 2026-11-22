from __future__ import annotations


import pytest

from cycle_overlay.core.presets import list_presets, load_preset, save_user_preset
from cycle_overlay.rendering.layout import ANCHORS, Layout, Style, WidgetConfig, Zone, parse_color


def w(**kw) -> WidgetConfig:
    base = dict(id="a", type="text", channel="speed")
    base.update(kw)
    return WidgetConfig(**base)


def test_parse_color():
    assert parse_color("#FF0000") == (255, 0, 0, 255)
    assert parse_color("#00FF0080") == (0, 255, 0, 128)
    for bad in ("red", "#FFF", "#GG0000", None):
        with pytest.raises(ValueError):
            parse_color(bad)


@pytest.mark.parametrize(
    "anchor,expected",
    [
        ("top_left", (96, 54)),
        ("top_right", (1920 - 96 - 259.2, 54)),
        ("bottom_left", (96, 1080 - 54 - 108)),
        ("bottom_right", (1920 - 96 - 259.2, 1080 - 54 - 108)),
        ("center", (960 + 96 - 129.6, 540 + 54 - 54)),
    ],
)
def test_rect_per_anchor(anchor, expected):
    cfg = w(anchor=anchor, x=0.05, y=0.05, size=0.1)  # Höhe 108 px, Breite 2.4 * 108
    x, y, ww, hh = cfg.rect(1920, 1080)
    assert (ww, hh) == pytest.approx((259.2, 108))
    assert (x, y) == pytest.approx(expected)


def test_rect_scales_proportionally_to_4k():
    cfg = w(anchor="bottom_right", x=0.03, y=0.07, size=0.12)
    hd = cfg.rect(1920, 1080)
    uhd = cfg.rect(3840, 2160)
    assert uhd == pytest.approx(tuple(v * 2 for v in hd))


@pytest.mark.parametrize("anchor", list(ANCHORS))
def test_move_to_roundtrip(anchor):
    cfg = w(anchor=anchor, size=0.1)
    cfg.move_to(400.0, 300.0, 1920, 1080)
    x, y, _, _ = cfg.rect(1920, 1080)
    assert (x, y) == pytest.approx((400.0, 300.0))


def test_aspect_per_type():
    assert w(type="arc").effective_aspect() == 1.0
    assert w(type="bar").effective_aspect() == 4.0
    assert w(type="bar", orientation="vertical").effective_aspect() < 1
    assert w(aspect=3.0).effective_aspect() == 3.0


def test_validation():
    for bad in (dict(type="pie"), dict(type="map"), dict(channel="position"), dict(scope="alles"), dict(anchor="links"), dict(size=0), dict(label_position="oben"),
                dict(min_value=10, max_value=5), dict(zones=[Zone(1, "rot")]), dict(style=Style(text_color="weiß"))):
        with pytest.raises(ValueError):
            w(**bad).validate()


def test_json_roundtrip(tmp_path):
    lay = Layout("Test", [w(zones=[Zone(300, "#FFAA00")], style=Style(badge=True)), w(id="b", type="arc", channel="power")])
    p = lay.save(tmp_path / "x.json")
    again = Layout.load(p)
    assert again == lay
    assert again.widgets[0].zones[0].from_value == 300


def test_layout_rejects_duplicate_ids_and_garbage(tmp_path):
    lay = Layout("x", [w(), w()])
    with pytest.raises(ValueError):
        Layout.from_dict(lay.to_dict())
    bad = tmp_path / "bad.json"
    bad.write_text("{kein json")
    with pytest.raises(ValueError):
        Layout.load(bad)


def test_unknown_keys_are_ignored():
    d = w().to_dict()
    d["zukunft"] = 1
    d["style"]["neu"] = 2
    assert WidgetConfig.from_dict(d).id == "a"


BUILTIN = ["rennrad_minimal", "pro_cycling_cockpit", "rennrad_navi"]


def test_builtin_presets_exist():
    assert set(BUILTIN) <= set(list_presets())
    assert load_preset().name == "Rennrad Minimal"


@pytest.mark.parametrize("name", BUILTIN)
def test_builtin_preset_fits_into_frame(name):
    lay = load_preset(name)
    for W, H in ((1920, 1080), (3840, 2160), (2704, 1520)):
        for cfg in lay.widgets:
            x, y, ww, hh = cfg.rect(W, H)
            assert x >= 0 and y >= 0 and x + ww <= W and y + hh <= H, (cfg.id, W, H)


@pytest.mark.parametrize("name", BUILTIN)
def test_builtin_widgets_do_not_overlap(name):
    lay = load_preset(name)
    rects = [cfg.rect(1920, 1080) for cfg in lay.widgets]
    for i, a in enumerate(rects):
        for b in rects[i + 1 :]:
            overlap_x = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
            overlap_y = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
            assert overlap_x <= 0 or overlap_y <= 0


def test_user_presets(tmp_path):
    lay = Layout("Mein Layout", [w()])
    path = save_user_preset(lay, "mein layout!")
    assert path.name == "mein_layout.json"
    assert "mein_layout" in list_presets()
    assert load_preset("mein_layout") == lay
    assert load_preset(path) == lay
    with pytest.raises(ValueError):
        load_preset("gibtsnicht")
    with pytest.raises(ValueError):
        load_preset(tmp_path / "fehlt.json")
