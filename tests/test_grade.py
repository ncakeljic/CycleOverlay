from __future__ import annotations

import numpy as np
import pytest

from cycle_overlay.rendering.grade import (
    DEFAULT_COLORS,
    class_runs,
    climb_status,
    climbs_for,
    detect_climbs,
    grade_class,
    grade_color,
    smooth_grade,
)


def test_grade_classes_and_colors():
    assert list(grade_class([-6, -2.5, 0, 1.9, 2, 4.9, 5, 7.9, 8, 11.9, 12, 25, np.nan])) == \
        [0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 1]
    assert grade_color(6.0) == DEFAULT_COLORS[3]
    assert grade_color(1.0, steps=(-1, 0.5), colors=("#000000", "#111111", "#222222")) == "#222222"


def test_class_runs_share_boundaries():
    cls = np.array([1, 1, 2, 2, 2, 1])
    runs = class_runs(cls)
    assert runs[0] == (1, 0, 2) and runs[1] == (2, 2, 5)
    mask = np.array([True, True, False, True, True, True])
    runs = class_runs(cls, mask)
    assert all(mask[a] and mask[b] for _, a, b in runs)
    assert class_runs(np.array([])) == []


def test_smooth_grade_on_sample(telemetry):
    g = smooth_grade(telemetry)
    assert g[450] == pytest.approx(5.0, abs=0.7)
    assert g[870] == pytest.approx(-6.0, abs=0.7)
    cls = grade_class(g)
    assert cls[450] in (2, 3) and cls[870] == 0 and cls[150] == 1


def test_detect_climb_in_sample(telemetry):
    climbs = climbs_for(telemetry)
    assert len(climbs) == 1
    c = climbs[0]
    assert c.length == pytest.approx(1500, abs=120)
    assert c.gain == pytest.approx(75, abs=6)
    assert c.avg_grade == pytest.approx(5.0, abs=0.6)
    assert c.start_d == pytest.approx(2500, abs=120)


def test_detect_climbs_tolerates_short_dips_and_ignores_rollers():
    d = np.arange(0, 6000, 5.0)
    e = np.where(d < 1000, 100.0, 100 + (d - 1000) * 0.06)  # 6 % ab km 1
    e = np.where((d > 1800) & (d < 1850), e - 4, e)  # kurze Delle
    e = np.where(d > 3000, 220 - (d - 3000) * 0.05, e)  # Abfahrt
    roller = 1.0 * np.sin(d / 80)  # sanfte Wellen ± 1 m
    climbs = detect_climbs(d, e + roller)
    assert len(climbs) == 1
    assert climbs[0].start_d == pytest.approx(1000, abs=100)
    assert climbs[0].end_d == pytest.approx(3000, abs=60)
    assert climbs[0].max_grade >= 5.5
    assert detect_climbs(d, 100 + roller) == []


def test_climb_status():
    from cycle_overlay.rendering.grade import Climb

    climbs = [Climb(1000, 2500, 100, 190, 8)]
    assert climb_status(climbs, 500, 3000)[0] == "ahead"
    assert climb_status(climbs, 500, 300)[0] == "none"
    st, c = climb_status(climbs, 1200, 3000)
    assert st == "in" and c.gain == 90
    assert climb_status(climbs, 2600, 3000)[0] == "none"


def test_descent_detection_in_sample(telemetry):
    from cycle_overlay.rendering.grade import climbs_for

    downs = climbs_for(telemetry, mode="descents")
    assert len(downs) == 1
    d = downs[0]
    assert d.is_descent and d.gain == pytest.approx(-225, abs=10)
    assert d.avg_grade == pytest.approx(-6.0, abs=0.7)
    assert d.max_grade < -5
    both = climbs_for(telemetry, mode="both")
    assert [c.kind for c in both] == ["climb", "descent"]


def test_climb_status_with_descents():
    from cycle_overlay.rendering.grade import Climb

    items = [Climb(1000, 2000, 100, 180, 9), Climb(2500, 4000, 180, 60, -11, "descent")]
    st, c = climb_status(items, 2200, 1000)
    assert st == "ahead" and c.is_descent
    st, c = climb_status(items, 3000, 1000)
    assert st == "in" and c.is_descent and c.gain == -120


def test_grade_schemes():
    from cycle_overlay.rendering.layout import GRADE_SCHEMES, WidgetConfig, grade_scheme_of

    for key, (_, steps, colors) in GRADE_SCHEMES.items():
        assert len(colors) == len(steps) + 1 and steps == sorted(steps)
        w = WidgetConfig(id="a", type="text", channel="speed", grade_steps=list(steps), grade_colors=list(colors))
        w.validate()
        assert grade_scheme_of(steps, colors) == key
    _, steps, colors = GRADE_SCHEMES["both"]
    # abgestufte Abfahrt: -3 %, -6 %, -10 %, -15 % bekommen unterschiedliche Farben
    assert len({grade_color(g, steps, colors) for g in (-3, -6, -10, -15)}) == 4
    assert grade_scheme_of([1.0], ["#000000", "#FFFFFF"]) is None


def test_lowest_reference(telemetry):
    from cycle_overlay.rendering.grade import lowest_reference

    ref = lowest_reference(telemetry, 800, 1600)
    ele = telemetry.channels["elevation"]
    assert np.all(ref <= ele + 2.0)  # liegt (bis auf Glättung) nie über der eigenen Höhe
    assert ref[650] < ele[650] - 50  # oben am Gipfel: tiefere Teile der Abfahrt voraus sind der Bezug
