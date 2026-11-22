from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from cycle_overlay.core.gpx_parser import parse_gpx
from cycle_overlay.core.processor import process

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_GPX = FIXTURES / "sample_ride.gpx"

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe nicht installiert")


@pytest.fixture(autouse=True)
def _isolated_user_dirs(tmp_path, monkeypatch):
    """Tests schreiben nie in die echten Benutzerordner."""
    from cycle_overlay import app_paths

    monkeypatch.setenv("CYCLE_OVERLAY_HOME", str(tmp_path / "user"))
    app_paths._SESSION_DIR = None
    yield
    app_paths.clear_session_cache()


@pytest.fixture(scope="session")
def raw_track():
    return parse_gpx(SAMPLE_GPX)


@pytest.fixture(scope="session")
def telemetry(raw_track):
    return process(raw_track)


def make_clip(path: Path, *, creation_time: str | None, fps: str = "30000/1001", seconds: float = 3.0,
              handler: str | None = None, size: str = "320x240") -> Path:
    """Kurzen Testclip mit ffmpeg erzeugen."""
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s={size}:r={fps}",
           "-t", str(seconds), "-c:v", "mpeg4"]
    if creation_time:
        cmd += ["-metadata", f"creation_time={creation_time}"]
    if handler:
        cmd += ["-metadata:s:v:0", f"handler_name={handler}"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True)
    return path
