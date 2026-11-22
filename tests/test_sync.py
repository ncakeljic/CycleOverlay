from __future__ import annotations

from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import pytest

from cycle_overlay.core.models import VideoInfo
from cycle_overlay.core.sync import (
    SyncError,
    VideoSync,
    check_window,
    get_profile,
    video_start_utc,
)

UTC = timezone.utc
TRACK_START = datetime(2026, 6, 14, 8, 0, 0, tzinfo=UTC)


def info(creation: str | None = "2026-06-14T10:05:00+00:00", duration=60.0, fps=Fraction(30000, 1001), apple=None):
    return VideoInfo(
        path=Path("clip.mp4"),
        width=1920,
        height=1080,
        fps=fps,
        duration=duration,
        n_frames=int(round(duration * fps)),
        creation_time=datetime.fromisoformat(creation) if creation else None,
        apple_creation_date=datetime.fromisoformat(apple) if apple else None,
    )


def test_gopro_local_time_summer():
    # GoPro schreibt 10:05 Ortszeit (CEST = UTC+2) als "UTC"
    start = video_start_utc(info(), get_profile("gopro", "Europe/Berlin"))
    assert start == datetime(2026, 6, 14, 8, 5, tzinfo=UTC)


def test_gopro_local_time_winter():
    start = video_start_utc(info("2026-01-10T10:05:00+00:00"), get_profile("gopro", "Europe/Berlin"))
    assert start == datetime(2026, 1, 10, 9, 5, tzinfo=UTC)  # CET = UTC+1


def test_generic_profile_treats_creation_time_as_utc():
    start = video_start_utc(info(), get_profile("generic"))
    assert start == datetime(2026, 6, 14, 10, 5, tzinfo=UTC)


def test_creation_time_is_end():
    from dataclasses import replace

    prof = replace(get_profile("generic"), creation_time_is_end=True)
    start = video_start_utc(info("2026-06-14T08:06:00+00:00", duration=60), prof)
    assert start == datetime(2026, 6, 14, 8, 5, tzinfo=UTC)


def test_apple_creationdate_preferred():
    i = info("2026-06-14T08:05:03+00:00", apple="2026-06-14T10:05:00+02:00")
    assert video_start_utc(i, get_profile("apple")) == datetime(2026, 6, 14, 8, 5, tzinfo=UTC)


def test_missing_creation_time_raises():
    with pytest.raises(SyncError):
        video_start_utc(info(None), get_profile("gopro", "Europe/Berlin"))


def test_unknown_timezone_and_profile():
    with pytest.raises(SyncError):
        video_start_utc(info(), get_profile("gopro", "Mars/Olympus"))
    with pytest.raises(SyncError):
        get_profile("super8")


def test_track_time_and_offset():
    s = VideoSync.from_video(info(), TRACK_START, get_profile("gopro", "Europe/Berlin"))
    assert s.base_offset_s == 300
    assert float(s.track_time(10)) == 310
    s.offset_s = -2.5
    assert s.window() == (297.5, 357.5)


def test_frame_track_times():
    s = VideoSync.from_video(info(duration=2.0), TRACK_START, get_profile("gopro", "Europe/Berlin"))
    ft = s.frame_track_times()
    assert ft.size == 60
    assert ft[0] == 300
    assert ft[1] == pytest.approx(300 + 1001 / 30000)


def test_sync_point_offset_snaps_to_frame():
    s = VideoSync.from_video(info(), TRACK_START, get_profile("gopro", "Europe/Berlin"))
    # Moment bei Video 12,0 s gehört eigentlich zu Track 315,3 s
    off = s.offset_for_sync_point(12.0, 315.3)
    frame = 1001 / 30000
    assert off == pytest.approx(3.3, abs=frame / 2)
    assert (off / frame) == pytest.approx(round(off / frame))
    s.offset_s = off
    assert float(s.track_time(12.0)) == pytest.approx(315.3, abs=frame / 2)


def test_check_ok_partial_outside():
    assert check_window(100, 200, 1200).status == "ok"
    assert check_window(1150, 1250, 1200).status == "partial"
    out = check_window(7500, 7560, 1200)
    assert out.status == "outside"
    assert out.suggested_offset_s == -7200
    assert "-2 h" in out.message


def test_check_suggests_smallest_hour_shift():
    out = check_window(3700, 3760, 1200)  # 1 h zu spät
    assert out.suggested_offset_s == -3600


def test_check_without_suggestion():
    out = check_window(-50000, -49000, 1200)
    assert out.status == "outside" and out.suggested_offset_s is None
