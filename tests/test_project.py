from __future__ import annotations

from fractions import Fraction

import numpy as np
import pytest

from cycle_overlay.core.project import Project, ProjectModel
from cycle_overlay.core.video_frames import VideoFrameSource, preview_size

from .conftest import SAMPLE_GPX, make_clip, needs_ffmpeg


@pytest.fixture()
def model():
    m = ProjectModel()
    m.load_gpx(SAMPLE_GPX)
    return m


@pytest.fixture()
def clip(tmp_path):
    return make_clip(tmp_path / "GX010001.MP4", creation_time="2026-06-14T10:05:00.000000Z",
                     handler="GoPro AVC", seconds=4.0, size="640x360")


def test_manual_mode_defaults(model):
    assert model.ready and not model.has_video
    assert model.timeline_duration() == 1200
    assert model.export_range() == (0.0, 60.0)
    model.set_in_out(400, 430)
    assert model.export_range() == (400, 430)
    model.project.export.fps = "29.97"
    assert model.fps() == Fraction(30000, 1001)
    assert model.n_frames() == 899
    tt = model.export_track_times()
    assert tt[0] == 400 and tt.size == 899
    assert model.frame_size() == (1920, 1080)


def test_in_out_swapped_and_clamped(model):
    model.set_in_out(500, 300)
    assert model.export_range() == (300, 500)
    model.set_in_out(out_s=99999)
    assert model.export_range()[1] == 1200


def test_in_out_marks_move_together(model):
    model.set_in_out(100, 130)
    model.set_in_out(in_s=400)  # In hinter Out: Out wandert mit, Länge bleibt 30 s
    assert model.export_range() == (400, 430)
    model.set_in_out(out_s=50)  # Out vor In: In wandert mit
    assert model.export_range() == (20, 50)
    model.set_in_out(in_s=30)  # normaler Fall
    assert model.export_range() == (30, 50)
    model.set_in_out(in_s=1190)
    assert model.export_range() == (1190, 1200)


def test_signal_rows(model):
    rows = {r.channel: r for r in model.signal_rows()}
    assert rows["power"].available and "W" in rows["power"].summary
    assert rows["distance"].summary == "9.00 km"
    assert "time_of_day" in model.available_channels()


def test_add_remove_duplicate_widgets(model):
    n = len(model.project.layout.widgets)
    a = model.add_widget("power", "bar")
    b = model.add_widget("power", "bar")
    assert a.id == "power_2" and b.id == "power_3"  # "power" gibt es im Preset schon
    dup = model.duplicate_widget(a.id)
    assert dup.id == "power_2_2"
    assert len(model.project.layout.widgets) == n + 3
    model.remove_widget(a.id)
    assert model.widget(a.id) is None


def test_widget_hit_test(model):
    cfg = model.widget("speed")
    x, y, w, h = cfg.rect(1920, 1080)
    assert model.widget_at(x + w / 2, y + h / 2, 1920, 1080) is cfg
    assert model.widget_at(960, 540, 1920, 1080) is None
    cfg.visible = False
    assert model.widget_at(x + w / 2, y + h / 2, 1920, 1080) is None


def test_frame_values_use_clip_start_for_text_rate(model):
    model.set_in_out(400, 460)
    v = model.frame_values([400.1])
    assert v.t0 == 400


def test_default_output_without_video(model, tmp_path):
    model.set_in_out(723, 800)
    out = model.default_output()
    assert out == SAMPLE_GPX.parent / "sample_ride_001203_overlay.mov"
    model.project.export.out_dir = str(tmp_path)
    model.project.export.filename = "abfahrt"
    assert model.default_output() == tmp_path / "abfahrt.mov"
    model.project.export.profile = "png_sequence"
    assert model.default_output() == tmp_path / "abfahrt"
    assert model.estimated_size() > 0


@needs_ffmpeg
def test_video_mode(model, clip):
    model.set_sync_options(timezone="Europe/Berlin")
    model.load_video(clip)
    assert model.has_video and model.camera_key == "gopro"
    assert model.sync_check().status == "ok"
    assert model.timeline_duration() == pytest.approx(4.0, abs=0.05)
    assert model.track_time(0) == 300
    assert model.timeline_time_for_track(301) == pytest.approx(1)
    assert model.frame_size() == (640, 360)
    assert model.fps() == Fraction(30000, 1001)
    assert model.n_frames() == 120
    assert model.export_track_times()[0] == 300
    assert model.default_output() == clip.with_name("GX010001_overlay.mov")
    model.project.export.size_from_video = False
    model.project.export.width, model.project.export.height = 3840, 2160
    assert model.frame_size() == (3840, 2160)


@needs_ffmpeg
def test_sync_options_and_suggestion(model, clip):
    model.load_video(clip)
    model.set_sync_options(timezone="UTC")
    chk = model.sync_check()
    assert chk.status == "outside" and chk.suggested_offset_s == -7200
    assert model.apply_suggestion()
    assert model.sync_check().status == "ok"
    model.set_sync_options(offset_s=0, timezone="Europe/Berlin")
    off = model.set_sync_point(2.0, 305.0)
    assert off == pytest.approx(3.0, abs=0.04)
    assert model.track_time(2.0) == pytest.approx(305.0, abs=0.04)
    with pytest.raises(ValueError):
        model.set_sync_options(camera="super8")


@needs_ffmpeg
def test_project_roundtrip(model, clip, tmp_path):
    model.load_video(clip)
    model.set_sync_options(timezone="Europe/Berlin", offset_s=1.5)
    model.add_widget("vam", "bar")
    path = model.project.save(tmp_path / "projekt")
    assert path.suffix == ".cyov"
    m2 = ProjectModel.open(path)
    assert m2.has_video and m2.project.offset_s == 1.5
    assert m2.track_time(0) == pytest.approx(301.5)
    assert m2.widget("vam") is not None
    assert m2.project.to_dict() == model.project.to_dict()


def test_project_relative_paths(tmp_path):
    import shutil

    src = tmp_path / "a"
    src.mkdir()
    shutil.copy(SAMPLE_GPX, src / "f.gpx")
    p = Project(gpx_path=str(src / "f.gpx"))
    p.save(src / "x.cyov")
    moved = tmp_path / "b"
    src.rename(moved)
    p2 = Project.load(moved / "x.cyov")
    assert p2.gpx_path == str(moved / "f.gpx")


def test_project_load_errors(tmp_path):
    bad = tmp_path / "x.cyov"
    bad.write_text("nix")
    with pytest.raises(ValueError):
        Project.load(bad)


def test_preview_size():
    assert preview_size(3840, 2160) == (960, 540)
    assert preview_size(640, 360) == (640, 360)
    assert preview_size(2704, 1520) == (960, 540)


@needs_ffmpeg
def test_video_frame_source(clip):
    src = VideoFrameSource(clip, 320, 180, Fraction(30000, 1001))
    f = src.grab(1.0)
    assert f.shape == (180, 320, 3)
    assert src.grab(1.0) is f  # Cache
    assert src.grab(999) is None
    with src.stream(3.5) as st:
        frames = []
        while (fr := st.read(2.0)) is not None:
            frames.append(fr)
    assert 10 <= len(frames) <= 20
    assert np.all(frames[0] == frames[0][0, 0])  # einfarbiger Testclip


@needs_ffmpeg
def test_batch_jobs(model, tmp_path):
    a = make_clip(tmp_path / "GX010001.MP4", creation_time="2026-06-14T10:05:00.000000Z", handler="GoPro AVC",
                  seconds=2.0, size="640x360")
    b = make_clip(tmp_path / "GX020001.MP4", creation_time="2026-06-14T10:10:00.000000Z", handler="GoPro AVC",
                  seconds=2.0, size="640x360", fps="25")
    far = make_clip(tmp_path / "andere.mp4", creation_time="2026-06-20T10:00:00.000000Z", handler="GoPro AVC",
                    seconds=1.0, size="320x180")
    broken = tmp_path / "kaputt.mp4"
    broken.write_bytes(b"xx")
    model.set_sync_options(timezone="Europe/Berlin", offset_s=1.0)
    jobs = model.batch_jobs([a, b, far, broken])
    assert [j.ok for j in jobs] == [True, True, False, False]
    assert jobs[0].track_times[0] == 301 and jobs[0].fps == Fraction(30000, 1001)
    assert jobs[1].track_times[0] == 601 and jobs[1].fps == 25 and jobs[1].track_times.size == 50
    assert jobs[0].target == tmp_path / "GX010001_overlay.mov"
    assert jobs[2].check.status == "outside"
    assert jobs[3].error
    model.project.export.out_dir = str(tmp_path / "out")
    model.project.export.size_from_video = False
    model.project.export.width, model.project.export.height = 1280, 720
    j = model.batch_jobs([a])[0]
    assert j.target == tmp_path / "out" / "GX010001_overlay.mov" and j.size == (1280, 720)


# --------------------------------------------------------------------------- Projektpaket


def _fake_osm(url, query, timeout):
    import json

    return json.dumps({"elements": [
        {"type": "way", "tags": {"highway": "residential"},
         "geometry": [{"lat": 48.0, "lon": 11.0}, {"lat": 48.01, "lon": 11.0}]},
        {"type": "way", "tags": {"natural": "water"},
         "geometry": [{"lat": 48.0, "lon": 11.0}, {"lat": 48.0, "lon": 11.001}, {"lat": 48.001, "lon": 11.0},
                      {"lat": 48.0, "lon": 11.0}]},
    ]}).encode()


def test_bundle_is_self_contained(tmp_path):
    import shutil
    import zipfile

    gpx = tmp_path / "fahrt.gpx"
    shutil.copy(SAMPLE_GPX, gpx)
    m = ProjectModel()
    m.load_gpx(gpx)
    m.download_map_data(post=_fake_osm)
    m.add_widget("position", "minimap")
    m.set_in_out(300, 330)
    path = m.save(tmp_path / "projekt")
    assert path.suffix == ".cyov" and zipfile.is_zipfile(path)
    with zipfile.ZipFile(path) as z:
        assert {"manifest.json", "project.json", "media/fahrt.gpx", "mapdata/osm.json"} <= set(z.namelist())
    gpx.unlink()  # Originaldatei weg – das Projekt bringt sie mit
    moved = tmp_path / "woanders"
    moved.mkdir()
    path = path.rename(moved / path.name)
    m2 = ProjectModel.open(path)
    assert m2.ready and m2.telemetry.duration == 1200
    assert m2.map_data is not None and len(m2.map_data) == 2
    assert m2.export_range() == (300, 330)
    assert m2.widget("position").type == "minimap"
    assert m2.missing_media == []
    assert "1 Wege" in m2.map_status()


@needs_ffmpeg
def test_bundle_video_reference_and_relink(model, clip, tmp_path):
    model.set_sync_options(timezone="Europe/Berlin", offset_s=2.5)
    model.load_video(clip)
    path = model.save(tmp_path / "p.cyov")
    moved_clip = clip.with_name("umbenannt.MP4")
    clip.rename(moved_clip)
    m2 = ProjectModel.open(path)
    assert m2.ready and not m2.has_video
    assert m2.missing_media == [str(clip)]
    m2.relink_video(moved_clip)
    assert m2.has_video and m2.project.offset_s == 2.5
    assert m2.track_time(0) == pytest.approx(302.5)


def test_legacy_json_project_still_opens(tmp_path):
    p = Project(gpx_path=str(SAMPLE_GPX))
    path = p.save(tmp_path / "alt.cyov")
    m = ProjectModel.open(path)
    assert m.ready


def test_bundle_errors(tmp_path):
    import json
    import zipfile

    foreign = tmp_path / "x.cyov"
    with zipfile.ZipFile(foreign, "w") as z:
        z.writestr("irgendwas.txt", "hallo")
    with pytest.raises(ValueError, match="kein CycleOverlay-Projekt"):
        ProjectModel.open(foreign)
    newer = tmp_path / "neu.cyov"
    with zipfile.ZipFile(newer, "w") as z:
        z.writestr("manifest.json", json.dumps({"format": "cycleoverlay-project", "version": 99}))
        z.writestr("project.json", "{}")
    with pytest.raises(ValueError, match="neueren Version"):
        ProjectModel.open(newer)


def test_new_track_drops_old_map_data(model, tmp_path):
    model.download_map_data(post=_fake_osm)
    assert model.map_data is not None
    from .fit_writer import points_from_raw, write_fit

    other = tmp_path / "andere.fit"
    write_fit(other, points_from_raw(model.raw)[:300])
    model.load_gpx(other)
    assert model.map_data is None


def test_map_download_requires_gps(tmp_path):
    from cycle_overlay.core.osm import MapDataError

    from .fit_writer import write_fit

    pts = [{"unix_ts": 1_800_000_000 + i, "power": 200, "speed": 8.0, "dist": 8.0 * i} for i in range(60)]
    write_fit(tmp_path / "indoor.fit", pts)
    m = ProjectModel()
    m.load_gpx(tmp_path / "indoor.fit")
    with pytest.raises(MapDataError):
        m.download_map_data(post=_fake_osm)


def test_downloaded_data_lives_only_in_session(model):
    from cycle_overlay import app_paths

    model.download_map_data(post=_fake_osm)
    session = app_paths.session_cache_dir()
    cached = list(session.rglob("osm_*.json"))
    assert cached and str(session).startswith(str(app_paths.user_data_dir()))
    app_paths.clear_session_cache()
    assert not session.exists()
    assert not list((app_paths.user_data_dir()).rglob("osm_*.json"))


def test_cleanup_stale_sessions():
    import os
    import time

    from cycle_overlay import app_paths

    root = app_paths.user_data_dir() / "session"
    old = root / "123-alt"
    old.mkdir(parents=True)
    (old / "x.json").write_text("{}")
    os.utime(old, (time.time() - 2 * 86400, time.time() - 2 * 86400))
    fresh = root / "456-neu"
    fresh.mkdir()
    legacy = app_paths.user_data_dir() / "mapcache"
    legacy.mkdir()
    assert app_paths.cleanup_stale_sessions() == 2
    assert not old.exists() and fresh.exists() and not legacy.exists()


def test_export_content_overlay_or_video(model, clip, tmp_path):
    # ohne Video gibt es nur „Nur Anzeigen“, auch wenn „video“ eingestellt ist
    model.project.export.content = "video"
    assert not model.export_with_video and model.active_profile().key == "prores4444"
    model.set_sync_options(timezone="Europe/Berlin")
    model.load_video(clip)
    assert model.export_with_video
    assert model.active_profile().key == "mp4_h264"
    assert model.default_output() == clip.with_name("GX010001_mit_daten.mp4")
    model.project.export.video_profile = "mov_prores422"
    assert model.default_output().suffix == ".mov" and model.estimated_size() > 0
    model.set_export_content("overlay")
    assert model.default_output() == clip.with_name("GX010001_overlay.mov")
    with pytest.raises(ValueError):
        model.set_export_content("alles")
    # Speichern/Laden und unbekannte Werte
    model.set_export_content("video")
    p = Project.from_dict(model.project.to_dict())
    assert p.export.content == "video" and p.export.video_profile == "mov_prores422"
    d = model.project.to_dict()
    d["export"].update(content="x", video_profile="y", profile="mp4_h264", quality="ultra")
    p = Project.from_dict(d)
    assert (p.export.content, p.export.video_profile, p.export.profile, p.export.quality) == (
        "overlay", "mp4_h264", "prores4444", "standard")
    jobs = model.batch_jobs([clip])
    assert jobs[0].target == clip.with_name("GX010001_mit_daten.mov")
