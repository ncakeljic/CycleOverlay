from __future__ import annotations

import io
import math
from datetime import datetime, timezone

import numpy as np
import pytest

from cycle_overlay.core.gpx_parser import GPXParseError, parse_gpx, parse_time

from .conftest import SAMPLE_GPX


def gpx(points: str, extra_ns: str = "") -> io.BytesIO:
    doc = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<gpx version="1.1" creator="Test" xmlns="http://www.topografix.com/GPX/1/1" {extra_ns}>'
        f"<trk><name>T</name><trkseg>{points}</trkseg></trk></gpx>"
    )
    return io.BytesIO(doc.encode("utf-8"))


def pt(t: str, ext: str = "", ele: str | None = "100", lat="48.0", lon="11.0") -> str:
    e = f"<ele>{ele}</ele>" if ele is not None else ""
    x = f"<extensions>{ext}</extensions>" if ext else ""
    return f'<trkpt lat="{lat}" lon="{lon}">{e}<time>{t}</time>{x}</trkpt>'


def test_sample_ride_basics(raw_track):
    assert raw_track.n_points == 1021
    assert raw_track.start_time == datetime(2026, 6, 14, 8, 0, tzinfo=timezone.utc)
    assert raw_track.duration == 1200
    assert raw_track.name == "Testfahrt"  # Name aus <metadata> hat Vorrang
    assert raw_track.creator == "CycleOverlay Testdaten"
    assert np.all(np.diff(raw_track.t) > 0)


def test_sample_ride_extensions_with_ns3_prefix(raw_track):
    s = raw_track.sensors
    assert np.nanmax(s["power"]) > 250  # <power> direkt unter <extensions>
    assert np.nanmean(s["cadence"]) > 0
    assert 18.0 <= np.nanmin(s["temperature"]) <= 20.0
    # HR-Dropouts im Rohsignal
    idx = np.searchsorted(raw_track.t, [102, 160])
    assert np.isnan(s["heart_rate"][idx]).all()
    assert np.isnan(s["speed_device"]).all()


@pytest.mark.parametrize(
    "ext,channel,value",
    [
        ('<gpxtpx:TrackPointExtension><gpxtpx:hr>150</gpxtpx:hr></gpxtpx:TrackPointExtension>', "heart_rate", 150),
        ('<ns3:TrackPointExtension><ns3:cad>92</ns3:cad></ns3:TrackPointExtension>', "cadence", 92),
        ("<power>250</power>", "power", 250),
        ("<watts>260</watts>", "power", 260),
        ('<gpxpx:PowerInWatts>270</gpxpx:PowerInWatts>', "power", 270),
        ('<gpxtpx:TrackPointExtension><gpxtpx:atemp>21.5</gpxtpx:atemp></gpxtpx:TrackPointExtension>', "temperature", 21.5),
        ('<gpxtpx:TrackPointExtension><gpxtpx:speed>8.5</gpxtpx:speed></gpxtpx:TrackPointExtension>', "speed_device", 8.5),
    ],
)
def test_extension_variants(ext, channel, value):
    ns = ('xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1" '
          'xmlns:ns3="http://www.garmin.com/xmlschemas/TrackPointExtension/v1" '
          'xmlns:gpxpx="http://www.garmin.com/xmlschemas/PowerExtension/v1"')
    tr = parse_gpx(gpx(pt("2026-01-01T10:00:00Z", ext) + pt("2026-01-01T10:00:01Z", ext), ns))
    assert tr.sensors[channel][0] == pytest.approx(value)


def test_missing_elevation_and_sensors():
    tr = parse_gpx(gpx(pt("2026-01-01T10:00:00Z", ele=None) + pt("2026-01-01T10:00:01Z", ele=None)))
    assert np.isnan(tr.ele).all()
    assert all(np.isnan(v).all() for v in tr.sensors.values())


def test_implausible_sensor_values_are_dropped():
    ext0 = "<power>5000</power><hr>0</hr>"
    tr = parse_gpx(gpx(pt("2026-01-01T10:00:00Z", ext0) + pt("2026-01-01T10:00:01Z", "<power>200</power>")))
    assert math.isnan(tr.sensors["power"][0])
    assert math.isnan(tr.sensors["heart_rate"][0])
    assert tr.sensors["power"][1] == 200


def test_duplicate_and_backwards_timestamps_removed():
    pts = "".join(pt(t) for t in [
        "2026-01-01T10:00:00Z", "2026-01-01T10:00:01Z", "2026-01-01T10:00:01Z",
        "2026-01-01T09:59:59Z", "2026-01-01T10:00:02Z",
    ])
    tr = parse_gpx(gpx(pts))
    assert list(tr.t) == [0, 1, 2]
    assert any("rückwärts" in w for w in tr.warnings)


def test_points_without_time_skipped():
    pts = pt("2026-01-01T10:00:00Z") + '<trkpt lat="48" lon="11"><ele>1</ele></trkpt>' + pt("2026-01-01T10:00:02Z")
    tr = parse_gpx(gpx(pts))
    assert tr.n_points == 2
    assert any("ohne Zeitstempel" in w for w in tr.warnings)


def test_invalid_files():
    with pytest.raises(GPXParseError):
        parse_gpx(io.BytesIO(b"<gpx><trk><trkseg><trkpt"))
    with pytest.raises(GPXParseError):
        parse_gpx(io.BytesIO(b"<html></html>"))
    with pytest.raises(GPXParseError):
        parse_gpx(gpx(pt("2026-01-01T10:00:00Z")))
    with pytest.raises(GPXParseError):
        parse_gpx("/gibt/es/nicht.gpx")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2026-06-14T08:00:00Z", datetime(2026, 6, 14, 8, 0, tzinfo=timezone.utc)),
        ("2026-06-14T08:00:00.123Z", datetime(2026, 6, 14, 8, 0, 0, 123000, tzinfo=timezone.utc)),
        ("2026-06-14T08:00:00.123456789Z", datetime(2026, 6, 14, 8, 0, 0, 123456, tzinfo=timezone.utc)),
        ("2026-06-14T10:00:00+02:00", datetime(2026, 6, 14, 8, 0, tzinfo=timezone.utc)),
        ("2026-06-14T10:00:00+0200", datetime(2026, 6, 14, 8, 0, tzinfo=timezone.utc)),
        ("2026-06-14T08:00:00", datetime(2026, 6, 14, 8, 0, tzinfo=timezone.utc)),  # ohne Zone = UTC
    ],
)
def test_parse_time(text, expected):
    assert parse_time(text) == expected


def test_parse_from_path_string():
    tr = parse_gpx(str(SAMPLE_GPX))
    assert tr.source_path == SAMPLE_GPX
