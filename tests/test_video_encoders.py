"""Encoder-Auswahl für den Export mit Video (ohne Qt)."""

from __future__ import annotations

from fractions import Fraction

import pytest

from cycle_overlay.export import video_encoders as ve


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(ve, "_PROBE", {})


def test_hardware_first_but_only_if_it_really_works(monkeypatch):
    tried = []

    def fake_probe(ffmpeg, enc):
        tried.append(enc)
        return enc == "libx264"  # z. B. NVENC gelistet, aber keine NVIDIA-Karte

    monkeypatch.setattr(ve, "probe_encoder", fake_probe)
    avail = {"h264_nvenc", "h264_qsv", "libx264", "h264_mf"}
    assert ve.choose_encoder("h264", "ffmpeg", avail) == "libx264"
    assert tried == ["h264_nvenc", "h264_qsv", "libx264"]


def test_large_frames_skip_h264_hardware(monkeypatch):
    monkeypatch.setattr(ve, "probe_encoder", lambda f, e: True)
    avail = {"h264_videotoolbox", "libx264"}
    assert ve.choose_encoder("h264", "ffmpeg", avail, 1920, 1080) == "h264_videotoolbox"
    assert ve.choose_encoder("h264", "ffmpeg", avail, 5760, 2880) == "libx264"
    assert ve.choose_encoder("h264", "ffmpeg", {"h264_videotoolbox"}, 5760, 2880) is None
    assert ve.choose_encoder("hevc", "ffmpeg", {"hevc_videotoolbox"}, 5760, 2880) == "hevc_videotoolbox"


def test_encoder_args():
    x = ve.encoder_args("h264", "libx264", "high", 1920, 1080, 30)
    assert x.bitrate is None and "-crf" in x.args and "16" in x.args
    nv = ve.encoder_args("hevc", "hevc_nvenc", "standard", 3840, 2160, Fraction(60000, 1001))
    assert nv.bitrate == pytest.approx(10e6 * 4 * 2, rel=0.01)
    assert "hvc1" in nv.args and "bt709" in nv.args
    qsv = ve.encoder_args("h264", "h264_qsv", "standard", 1920, 1080, 30)
    assert qsv.args[qsv.args.index("-pix_fmt") + 1] == "nv12"
    assert ve.target_bitrate("h264", "high", 1920, 1080, 30) > ve.target_bitrate("h264", "standard", 1920, 1080, 30)
    assert "Hardware" in ve.encoder_title("h264_videotoolbox") and "Software" in ve.encoder_title("libx264")


def test_probe_handles_missing_program():
    assert ve.probe_encoder("/gibt/es/nicht/ffmpeg", "libx264") is False
