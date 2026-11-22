from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path

import pytest

from cycle_overlay.core.project import Project
from cycle_overlay.core.sync import VideoSync, get_profile, metadata_plausible
from cycle_overlay.core.units import format_offset, parse_offset
from cycle_overlay.core.video_probe import VideoInfo

T0 = datetime(2026, 4, 26, 4, 48, tzinfo=timezone.utc)


def _info(created: datetime | None, duration=427.0):
    import inspect

    params = inspect.signature(VideoInfo).parameters
    kw = dict(path=Path("x.mp4"), width=1920, height=1080, fps=Fraction(30000, 1001), duration=duration,
              n_frames=int(duration * 29.97), creation_time=created, creation_time_raw=None, camera="generic")
    return VideoInfo(**{k: v for k, v in kw.items() if k in params})


def test_plausibility():
    assert metadata_plausible(100, 60, 3600)
    assert metadata_plausible(-30, 60, 3600)  # teilweise
    assert metadata_plausible(3600 * 2 + 4000, 60, 3600 * 5)  # Zeitzonen-Vorschlag möglich
    assert not metadata_plausible(-5 * 365 * 86400, 60, 3600)


def test_auto_anchors_on_wrong_camera_clock():
    info = _info(datetime(2021, 3, 21, 8, 49, 3, tzinfo=timezone.utc))
    prof = get_profile("generic", "UTC")
    s = VideoSync.from_video(info, T0, prof, 0.0, time_source="auto", track_duration=36000)
    assert s.anchored and "Kamera-Uhr" in s.anchor_reason
    assert s.window()[0] == 0.0
    assert s.check(36000).status == "ok"
    s2 = VideoSync.from_video(info, T0, prof, 3723.5, time_source="auto", track_duration=36000)
    assert s2.window()[0] == pytest.approx(3723.5)


def test_auto_keeps_good_metadata_and_explicit_modes():
    info = _info(T0 + timedelta(hours=1))
    prof = get_profile("generic", "UTC")
    s = VideoSync.from_video(info, T0, prof, 0.0, time_source="auto", track_duration=36000)
    assert not s.anchored and s.window()[0] == pytest.approx(3600)
    s = VideoSync.from_video(info, T0, prof, 0.0, time_source="track_start", track_duration=36000)
    assert s.anchored and s.window()[0] == 0
    bad = _info(datetime(2021, 3, 21, tzinfo=timezone.utc))
    s = VideoSync.from_video(bad, T0, prof, 0.0, time_source="metadata", track_duration=36000)
    assert not s.anchored and s.check(36000).status == "outside"


def test_auto_without_creation_time():
    s = VideoSync.from_video(_info(None), T0, get_profile("generic", "UTC"), 0.0, time_source="auto",
                             track_duration=36000)
    assert s.anchored and s.metadata_start is None


def test_old_projects_keep_metadata_reference():
    assert Project.from_dict({"offset_s": 12.0}).time_source == "metadata"
    assert Project.from_dict({"offset_s": 0.0}).time_source == "auto"
    assert Project.from_dict({"offset_s": 12.0, "time_source": "auto"}).time_source == "auto"


@pytest.mark.parametrize("text,value", [
    ("12", 12), ("-12.5", -12.5), ("1,5", 1.5), ("90 s", 90), ("+90s", 90), ("1:30", 90),
    ("-1:02:03", -3723), ("0:00:00.040", 0.04), ("−0:10", -10), ("100:00:00", 360000),
])
def test_parse_offset(text, value):
    assert parse_offset(text) == pytest.approx(value)


@pytest.mark.parametrize("text", ["", "-", "abc", "1:60", "1:70:00", "1.5:00", "1:2:3:4", "1::2"])
def test_parse_offset_invalid(text):
    with pytest.raises(ValueError):
        parse_offset(text)


def test_format_offset_roundtrip():
    for v in (0, 12.5, -3723.04, 359999.999, -0.033):
        assert parse_offset(format_offset(v)) == pytest.approx(v, abs=1e-3)
    assert format_offset(3723.5) == "+1:02:03.500"
    assert format_offset(-0.5) == "-0:00:00.500"


def test_unknown_timezone_is_reported_not_hidden():
    from cycle_overlay.core.sync import SyncError

    info = _info(T0)
    prof = get_profile("gopro", "Mars/Olympus")
    with pytest.raises(SyncError, match="Zeitzone"):
        VideoSync.from_video(info, T0, prof, 0.0, time_source="auto", track_duration=36000)
    s = VideoSync.from_video(info, T0, prof, 0.0, time_source="track_start", track_duration=36000)
    assert s.anchored and s.metadata_start is None


def test_invalid_timezone_rejected_without_side_effects():
    from cycle_overlay.core.project import ProjectModel

    m = ProjectModel()
    m.set_sync_options(timezone="Europe/Berlin", offset_s=3.0)
    with pytest.raises(ValueError, match="Zeitzone"):
        m.set_sync_options(camera="gopro", timezone="Europe/Atlantis", offset_s=9.0)
    assert m.project.timezone == "Europe/Berlin" and m.project.offset_s == 3.0 and m.project.camera == "auto"
