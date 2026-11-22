"""Filterstärke je Kanal (Aus/niedrig/mittel/stark)."""

import numpy as np
import pytest

from cycle_overlay.core.processor import FILTER_DEFAULTS, ProcessingConfig, config_for, filter_value
from cycle_overlay.core.project import Project, ProjectModel

from .conftest import SAMPLE_GPX


def test_defaults_match_previous_behaviour():
    cfg, old = config_for({}), ProcessingConfig()
    for ch in ("speed", "power", "elevation", "temperature"):
        assert cfg.smoothing_s[ch] == old.smoothing_s[ch]
    assert cfg.gradient_window_m == old.gradient_window_m


def test_levels_increase():
    for ch in FILTER_DEFAULTS:
        vals = [filter_value(ch, lv) for lv in ("off", "low", "medium", "strong")]
        assert vals == sorted(vals) and vals[0] < vals[-1]
    with pytest.raises(ValueError):
        filter_value("speed", "extrem")


def test_filter_changes_telemetry_and_is_saved(tmp_path):
    m = ProjectModel()
    m.load_gpx(SAMPLE_GPX)
    raw_like = m.telemetry.channels["speed"].copy()
    m.set_filter("speed", "strong")
    strong = m.telemetry.channels["speed"]
    assert np.abs(np.diff(strong)).mean() < np.abs(np.diff(raw_like)).mean()
    m.set_filter("speed", "off")
    off = m.telemetry.channels["speed"]
    assert np.abs(np.diff(off)).mean() > np.abs(np.diff(raw_like)).mean()
    assert m.filter_level("speed") == "off" and m.filter_level("power") == "low"
    m.set_filter("speed", "low")  # Standard -> nicht gespeichert
    assert "speed" not in m.project.filters
    m.set_filter("heart_rate", "medium")
    m.set_pause_mode("hold")
    m.project.count_numbers = False
    path = m.save(tmp_path / "p")
    m2 = ProjectModel.open(path)
    assert m2.filter_level("heart_rate") == "medium" and m2.project.pause_mode == "hold"
    assert m2.project.count_numbers is False and Project.from_dict({}).count_numbers is True
    with pytest.raises(ValueError):
        m.set_filter("speed", "kaputt")


def test_broken_filter_entries_ignored():
    p = Project.from_dict({"filters": {"speed": "ultra", "power": "strong", "foo": "low"}, "pause_mode": "x"})
    assert p.filters == {"power": "strong"} and p.pause_mode == "zero"
