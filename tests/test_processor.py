from __future__ import annotations

import io
from fractions import Fraction

import numpy as np
import pytest

from cycle_overlay.core.gpx_parser import parse_gpx
from cycle_overlay.core.processor import (
    ProcessingConfig,
    elevation_gain,
    frame_times,
    haversine,
    interp_sparse,
    process,
    smooth,
)


KMH = 1 / 3.6


def at(tel, t):
    return tel.value_at(t)


# ------------------------------------------------------------------ Gesamtwerte der Testfahrt


def test_total_distance(telemetry):
    assert telemetry.channels["distance"][-1] == pytest.approx(9000, abs=15)


def test_total_elevation_gain(telemetry):
    assert telemetry.channels["elevation_gain"][-1] == pytest.approx(75, abs=5)


def test_duration_and_pause_count(telemetry):
    assert telemetry.duration == 1200
    assert telemetry.n_pauses == 1
    assert telemetry.pause_mask[690]
    assert not telemetry.pause_mask[650]


def test_moving_time(telemetry):
    # 1200 s minus 60 s Stillstand minus 60 s Pause, kleine Übergänge durch Glättung
    assert telemetry.channels["moving_time"][-1] == pytest.approx(1080, abs=5)


def test_signals_detected(telemetry):
    for ch in ("speed", "elevation", "gradient", "power", "heart_rate", "cadence", "temperature"):
        assert telemetry.available[ch], ch
    assert telemetry.speed_source == "gps"
    assert telemetry.stats["heart_rate"].coverage < 1.0  # langer Dropout


# ------------------------------------------------------------------ Geschwindigkeit


@pytest.mark.parametrize("t,kmh", [(150, 30), (450, 18), (870, 45), (1100, 25)])
def test_speed_per_phase(telemetry, t, kmh):
    assert at(telemetry, t)["speed"] == pytest.approx(kmh * KMH, rel=0.01)


def test_speed_constant_during_smart_recording(telemetry):
    sp = telemetry.channels["speed"][360:540]
    assert np.allclose(sp, 18 * KMH, rtol=0.01)


def test_speed_zero_at_standstill_despite_gps_drift(telemetry):
    assert np.all(telemetry.channels["speed"][605:660] == 0)


def test_standstill_drift_not_counted_as_distance(telemetry):
    d = telemetry.channels["distance"]
    assert d[659] - d[605] == pytest.approx(0, abs=2)


def test_pause_mode_zero_and_hold(raw_track):
    zero = process(raw_track, ProcessingConfig(pause_mode="zero"))
    assert zero.value_at(690)["speed"] == 0
    assert zero.value_at(690)["power"] == 0
    hold = process(raw_track, ProcessingConfig(pause_mode="hold"))
    # vor der Pause stand der Fahrer -> gehaltener Wert ist ebenfalls ~0; Leistung 0
    assert hold.value_at(690)["power"] == pytest.approx(hold.value_at(659)["power"])
    # HF wird in der Pause immer gehalten
    assert zero.value_at(690)["heart_rate"] == pytest.approx(zero.value_at(660)["heart_rate"], abs=1)


def test_device_speed_preferred():
    pts = []
    for i in range(20):
        lat = 48 + i * 1e-4  # ~11 m/s laut GPS
        pts.append(
            f'<trkpt lat="{lat}" lon="11"><time>2026-01-01T10:00:{i:02d}Z</time>'
            f"<extensions><speed>5.0</speed></extensions></trkpt>"
        )
    doc = ('<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
           + "".join(pts) + "</trkseg></trk></gpx>")
    raw = parse_gpx(io.BytesIO(doc.encode()))
    tel = process(raw)
    assert tel.speed_source == "device"
    assert tel.value_at(10)["speed"] == pytest.approx(5.0)
    gps = process(raw, ProcessingConfig(prefer_device_speed=False))
    assert gps.speed_source == "gps"
    assert gps.value_at(10)["speed"] == pytest.approx(11.1, rel=0.02)
    assert not tel.available["elevation"]  # keine Höhe in dieser Datei


# ------------------------------------------------------------------ Höhe & Steigung


@pytest.mark.parametrize("t,pct", [(450, 5.0), (870, -6.0), (150, 0.0), (1100, 0.0)])
def test_gradient_per_phase(telemetry, t, pct):
    assert at(telemetry, t)["gradient"] == pytest.approx(pct, abs=1.0)


def test_gradient_has_no_extremes_at_standstill(telemetry):
    g = telemetry.channels["gradient"][600:720]
    assert np.all(np.abs(g) < 8)
    assert np.ptp(g[10:115]) < 0.5  # im Stand stabil


def test_gradient_never_extreme(telemetry):
    assert np.nanmax(np.abs(telemetry.channels["gradient"])) < 10


def test_elevation_values(telemetry):
    assert at(telemetry, 100)["elevation"] == pytest.approx(300, abs=1)
    assert at(telemetry, 650)["elevation"] == pytest.approx(375, abs=1)
    assert at(telemetry, 1150)["elevation"] == pytest.approx(150, abs=1)


def test_vam_on_climb(telemetry):
    # 5 m/s * 5 % = 0,25 m/s = 900 m/h
    assert at(telemetry, 450)["vam"] == pytest.approx(900, rel=0.08)


def test_gain_hysteresis_ignores_noise():
    rng = np.random.default_rng(0)
    flat = 100 + rng.normal(0, 0.8, 2000)
    # wie in der Pipeline: erst glätten (10 s), dann Hysterese
    assert elevation_gain(smooth(flat, 10), 3.0)[-1] < 5
    assert elevation_gain(flat, 3.0)[-1] > 50  # ohne Glättung würde Rauschen gezählt
    climb = np.linspace(100, 200, 1000)
    assert elevation_gain(climb, 3.0)[-1] == pytest.approx(100, abs=0.01)


def test_gain_counts_continuously_while_climbing():
    climb = np.linspace(0, 10, 101)
    g = elevation_gain(climb, 3.0)
    assert g[50] == pytest.approx(5, abs=0.01)  # keine 3-m-Stufen


# ------------------------------------------------------------------ Sensoren


def test_short_hr_dropout_interpolated(telemetry):
    assert not np.isnan(at(telemetry, 102)["heart_rate"])


def test_long_hr_dropout_shown_as_missing(telemetry):
    assert np.isnan(at(telemetry, 160)["heart_rate"])


def test_power_per_phase(telemetry):
    assert at(telemetry, 150)["power"] == pytest.approx(200, abs=15)
    assert at(telemetry, 450)["power"] == pytest.approx(300, abs=15)
    assert at(telemetry, 870)["power"] == 0


# ------------------------------------------------------------------ Hilfsfunktionen


def test_haversine_one_degree_latitude():
    assert haversine(0, 0, 1, 0) == pytest.approx(111_195, rel=1e-3)


def test_centered_smoothing_has_no_lag():
    step = np.r_[np.zeros(50), np.ones(50)]
    s = smooth(step, 5, "centered")
    assert s[49] < 0.5 < s[50]  # Sprung bleibt an derselben Stelle
    tr = smooth(step, 5, "trailing")
    assert tr[50] < 0.5  # nachlaufend verzögert


def test_smoothing_keeps_nan():
    v = np.r_[1.0, 2.0, np.nan, 4.0, 5.0]
    s = smooth(v, 3)
    assert np.isnan(s[2]) and not np.isnan(s[1])


def test_interp_sparse_respects_max_gap():
    t = np.array([0.0, 1.0, 2.0, 20.0, 21.0])
    v = np.array([1.0, 1.0, 1.0, 3.0, 3.0])
    out = interp_sparse(t, v, np.arange(22.0), max_gap_s=10)
    assert out[1] == 1 and np.isnan(out[10]) and out[20] == 3


def test_frame_times_exact_for_ntsc_rates():
    n = 30000 * 60 * 60 // 1001  # ca. 1 h bei 29,97 FPS
    ft = frame_times(0.0, n, Fraction(30000, 1001))
    assert ft[-1] == pytest.approx((n - 1) * 1001 / 30000, abs=1e-9)
    assert np.allclose(np.diff(ft), 1001 / 30000)
    assert frame_times(10.0, 3, 25)[2] == pytest.approx(10.08)


def test_sample_outside_track_is_nan(telemetry):
    s = telemetry.sample([-5, 5000])
    assert np.isnan(s["speed"]).all()


def test_sample_frames_smooth(telemetry):
    ft = frame_times(400.0, 120, Fraction(60))
    sp = telemetry.sample(ft)["speed"]
    assert np.max(np.abs(np.diff(sp))) < 0.05  # keine Sprünge zwischen Frames


def test_large_track_performance():
    import time

    n = 30_000  # gut 8 h bei 1 Hz
    pts = "".join(
        f'<trkpt lat="{48 + i * 5e-5:.7f}" lon="11"><ele>{100 + (i % 600) * 0.1:.1f}</ele>'
        f"<time>2026-01-01T{i // 3600:02d}:{(i % 3600) // 60:02d}:{i % 60:02d}Z</time>"
        f"<extensions><power>{200 + i % 50}</power></extensions></trkpt>"
        for i in range(n)
    )
    doc = '<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>' + pts + "</trkseg></trk></gpx>"
    t0 = time.perf_counter()
    tel = process(parse_gpx(io.BytesIO(doc.encode())))
    assert tel.duration == n - 1
    assert time.perf_counter() - t0 < 10


@pytest.mark.parametrize(
    "value,expected",
    [
        ("29.97", Fraction(30000, 1001)), (29.97, Fraction(30000, 1001)), ("23.976", Fraction(24000, 1001)),
        ("59.94", Fraction(60000, 1001)), ("30", Fraction(30)), (25, Fraction(25)), ("60000/1001", Fraction(60000, 1001)),
        ("12.5", Fraction(25, 2)), (Fraction(50), Fraction(50)),
    ],
)
def test_parse_fps(value, expected):
    from cycle_overlay.core.processor import parse_fps

    assert parse_fps(value) == expected


# ------------------------------------------------------------------ GPS-Ausreißer


def _straight_track(speeds_ms):
    """GPX mit 1-Hz-Punkten nach Norden, Geschwindigkeit je Segment vorgegeben."""
    import math

    lat = 48.0
    pts = []
    for i in range(len(speeds_ms) + 1):
        pts.append(f'<trkpt lat="{lat:.9f}" lon="11"><ele>100</ele><time>2026-01-01T10:{i // 60:02d}:{i % 60:02d}Z</time></trkpt>')
        if i < len(speeds_ms):
            lat += math.degrees(speeds_ms[i] / 6_371_000.0)
    doc = '<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>' + "".join(pts) + "</trkseg></trk></gpx>"
    return parse_gpx(io.BytesIO(doc.encode()))


def test_gps_spike_is_removed():
    sp = [5.0] * 60
    sp[30] = 36.0  # 130 km/h für eine Sekunde
    sp[32] = 25.0
    tel = process(_straight_track(sp))
    assert np.nanmax(tel.channels["speed"]) < 6.0
    assert tel.channels["distance"][-1] == pytest.approx(300, abs=3)
    assert any("Ausreißer" in w for w in tel.warnings)


def test_real_acceleration_and_braking_are_kept():
    sprint = list(np.linspace(5, 15, 10)) + [15.0] * 10 + [15.0, 9.0, 4.0, 2.0] + [2.0] * 10  # Vollbremsung
    tel = process(_straight_track(sprint))
    assert not any("Ausreißer" in w for w in tel.warnings)
    assert np.nanmax(tel.channels["speed"]) == pytest.approx(15.0, abs=0.2)


def test_outlier_filter_can_be_disabled():
    sp = [5.0] * 40
    sp[20] = 36.0
    tel = process(_straight_track(sp), ProcessingConfig(outlier_filter=False))
    assert np.nanmax(tel.channels["speed"]) > 10
