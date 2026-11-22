from __future__ import annotations

from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import pytest

from cycle_overlay.core.video_probe import VideoProbeError, parse_ffprobe_json, probe

from .conftest import make_clip, needs_ffmpeg


def _data(**video):
    v = {"codec_type": "video", "codec_name": "hevc", "width": 3840, "height": 2160,
         "r_frame_rate": "60000/1001", "duration": "10.01", "nb_frames": "600"}
    v.update(video)
    return {"streams": [{"codec_type": "audio"}, v], "format": {"duration": "10.01", "tags": {}}}


def test_parse_basic():
    i = parse_ffprobe_json(_data(), Path("a.mp4"))
    assert (i.width, i.height) == (3840, 2160)
    assert i.fps == Fraction(60000, 1001)
    assert i.n_frames == 600
    assert i.camera == "generic"


def test_parse_rotation_swaps_size():
    i = parse_ffprobe_json(_data(side_data_list=[{"rotation": -90}]), Path("a.mov"))
    assert (i.width, i.height) == (2160, 3840)


def test_parse_camera_detection():
    assert parse_ffprobe_json(_data(tags={"handler_name": "GoPro H.265"}), Path("a")).camera == "gopro"
    d = _data()
    d["format"]["tags"] = {"com.apple.quicktime.make": "Apple", "com.apple.quicktime.creationdate": "2026-06-14T10:05:00+0200"}
    i = parse_ffprobe_json(d, Path("a"))
    assert i.camera == "apple"
    assert i.apple_creation_date == datetime(2026, 6, 14, 8, 5, tzinfo=timezone.utc)


def test_parse_no_video():
    with pytest.raises(VideoProbeError):
        parse_ffprobe_json({"streams": [{"codec_type": "audio"}], "format": {}}, Path("a"))


def test_missing_nb_frames_estimated():
    i = parse_ffprobe_json(_data(nb_frames=None, r_frame_rate="25/1", duration="4.0"), Path("a"))
    assert i.n_frames == 100


@needs_ffmpeg
def test_probe_real_clip(tmp_path):
    clip = make_clip(tmp_path / "GX010001.MP4", creation_time="2026-06-14T10:05:00.000000Z", handler="GoPro AVC")
    i = probe(clip)
    assert i.fps == Fraction(30000, 1001)
    assert i.n_frames == 90
    assert i.duration == pytest.approx(3.0, abs=0.05)
    assert i.creation_time == datetime(2026, 6, 14, 10, 5, tzinfo=timezone.utc)
    assert i.camera == "gopro"


@needs_ffmpeg
def test_probe_missing_file(tmp_path):
    with pytest.raises(VideoProbeError):
        probe(tmp_path / "fehlt.mp4")


def test_variable_frame_rate_uses_average(tmp_path):
    from pathlib import Path

    from cycle_overlay.core.video_probe import parse_ffprobe_json

    data = {"streams": [{"codec_type": "video", "r_frame_rate": "90000/1", "avg_frame_rate": "2997/100",
                         "width": 1920, "height": 1080, "duration": "10.0"}], "format": {}}
    info = parse_ffprobe_json(data, Path("x.mov"))
    assert abs(float(info.fps) - 29.97) < 0.01
    data["streams"][0]["r_frame_rate"] = "30000/1001"
    data["streams"][0]["avg_frame_rate"] = "29999/1001"
    assert parse_ffprobe_json(data, Path("x.mov")).fps.numerator == 30000
