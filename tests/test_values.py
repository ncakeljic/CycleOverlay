from __future__ import annotations

import math

import numpy as np
import pytest

from cycle_overlay.core.processor import frame_times
from cycle_overlay.rendering.layout import WidgetConfig, Zone
from cycle_overlay.rendering.values import FrameValues, format_number


def w(**kw) -> WidgetConfig:
    base = dict(id="a", type="text", channel="speed")
    base.update(kw)
    return WidgetConfig(**base)


@pytest.fixture()
def values(telemetry):
    # 4 s ab Track-Zeit 290 s (Übergang Ebene -> Anstieg), 60 FPS
    return FrameValues(telemetry, frame_times(290.0, 240, 60), tz="Europe/Berlin")


def test_text_updates_only_with_update_rate(values):
    values.count_numbers = False  # Verhalten „Springen“
    cfg = w(channel="speed", update_rate_hz=4)
    texts = [values.text(cfg, i)[0] for i in range(values.n_frames)]
    changes = sum(1 for a, b in zip(texts, texts[1:]) if a != b)
    assert changes <= 4 * 4  # höchstens 4 Wechsel pro Sekunde
    # alle 15 Frames (60/4) darf sich der Text ändern, dazwischen nicht
    for i in range(values.n_frames):
        if i % 15:
            assert texts[i] == texts[i - 1]


def test_every_frame_when_rate_zero(values):
    cfg = w(channel="distance", update_rate_hz=0, decimals=4)
    texts = {values.text(cfg, i)[0] for i in range(values.n_frames)}
    assert len(texts) > 50


def test_gauge_fraction_is_smooth_and_clamped(values):
    cfg = w(type="arc", channel="speed", min_value=0, max_value=70)
    fr = np.array([values.fraction(cfg, i) for i in range(values.n_frames)])
    assert np.all((fr >= 0) & (fr <= 1))
    assert np.max(np.abs(np.diff(fr))) < 0.01  # flüssig
    assert fr[0] == pytest.approx(30 / 70, abs=0.01)
    tight = w(type="arc", channel="speed", min_value=0, max_value=10)
    assert values.fraction(tight, 0) == 1.0


def test_units_and_decimals(values):
    assert values.text(w(channel="speed"), 0) == ("30.0", "km/h")
    assert values.text(w(channel="speed", decimals=0, show_unit=False), 0) == ("30", "")
    assert values.text(w(channel="power"), 0)[1] == "W"


def test_imperial(telemetry):
    v = FrameValues(telemetry, np.array([150.0]), units="imperial")
    num, unit = v.text(w(channel="speed"), 0)
    assert unit == "mph" and float(num) == pytest.approx(18.6, abs=0.1)
    assert v.range(w(type="arc", channel="speed")) == (0, 45)


def test_clock_and_durations(values):
    assert values.text(w(channel="time_of_day"), 0) == ("10:04", "")
    assert values.text(w(channel="time_of_day", decimals=1), 0) == ("10:04:50", "")
    assert values.text(w(channel="elapsed_time"), 0) == ("0:04:50", "")
    assert values.is_available("time_of_day")


def test_missing_value_shows_dashes(telemetry):
    v = FrameValues(telemetry, np.array([160.0]))  # langer HF-Dropout
    assert v.text(w(channel="heart_rate"), 0) == ("--", "bpm")
    assert math.isnan(v.fraction(w(type="arc", channel="heart_rate"), 0))


def test_zones(values):
    cfg = w(channel="speed", zones=[Zone(0, "#00FF00"), Zone(25, "#FF0000")], zones_color_text=True)
    assert values.fill_color(cfg, 0) == "#FF0000"  # 30 km/h
    assert values.text_color(cfg, 0) == "#FF0000"
    assert values.zone_color(cfg, 10) == "#00FF00"
    plain = w(channel="speed", zones=[Zone(25, "#FF0000")])
    assert values.text_color(plain, 0) == plain.style.text_color


def test_default_ranges(values):
    assert values.range(w(channel="power")) == (0, 600)
    lo, hi = values.range(w(channel="elevation"))  # aus Track-Statistik
    assert lo == pytest.approx(150, abs=2) and hi == pytest.approx(375, abs=2)
    assert values.range(w(channel="power", min_value=100, max_value=400)) == (100, 400)


def test_label(values):
    assert values.language == "de"
    assert values.label(w(channel="heart_rate")) == "PULS"
    assert values.label(w(channel="time_of_day")) == "UHRZEIT"
    values.language = "en"
    assert values.label(w(channel="heart_rate")) == "HR"
    assert values.label(w(channel="heart_rate", label="Puls")) == "Puls"  # eigene Beschriftung bleibt
    assert values.label(w(channel="time_of_day")) == "TIME"


def test_format_number():
    assert format_number(-0.04, 1) == "0.0"
    assert format_number(-0.4, 0) == "0"
    assert format_number(float("nan"), 1) == "--"
    assert format_number(12.345, 2) == "12.35"


def _ramp_telemetry():
    from datetime import datetime, timezone

    from cycle_overlay.core.models import SignalStats, Telemetry

    t = np.arange(0, 30.0)
    kmh = np.where(t <= 10, 20.0, np.where(t >= 11, 30.0, 20.0 + (t - 10) * 10))
    v = kmh / 3.6
    chans = {"speed": v, "distance": np.cumsum(v), "lat": np.full(t.size, 48.0), "lon": np.full(t.size, 9.0),
             "elapsed_time": t.copy()}
    st = {k: SignalStats(float(np.min(a)), float(np.max(a)), float(np.mean(a)), 1.0) for k, a in chans.items()}
    return Telemetry(datetime(2026, 4, 26, tzinfo=timezone.utc), t, chans, {k: True for k in chans}, st,
                     np.zeros(t.size, bool), "gps", 0.5)


def test_numbers_count_through_instead_of_jumping():
    """10 km/h mehr in 1 s bei 1 Hz Aktualisierung: jede Zahl dazwischen wird gezeigt."""
    tel = _ramp_telemetry()
    times = frame_times(9.0, 90, 30)  # 9 s bis 12 s
    cfg = w(channel="speed", update_rate_hz=1, decimals=0)
    counted = [FrameValues(tel, times, t0=0.0).text(cfg, i)[0] for i in range(90)]
    jumped = [FrameValues(tel, times, t0=0.0, count_numbers=False).text(cfg, i)[0] for i in range(90)]
    seen = [int(x) for x in dict.fromkeys(counted)]
    assert seen == list(range(20, 31))  # 20, 21, …, 30 – keine Lücke, nie rückwärts
    assert [int(x) for x in dict.fromkeys(jumped)] == [20, 30]
    # Uhrzeit und Dauer zählen nicht künstlich
    assert FrameValues(tel, times, t0=0.0).counted("elapsed_time", 5, 1.0) == \
        FrameValues(tel, times, t0=0.0).raw("elapsed_time", 5, 1.0)
