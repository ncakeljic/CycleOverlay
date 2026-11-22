from __future__ import annotations

import io

import numpy as np
import pytest

from cycle_overlay.core.fit_parser import FITParseError, parse_fit
from cycle_overlay.core.processor import process
from cycle_overlay.core.track_loader import is_fit, load_track

from .fit_writer import points_from_raw, write_fit


@pytest.fixture()
def fit_file(tmp_path, raw_track):
    path = tmp_path / "ride.FIT"
    write_fit(path, points_from_raw(raw_track))
    return path


def test_fit_matches_gpx(fit_file, raw_track):
    fit = parse_fit(fit_file)
    assert fit.start_time == raw_track.start_time
    assert np.array_equal(fit.t, raw_track.t)
    assert np.allclose(fit.lat, raw_track.lat, atol=1e-6)
    assert np.allclose(fit.ele, raw_track.ele, atol=0.2)
    assert np.array_equal(np.isnan(fit.sensors["heart_rate"]), np.isnan(raw_track.sensors["heart_rate"]))
    assert np.nanmax(fit.sensors["power"]) == np.nanmax(raw_track.sensors["power"])
    assert np.isfinite(fit.sensors["distance_device"]).all()
    assert fit.has_position


def test_fit_processing_uses_device_distance(fit_file):
    tel = process(parse_fit(fit_file))
    assert tel.channels["distance"][-1] == pytest.approx(9000, abs=20)
    assert tel.channels["elevation_gain"][-1] == pytest.approx(75, abs=5)
    assert tel.available["position"]


@pytest.mark.parametrize("opts", [dict(big_endian=True), dict(compressed=True), dict(dev_field=True),
                                  dict(compressed=True, big_endian=True, dev_field=True)])
def test_fit_variants(tmp_path, raw_track, opts):
    path = tmp_path / "v.fit"
    write_fit(path, points_from_raw(raw_track), **opts)
    fit = parse_fit(path)
    assert fit.n_points == raw_track.n_points
    assert fit.duration == raw_track.duration
    assert np.allclose(fit.lat, raw_track.lat, atol=1e-6)


def test_device_speed_preferred(tmp_path, raw_track):
    path = tmp_path / "s.fit"
    write_fit(path, points_from_raw(raw_track, device_speed=7.0))
    tel = process(parse_fit(path))
    assert tel.speed_source == "device"
    assert tel.value_at(150)["speed"] == pytest.approx(7.0)


def test_indoor_fit_without_position(tmp_path):
    pts = [{"unix_ts": 1_800_000_000 + i, "hr": 120, "cad": 90, "power": 200, "speed": 8.0, "dist": 8.0 * i}
           for i in range(120)]
    path = tmp_path / "indoor.fit"
    write_fit(path, pts)
    raw = parse_fit(path)
    assert not raw.has_position
    tel = process(raw)
    assert not tel.available["position"]
    assert tel.channels["distance"][-1] == pytest.approx(8.0 * 119, abs=1)
    assert tel.value_at(60)["speed"] == pytest.approx(8.0)
    assert any("Indoor" in w for w in tel.warnings)


def test_track_loader_detects_format(fit_file, tmp_path):
    from .conftest import SAMPLE_GPX

    assert is_fit(fit_file) and not is_fit(SAMPLE_GPX)
    disguised = tmp_path / "fahrt.gpx"
    disguised.write_bytes(fit_file.read_bytes())  # falsche Endung
    assert load_track(disguised).creator == "FIT"
    assert load_track(SAMPLE_GPX).creator == "CycleOverlay Testdaten"


def test_invalid_fit():
    with pytest.raises(FITParseError):
        parse_fit(io.BytesIO(b"\x0e\x20\x00\x00\x10\x00\x00\x00.FIT\x00\x00\x45"))
    with pytest.raises(FITParseError):
        parse_fit(io.BytesIO(b"kein fit"))


def test_cli_info_with_fit(fit_file, capsys):
    from cycle_overlay.cli import main

    assert main(["info", str(fit_file)]) == 0
    out = capsys.readouterr().out
    assert "9.00 km" in out or "8.99 km" in out or "9.01 km" in out
