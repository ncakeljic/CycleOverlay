"""Sprache der Overlay-Texte (ohne Qt)."""

from __future__ import annotations

from cycle_overlay.core.units import CHANNELS
from cycle_overlay.rendering.i18n import CHANNEL_LABELS, LANGUAGES, TEXTS, channel_text, normalize_language, tr
from cycle_overlay.rendering.layout import Layout
from cycle_overlay.rendering.values import channel_label


def test_every_channel_has_a_label_in_every_language():
    for lang in LANGUAGES:
        for ch in CHANNELS:
            assert ch in CHANNEL_LABELS[lang], (lang, ch)
        assert set(TEXTS[lang]) == set(TEXTS["de"])


def test_channel_label_languages():
    assert channel_label("speed") == "TEMPO"
    assert channel_label("speed", "en") == "SPEED"
    assert channel_label("time_of_day", "en") == "TIME"
    assert channel_label("position", "de") == "ROUTE"
    assert channel_text("heart_rate", "xx") == "PULS"  # unbekannt -> Standard (Deutsch)


def test_tr_formats_and_falls_back():
    assert tr("climb_ahead", "de", dist="2.0 km") == "IN 2.0 km"
    assert tr("climb_ahead", "en", dist="2.0 km") == "CLIMB IN 2.0 km"
    assert tr("flat", "fr") == "FLACH"
    assert normalize_language(None) == "de" and normalize_language("en") == "en"


def test_layout_language_roundtrip_and_old_files():
    lay = Layout("x", [], language="en")
    assert Layout.from_dict(lay.to_dict()).language == "en"
    assert Layout.from_dict({"name": "alt", "widgets": []}).language == "de"  # Layouts vor 1.6.0
    assert Layout.from_dict({"name": "x", "widgets": [], "language": "klingonisch"}).language == "de"
