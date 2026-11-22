from __future__ import annotations

import json
import urllib.error

import numpy as np
import pytest

from cycle_overlay.core.osm import (
    CLASSES,
    MapData,
    MapDataError,
    MapIndex,
    build_query,
    classify,
    fetch_map_data,
    parse_overpass,
    simplify_track,
)


def way(tags, coords, wid=1):
    return {"type": "way", "id": wid, "tags": tags, "geometry": [{"lat": a, "lon": b} for a, b in coords]}


SAMPLE = {
    "elements": [
        way({"highway": "residential"}, [(48.0, 11.0), (48.001, 11.0), (48.002, 11.001)], 1),
        way({"highway": "primary"}, [(48.0, 11.002), (48.003, 11.002)], 2),
        way({"highway": "cycleway"}, [(48.0, 11.003), (48.001, 11.003)], 3),
        way({"highway": "track"}, [(48.01, 11.0), (48.011, 11.0)], 4),
        way({"natural": "water"}, [(48.0, 11.0), (48.0, 11.001), (48.001, 11.001), (48.0, 11.0)], 5),
        way({"natural": "wood"}, [(48.0, 11.0), (48.0, 11.001), (48.001, 11.0)], 6),  # nicht geschlossen -> weg
        way({"waterway": "river"}, [(48.0, 11.01), (48.01, 11.01)], 7),
        way({"building": "yes"}, [(48.0, 11.0), (48.0, 11.0001)], 8),  # ignoriert
        {"type": "node", "id": 9, "lat": 48.0, "lon": 11.0},
        way({"highway": "residential"}, [(48.0, 11.0)], 10),  # zu kurz
    ]
}


def test_classify():
    assert classify({"highway": "motorway"}) == "major"
    assert classify({"highway": "secondary"}) == "primary"
    assert classify({"highway": "residential"}) == "minor"
    assert classify({"highway": "cycleway"}) == "cycle"
    assert classify({"highway": "footway"}) == "path"
    assert classify({"highway": "proposed"}) is None
    assert classify({"landuse": "forest"}) == "green"
    assert classify({"building": "yes"}) is None


def test_parse_overpass():
    md = parse_overpass(SAMPLE)
    assert md.counts() == {"minor": 1, "primary": 1, "cycle": 1, "path": 1, "water": 1, "waterline": 1}
    order = [CLASSES.index(f.cls) for f in md.features]
    assert order == sorted(order)  # Zeichenreihenfolge: Flächen unten, große Straßen oben


def test_mapdata_json_roundtrip():
    md = parse_overpass(SAMPLE)
    md.buffer_m = 1500
    again = MapData.from_json_bytes(md.to_json_bytes())
    assert len(again) == len(md) and again.buffer_m == 1500
    for a, b in zip(md.features, again.features):
        assert a.cls == b.cls and np.allclose(a.lat, b.lat, atol=1e-6) and np.allclose(a.lon, b.lon, atol=1e-6)
    with pytest.raises(MapDataError):
        MapData.from_json_bytes(b"{}")
    with pytest.raises(MapDataError):
        MapData.from_json_bytes(b"nix")


def test_simplify_and_query(raw_track):
    pts = simplify_track(raw_track.lat, raw_track.lon)
    assert 20 <= len(pts) <= 150
    assert pts[0] == pytest.approx((raw_track.lat[0], raw_track.lon[0]))
    q = build_query(pts, 1200)
    assert "around:1200," in q and '"highway"' in q and "out geom" in q
    with pytest.raises(MapDataError):
        build_query([])


def test_fetch_uses_cache_and_fallback(tmp_path, raw_track):
    calls = []

    def fake_post(url, query, timeout):
        calls.append(url)
        if "overpass-api.de" in url:
            raise urllib.error.URLError("Server überlastet")
        return json.dumps(SAMPLE).encode()

    md = fetch_map_data(raw_track.lat, raw_track.lon, cache_dir=tmp_path, post=fake_post, sleep=lambda s: None,
                        min_coverage=0)
    assert len(md) == 6 and len(calls) == 2  # erster Server fällt aus, zweiter antwortet
    md2 = fetch_map_data(raw_track.lat, raw_track.lon, cache_dir=tmp_path, post=fake_post, sleep=lambda s: None,
                         min_coverage=0)
    assert len(md2) == 6 and len(calls) == 2  # aus dem Zwischenspeicher


def test_fetch_offline_raises_clear_error(raw_track):
    def offline(url, query, timeout):
        raise urllib.error.URLError("keine Verbindung")

    with pytest.raises(MapDataError, match="Internetverbindung"):
        fetch_map_data(raw_track.lat, raw_track.lon, post=offline)


def test_fetch_server_remark(raw_track):
    def busy(url, query, timeout):
        return json.dumps({"remark": "runtime error: timeout"}).encode()

    with pytest.raises(MapDataError):
        fetch_map_data(raw_track.lat, raw_track.lon, post=busy)


def test_map_index_query():
    md = parse_overpass(SAMPLE)
    idx = MapIndex(md, 48.0, 11.0, cell_m=200)
    near = idx.query((-50, -50, 50, 50))
    names = {idx.features[i].cls for i in near}
    assert "minor" in names and "water" in names
    assert "path" not in names  # liegt ~1,1 km nördlich
    far = idx.query((-100, 1000, 100, 1300))
    assert {idx.features[i].cls for i in far} == {"path"}
    assert near == sorted(near)


def test_transient_errors_are_retried():
    from cycle_overlay.core.osm import TransientError

    calls = []

    def flaky(url, query, timeout):
        calls.append(url)
        if len(calls) < 3:
            raise TransientError("HTTP 429")
        return json.dumps(SAMPLE).encode()

    md = fetch_map_data(np.array([48.0, 48.01]), np.array([11.0, 11.0]), post=flaky, sleep=lambda s: None)
    assert len(md) == 6 and len(calls) == 3 and len(set(calls)) == 1  # gleicher Server, 3. Versuch klappt


def test_long_track_is_split_and_merged():
    from cycle_overlay.core.osm import split_chunks

    lat = 48.0 + np.arange(0, 0.6, 0.0005)  # ~67 km nach Norden
    lon = np.full(lat.size, 11.0)
    queries = []

    def post(url, query, timeout):
        queries.append(query)
        return json.dumps(SAMPLE).encode()  # jede Antwort enthält dieselben Wege (IDs)

    md = fetch_map_data(lat, lon, post=post, sleep=lambda s: None, chunked=True, min_coverage=0)
    assert len(queries) >= 5
    assert len(md) == 6  # doppelte Wege über die Abschnitte hinweg zusammengeführt
    chunks = split_chunks([(float(i), 0.0) for i in range(100)], 40)
    assert chunks[0][-1] == chunks[1][0]  # Abschnitte überlappen
    assert sum(len(c) for c in chunks) - (len(chunks) - 1) == 100


def test_error_details_are_collected():
    def html_error(url, query, timeout):
        return b'<html><body><p><strong style="color:#FF0000">Error</strong>: runtime error: out of memory</p></body></html>'

    with pytest.raises(MapDataError) as exc:
        fetch_map_data(np.array([48.0, 48.01]), np.array([11.0, 11.0]), post=html_error, sleep=lambda s: None)
    msg = str(exc.value)
    assert "out of memory" in msg and "overpass-api.de" in msg and "private.coffee" in msg


def test_ssl_context():
    import ssl

    from cycle_overlay.core.osm import ssl_context

    ctx, name = ssl_context()
    assert isinstance(ctx, ssl.SSLContext) and name


def test_silent_server_is_not_retried_and_next_server_used():
    from cycle_overlay.core.osm import ServerTimeout

    calls = []

    def post(url, query, timeout):
        calls.append(url)
        if "overpass-api.de" in url:
            raise ServerTimeout("keine Antwort")
        return json.dumps(SAMPLE).encode()

    lat = 48.0 + np.arange(0, 0.6, 0.0005)
    md = fetch_map_data(lat, np.full(lat.size, 11.0), post=post, sleep=lambda s: None, chunked=True, min_coverage=0)
    assert len(md) == 6
    # der stumme Server wird nur ein einziges Mal versucht, nicht pro Abschnitt erneut
    assert sum("overpass-api.de" in c for c in calls) == 1


def test_total_timeout_and_cancel():
    import threading

    from cycle_overlay.core.osm import DownloadCancelled, TransientError

    def busy(url, query, timeout):
        raise TransientError("HTTP 429")

    with pytest.raises(MapDataError, match="Zeitlimit"):
        fetch_map_data(np.array([48.0, 48.01]), np.array([11.0, 11.0]), post=busy, sleep=lambda s: None,
                       total_timeout=-1)
    ev = threading.Event()
    ev.set()
    with pytest.raises(DownloadCancelled):
        fetch_map_data(np.array([48.0, 48.01]), np.array([11.0, 11.0]), post=busy, cancel=ev)


def test_network_check_runs_quickly():
    import time

    from cycle_overlay.core.osm import network_check

    t0 = time.monotonic()
    lines = network_check(timeout=3)
    assert len(lines) >= 8 and all(isinstance(x, str) for x in lines)
    assert time.monotonic() - t0 < 15


def _road_along(lat, lon, wid):
    return {"type": "way", "id": wid, "tags": {"highway": "residential"},
            "geometry": [{"lat": float(a), "lon": float(b)} for a, b in zip(lat, lon)]}


def test_incomplete_answer_is_retried_and_gaps_reloaded():
    """Erste Antworten unvollständig (Overpass-'remark') -> nicht übernehmen; Lücken nachladen."""
    lat = 48.0 + np.arange(0, 0.45, 0.0005)  # ~50 km nach Norden, mehrere Abschnitte
    lon = np.full(lat.size, 11.0)
    calls = {"n": 0}

    def post(url, query, timeout):
        calls["n"] += 1
        # Abschnitt anhand der ersten Koordinate in der Abfrage bestimmen und dort eine Straße liefern
        first = float(query.split("around:")[1].split(",")[1])
        sel = (lat >= first - 0.001) & (lat <= first + 0.11)
        if calls["n"] == 1:
            return json.dumps({"elements": [], "remark": "runtime error: Query timed out"}).encode()
        return json.dumps({"elements": [_road_along(lat[sel], lon[sel] + 0.001, int(first * 1e5))]}).encode()

    md = fetch_map_data(lat, lon, post=post, sleep=lambda s: None, chunked=True)
    assert md.coverage is not None and md.coverage > 0.97


def test_coverage_detects_missing_part():
    from cycle_overlay.core.osm import Feature, route_coverage

    lat = 48.0 + np.arange(0, 0.2, 0.0005)
    lon = np.full(lat.size, 11.0)
    half = Feature("minor", lat[: lat.size // 2], lon[: lat.size // 2] + 0.001, 1)
    cov, missing = route_coverage([half], lat, lon)
    assert 0.4 < cov < 0.6 and missing[-1] and not missing[0]


def test_gap_triggers_reload_of_that_chunk():
    lat = 48.0 + np.arange(0, 0.45, 0.0005)
    lon = np.full(lat.size, 11.0)
    seen = []

    def post(url, query, timeout):
        first = float(query.split("around:")[1].split(",")[1])
        seen.append(round(first, 3))
        sel = (lat >= first - 0.001) & (lat <= first + 0.11)
        # beim ersten Durchlauf liefert der Server für Abschnitte ab 48.2 nichts (kein Fehler!)
        if first >= 48.2 and seen.count(round(first, 3)) == 1:
            return json.dumps({"elements": []}).encode()
        return json.dumps({"elements": [_road_along(lat[sel], lon[sel] + 0.001, int(first * 1e5))]}).encode()

    md = fetch_map_data(lat, lon, post=post, sleep=lambda s: None, chunked=True)
    assert md.coverage > 0.97
    assert len(seen) > len(set(seen))  # mindestens ein Abschnitt wurde nachgeladen


def test_whole_route_in_one_query_by_default():
    lat = 48.0 + np.arange(0, 0.45, 0.0005)  # ~50 km
    lon = np.full(lat.size, 11.0)
    queries = []

    def post(url, query, timeout):
        queries.append(query)
        return json.dumps({"elements": [_road_along(lat, lon + 0.001, 1)]}).encode()

    md = fetch_map_data(lat, lon, post=post, sleep=lambda s: None)
    assert len(queries) == 1  # am Stück
    assert "[timeout:600]" in queries[0]
    assert md.coverage > 0.97


def test_gaps_in_whole_route_fall_back_to_sections():
    lat = 48.0 + np.arange(0, 0.45, 0.0005)
    lon = np.full(lat.size, 11.0)
    answers = [_road_along(lat[:200], lon[:200] + 0.001, 1), _road_along(lat, lon + 0.001, 1)]
    calls = []

    def post(url, query, timeout):
        calls.append(1)
        return json.dumps({"elements": [answers[min(len(calls) - 1, 1)]]}).encode()

    md = fetch_map_data(lat, lon, post=post, sleep=lambda s: None)
    assert len(calls) >= 2 and md.coverage > 0.97 and len(md) == 1


def test_empty_whole_route_falls_back_to_sections():
    lat = 48.0 + np.arange(0, 0.45, 0.0005)
    lon = np.full(lat.size, 11.0)
    road = _road_along(lat, lon + 0.001, 1)
    calls = []

    def post(url, query, timeout):
        calls.append(query)
        if len(calls) <= 3:  # alle Server liefern für die Gesamtabfrage nichts
            return json.dumps({"elements": []}).encode()
        return json.dumps({"elements": [road]}).encode()

    msgs = []
    md = fetch_map_data(lat, lon, post=post, sleep=lambda s: None, progress=msgs.append)
    assert md.coverage > 0.97
    assert any("abschnittsweise" in m for m in msgs)


def test_query_is_simple_and_split_into_lines(raw_track):
    lat = 48.0 + np.arange(0, 0.45, 0.0005)
    pts = simplify_track(lat, np.full(lat.size, 11.0), max_points=250)
    q = build_query(pts)
    assert "!~" not in q  # keine komplexen Negativ-Filter mehr in der Server-Abfrage
    assert q.count('way["highway"](around:') >= 3  # lange Strecke -> mehrere around-Linien
    assert q.count("[out:json]") == 1  # aber eine einzige Abfrage
    for line in q.splitlines():
        if "around:" in line:
            n = line.split("around:")[1].count(",")  # Radius + 2 Werte je Punkt
            assert (n) // 2 <= 40


def test_client_side_filter():
    assert classify({"highway": "footway", "footway": "sidewalk"}) is None
    assert classify({"highway": "service", "service": "driveway"}) is None
    assert classify({"highway": "residential", "access": "private"}) is None
    assert classify({"highway": "steps"}) is None
    assert classify({"highway": "service"}) == "minor"
    assert classify({"highway": "track"}) == "path"


def test_empty_result_is_an_error():
    def empty(url, query, timeout):
        return json.dumps({"elements": []}).encode()

    with pytest.raises(MapDataError, match="leere Antwort"):
        fetch_map_data(np.array([48.0, 48.01]), np.array([11.0, 11.0]), post=empty, sleep=lambda s: None)


def test_successful_server_is_tried_first_next_time(monkeypatch):
    from cycle_overlay.core import osm

    monkeypatch.setattr(osm, "_last_good", {})
    lat = np.array([48.0, 48.01])
    lon = np.array([11.0, 11.0])
    road = _road_along(lat, lon + 0.0005, 1)
    hosts = []

    def post(url, query, timeout):
        hosts.append(url)
        if "mail.ru" in url:
            return json.dumps({"elements": [road]}).encode()
        return json.dumps({"elements": []}).encode()

    fetch_map_data(lat, lon, post=post, sleep=lambda s: None, min_coverage=0)
    assert "mail.ru" in hosts[-1]
    hosts.clear()
    fetch_map_data(lat, lon + 0.001, post=post, sleep=lambda s: None, min_coverage=0)
    assert "mail.ru" in hosts[0] and len(hosts) == 1


def test_slow_transient_failure_skips_server(monkeypatch):
    from cycle_overlay.core import osm

    monkeypatch.setattr(osm, "_last_good", {})
    monkeypatch.setattr(osm, "SLOW_FAILURE_S", -1.0)  # jeder Fehlschlag gilt als langsam
    lat = np.array([48.0, 48.01])
    lon = np.array([11.0, 11.0])
    road = _road_along(lat, lon + 0.0005, 1)
    hosts = []

    def post(url, query, timeout):
        hosts.append(url)
        if "overpass-api" in url:
            raise osm.TransientError("504")
        return json.dumps({"elements": [road]}).encode()

    fetch_map_data(lat, lon, post=post, sleep=lambda s: None, min_coverage=0)
    assert sum("overpass-api" in h for h in hosts) == 1


@pytest.fixture(autouse=True)
def _reset_server_preference(monkeypatch):
    from cycle_overlay.core import osm

    monkeypatch.setattr(osm, "_last_good", {})
