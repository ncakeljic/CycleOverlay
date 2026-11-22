"""Export-Pipeline mit ffmpeg – ohne Qt (Frames kommen hier direkt aus numpy)."""

from __future__ import annotations

import subprocess
import threading
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from cycle_overlay.export.ffmpeg_exporter import (
    ExportCancelled,
    ExportError,
    build_command,
    run_export,
)
from cycle_overlay.export.profiles import PROFILES, estimate_size_bytes, format_bytes, is_available

from .conftest import needs_ffmpeg

W, H = 160, 90


def make_frame(i: int = 0) -> np.ndarray:
    a = np.zeros((H, W, 4), np.uint8)
    a[20:50, 20:60] = (255, 0, 0, 128)  # halbtransparentes Rot (straight alpha)
    a[20:50, 80:120] = (255, 255, 255, 255)  # deckendes Weiß
    a[60:70, 10 + i : 20 + i] = (0, 128, 255, 255)  # bewegtes Element
    return a


def decode_rgba(path: Path, frame: int = 0, decoder: str | None = None) -> np.ndarray:
    dec = ["-c:v", decoder] if decoder else []
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", *dec, "-i", str(path), "-vf", f"select=eq(n\\,{frame})", "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, np.uint8).reshape(H, W, 4)


def test_build_command_prores():
    cmd = build_command("ffmpeg", PROFILES["prores4444"], 1920, 1080, Fraction(30000, 1001), Path("/x/out.mov.part"))
    s = " ".join(cmd)
    assert "-f rawvideo -pix_fmt rgba -s 1920x1080 -framerate 30000/1001 -i -" in s
    assert "-c:v prores_ks -profile:v 4444 -pix_fmt yuva444p10le" in s
    assert "out_color_matrix=bt709" in s and "-colorspace bt709" in s
    assert cmd[-3:] == ["-f", "mov", "/x/out.mov.part"]


def test_output_target():
    assert PROFILES["prores4444"].output_target(Path("a/clip_overlay")) == Path("a/clip_overlay.mov")
    assert PROFILES["prores4444"].output_target(Path("a/x.MOV")) == Path("a/x.MOV")
    assert PROFILES["png_sequence"].output_target(Path("a/seq.mov")) == Path("a/seq")


def test_size_estimate():
    one_min = estimate_size_bytes(PROFILES["prores4444"], 1920, 1080, Fraction(30000, 1001), 1798)
    assert 2.0e9 < one_min < 2.8e9  # ~330 Mbit/s
    uhd = estimate_size_bytes(PROFILES["prores4444"], 3840, 2160, Fraction(30000, 1001), 1798)
    assert uhd == pytest.approx(one_min * 4, rel=0.01)
    assert format_bytes(2_470_000_000) == "2.5 GB"


@needs_ffmpeg
@pytest.mark.parametrize("key", [k for k, p in PROFILES.items() if not p.with_video])
def test_export_keeps_straight_alpha(tmp_path, key):
    profile = PROFILES[key]
    if not is_available(profile):
        pytest.skip(f"Encoder {profile.requires_encoder} nicht vorhanden")
    lossy = key in ("hevc_alpha", "webm_vp9", "cineform")  # CineForm: Wavelet, minimale Abweichungen
    res = run_export(lambda i: make_frame(i).tobytes(), 12, W, H, Fraction(30000, 1001), profile, tmp_path / "clip")
    assert res.n_frames == 12
    if profile.alpha_pix_fmts:
        assert res.pix_fmt in profile.alpha_pix_fmts
    src = res.path / f"frame_000001{profile.frame_ext}" if profile.is_sequence else res.path
    a = decode_rgba(src, decoder=profile.alpha_decoder)
    if lossy and a[..., 3].min() == 255:
        pytest.skip("Diese ffmpeg-Version kann die Alpha-Ebene von HEVC nicht dekodieren (Datei trotzdem erzeugt)")
    assert a[5, 5, 3] <= (8 if lossy else 0), "Hintergrund muss voll transparent sein"
    red = a[35, 40]
    assert abs(int(red[3]) - 128) <= (12 if lossy else 2)
    assert red[0] >= (225 if lossy else 250) and red[1] <= (30 if lossy else 6), f"Farbe abgedunkelt: {red}"
    assert tuple(a[35, 100]) == pytest.approx((255, 255, 255, 255), abs=12 if lossy else 3)
    assert not list(tmp_path.glob("*.part"))


def test_unavailable_profile_is_rejected(tmp_path):
    from dataclasses import replace

    fake = replace(PROFILES["prores4444"], requires_encoder="gibt_es_nicht_enc")
    assert not is_available(fake)
    with pytest.raises(ExportError, match="nicht unterstützt"):
        run_export(lambda i: make_frame().tobytes(), 2, W, H, 30, fake, tmp_path / "x.mov", ffmpeg="ffmpeg")


@needs_ffmpeg
def test_export_frame_count_and_motion(tmp_path):
    res = run_export(lambda i: make_frame(i).tobytes(), 20, W, H, 25, PROFILES["prores4444"], tmp_path / "m.mov")
    a0 = decode_rgba(res.path, 0)
    a10 = decode_rgba(res.path, 10)
    assert a0[65, 15, 3] == 255 and a10[65, 25, 3] == 255 and a10[65, 12, 3] == 0


@needs_ffmpeg
def test_progress_callback(tmp_path):
    seen = []
    run_export(lambda i: make_frame().tobytes(), 5, W, H, 30, PROFILES["qtrle"], tmp_path / "p.mov", progress=seen.append)
    assert [p.frame for p in seen] == [1, 2, 3, 4, 5]
    assert seen[-1].eta_s == 0


@needs_ffmpeg
def test_cancel_cleans_up(tmp_path):
    ev = threading.Event()

    def src(i):
        if i == 3:
            ev.set()
        return make_frame().tobytes()

    with pytest.raises(ExportCancelled):
        run_export(src, 50, W, H, 30, PROFILES["prores4444"], tmp_path / "c.mov", cancel=ev)
    assert list(tmp_path.iterdir()) == []


@needs_ffmpeg
def test_cancel_cleans_up_sequence(tmp_path):
    ev = threading.Event()

    def src(i):
        if i == 3:
            ev.set()
        return make_frame().tobytes()

    with pytest.raises(ExportCancelled):
        run_export(src, 50, W, H, 30, PROFILES["png_sequence"], tmp_path / "seq", cancel=ev)
    assert not (tmp_path / "seq").exists()


@needs_ffmpeg
def test_error_in_frame_source_cleans_up(tmp_path):
    def src(i):
        if i == 2:
            raise RuntimeError("Renderfehler")
        return make_frame().tobytes()

    with pytest.raises(RuntimeError):
        run_export(src, 10, W, H, 30, PROFILES["prores4444"], tmp_path / "e.mov")
    assert list(tmp_path.iterdir()) == []


@needs_ffmpeg
def test_wrong_frame_size_raises(tmp_path):
    with pytest.raises(ExportError):
        run_export(lambda i: b"\x00" * 10, 3, W, H, 30, PROFILES["prores4444"], tmp_path / "w.mov")
    assert list(tmp_path.iterdir()) == []


@needs_ffmpeg
def test_ffmpeg_failure_reports_stderr(tmp_path):
    from dataclasses import replace

    broken = replace(PROFILES["prores4444"], codec_args=("-c:v", "gibtsnicht_codec"))
    with pytest.raises(ExportError) as exc:
        run_export(lambda i: make_frame().tobytes(), 30, W, H, 30, broken, tmp_path / "b.mov")
    assert "gibtsnicht_codec" in str(exc.value) or "ffmpeg" in str(exc.value)
    assert list(tmp_path.iterdir()) == []


def test_missing_target_dir(tmp_path):
    with pytest.raises(ExportError):
        run_export(lambda i: b"", 1, W, H, 30, PROFILES["prores4444"], tmp_path / "nicht" / "da.mov", ffmpeg="ffmpeg")


# --------------------------------------------------------------------------- Export mit Video


def _make_background(path: Path, seconds: float = 1.0) -> Path:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"color=c=0x204060:s={W}x{H}:r=30000/1001:d={seconds}",
         "-f", "lavfi", "-i", f"sine=f=440:d={seconds}",
         "-vf", "scale=out_color_matrix=bt709:out_range=tv",
         "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )
    return path


def _decode_rgb(path: Path, frame: int = 0) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"select=eq(n\\,{frame})", "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, np.uint8).reshape(H, W, 3).astype(int)


def test_build_command_with_video():
    cmd = build_command("ffmpeg", PROFILES["mov_prores422"], 1920, 1080, Fraction(30000, 1001),
                        Path("/x/o.mov.part"), background=Path("/v/clip.mp4"))
    s = " ".join(cmd)
    assert cmd.index("/v/clip.mp4") < cmd.index("-")  # Video ist Eingang 0, Anzeigen Eingang 1
    assert "overlay=0:0" in s and "out_color_matrix=bt709" in s and "format=yuv422p10le[v]" in s
    assert "-map [v] -map 0:a:0?" in s and "-c:a pcm_s16le" in s and "-an" not in cmd
    with pytest.raises(ExportError):
        build_command("ffmpeg", PROFILES["mp4_h264"], 16, 16, 30, Path("x.mp4"))


@needs_ffmpeg
@pytest.mark.parametrize("key", ["mp4_h264", "mp4_hevc", "mov_prores422"])
def test_export_with_video_burns_in_overlay(tmp_path, key):
    from cycle_overlay.export.profiles import available_encoders
    from cycle_overlay.export.video_encoders import CANDIDATES

    profile = PROFILES[key]
    if profile.codec_family and not set(CANDIDATES[profile.codec_family]) & available_encoders("ffmpeg"):
        pytest.skip("kein Encoder dafür vorhanden")
    bg = _make_background(tmp_path / "clip.mp4")
    res = run_export(lambda i: make_frame(i).tobytes(), 29, W, H, Fraction(30000, 1001), profile,
                     tmp_path / "clip_mit_daten", background=bg, ffmpeg="ffmpeg")
    assert res.path.suffix == profile.extension and res.encoder
    a = _decode_rgb(res.path)
    assert np.abs(a[5, 5] - (32, 64, 96)).max() <= 8, "Video muss unter den Anzeigen sichtbar sein"
    assert np.abs(a[35, 100] - 255).max() <= 6, "deckende Anzeige"
    assert np.abs(a[35, 40] - (143, 32, 48)).max() <= 14, f"halbtransparent gemischt: {a[35, 40]}"
    a10 = _decode_rgb(res.path, 10)
    assert a10[65, 25].tolist() != a[65, 25].tolist()  # bewegtes Element folgt den Frames
    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                              str(res.path)], capture_output=True, text=True).stdout.split()
    assert streams.count("audio") == 1, "Ton wird übernommen"
    assert not list(tmp_path.glob("*.part"))


@needs_ffmpeg
def test_export_with_video_refuses_to_overwrite_source(tmp_path):
    bg = _make_background(tmp_path / "clip.mp4")
    with pytest.raises(ExportError, match="Originalvideo"):
        run_export(lambda i: make_frame().tobytes(), 3, W, H, 30, PROFILES["mp4_h264"], tmp_path / "clip.mp4",
                   background=bg, ffmpeg="ffmpeg")
    with pytest.raises(ExportError, match="Video"):
        run_export(lambda i: make_frame().tobytes(), 3, W, H, 30, PROFILES["mp4_h264"], tmp_path / "x.mp4",
                   ffmpeg="ffmpeg")
