"""Einstellungen, Pfade, ffmpeg-Suche, Einheiten, Kommandozeile."""

from __future__ import annotations

import csv
import math

import pytest

from cycle_overlay import app_paths
from cycle_overlay.cli import main, parse_clock
from cycle_overlay.core.settings import AppSettings, settings_file
from cycle_overlay.core.units import convert, format_duration, format_value
from cycle_overlay.export import ffmpeg_locator

from .conftest import SAMPLE_GPX, make_clip, needs_ffmpeg


def test_user_dirs_are_overridable(tmp_path):
    assert str(app_paths.user_config_dir()).startswith(str(tmp_path))
    assert app_paths.user_log_dir().is_dir()


def test_resource_path_points_into_package():
    assert app_paths.resource_path("README.txt").is_file()


def test_settings_roundtrip():
    s = AppSettings(ffmpeg_path="/x/ffmpeg", units="imperial", default_timezone="Europe/Berlin")
    path = s.save()
    assert path == settings_file()
    assert AppSettings.load() == s


def test_settings_tolerate_garbage_and_unknown_keys():
    settings_file().write_text('{"units": "imperial", "neu": 1}', encoding="utf-8")
    assert AppSettings.load().units == "imperial"
    settings_file().write_text("kein json", encoding="utf-8")
    assert AppSettings.load() == AppSettings()


def test_find_executable_prefers_configured(tmp_path):
    fake = tmp_path / ffmpeg_locator.executable_name("ffmpeg")
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    assert ffmpeg_locator.find_executable("ffmpeg", fake) == fake
    assert ffmpeg_locator.find_executable("ffmpeg", tmp_path) == fake  # Ordner angegeben


def test_find_executable_missing(monkeypatch):
    monkeypatch.setattr(ffmpeg_locator.shutil, "which", lambda _: None)
    monkeypatch.setattr(ffmpeg_locator, "_common_dirs", lambda: [])
    assert ffmpeg_locator.find_executable("gibtsnicht-xyz") is None


@needs_ffmpeg
def test_locate_real_ffmpeg():
    st = ffmpeg_locator.locate()
    assert st.ffmpeg and st.ffprobe and st.version
    assert st.has_prores_ks


@pytest.mark.parametrize(
    "ch,val,system,expected",
    [
        ("speed", 10.0, "metric", (36.0, "km/h")),
        ("speed", 10.0, "imperial", (pytest.approx(22.369, rel=1e-3), "mph")),
        ("distance", 1609.344, "imperial", (pytest.approx(1.0), "mi")),
        ("elevation", 100.0, "imperial", (pytest.approx(328.084, rel=1e-4), "ft")),
        ("temperature", 20.0, "imperial", (68.0, "°F")),
        ("power", 250.0, "imperial", (250.0, "W")),
    ],
)
def test_units(ch, val, system, expected):
    assert convert(ch, val, system) == expected


def test_formatting():
    assert format_duration(3725) == "1:02:05"
    assert format_value("speed", 10.0) == "36.0 km/h"
    assert format_value("heart_rate", math.nan) == "-- bpm"


@pytest.mark.parametrize("text,sec", [("1:02:03", 3723), ("02:03", 123), ("75.5", 75.5), ("0:00:01.5", 1.5)])
def test_parse_clock(text, sec):
    assert parse_clock(text) == sec


def test_cli_info_and_values(capsys):
    assert main(["info", str(SAMPLE_GPX), "--tz", "Europe/Berlin"]) == 0
    out = capsys.readouterr().out
    assert "9.00 km" in out and "75 m" in out and "Herzfrequenz" in out
    assert main(["values", str(SAMPLE_GPX), "--at", "0:07:30"]) == 0
    assert "18.0 km/h" in capsys.readouterr().out


def test_cli_frames_without_video(tmp_path):
    out = tmp_path / "f.csv"
    assert main(["frames", str(SAMPLE_GPX), "--start", "0:07:00", "--duration", "2", "--fps", "29.97", "--out", str(out)]) == 0
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert len(rows) == 59
    assert float(rows[0]["speed [km/h]"]) == pytest.approx(18.0, abs=0.2)


def test_cli_bad_file(tmp_path, capsys):
    bad = tmp_path / "x.gpx"
    bad.write_text("Unsinn")
    assert main(["info", str(bad)]) == 1
    assert "Fehler" in capsys.readouterr().err


@needs_ffmpeg
def test_cli_sync_and_frames_with_video(tmp_path, capsys):
    clip = make_clip(tmp_path / "GX010001.MP4", creation_time="2026-06-14T10:05:00.000000Z", handler="GoPro AVC")
    assert main(["sync", str(SAMPLE_GPX), str(clip), "--tz", "Europe/Berlin"]) == 0
    assert "[OK]" in capsys.readouterr().out
    # falsche Zeitzone -> Fehler mit Vorschlag
    assert main(["sync", str(SAMPLE_GPX), str(clip), "--tz", "UTC"]) == 2
    assert "--offset -7200" in capsys.readouterr().out
    out = tmp_path / "f.csv"
    assert main(["frames", str(SAMPLE_GPX), "--video", str(clip), "--tz", "Europe/Berlin", "--out", str(out)]) == 0
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert len(rows) == 90
    assert float(rows[0]["track_time_s"]) == pytest.approx(300.0)
    assert rows[0]["uhrzeit"].startswith("10:05:00")
