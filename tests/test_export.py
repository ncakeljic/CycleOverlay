

def test_output_target_keeps_dots_in_names():
    from pathlib import Path

    from cycle_overlay.export.profiles import PROFILES

    mov, seq = PROFILES["prores4444"], PROFILES["png_sequence"]
    assert mov.output_target(Path("/x/Tour.2026_overlay")).name == "Tour.2026_overlay.mov"
    assert mov.output_target(Path("/x/clip.mp4")).name == "clip.mov"
    assert mov.output_target(Path("/x/clip.mov")).name == "clip.mov"
    assert seq.output_target(Path("/x/Tour.2026_overlay")).name == "Tour.2026_overlay"
    assert seq.output_target(Path("/x/clip.mov")).name == "clip"


def test_more_formats_and_1440p():
    from pathlib import Path

    from cycle_overlay.core.project import SIZE_PRESETS
    from cycle_overlay.export.profiles import PROFILES

    assert ("2560x1440 (1440p QHD)", 2560, 1440) in SIZE_PRESETS
    for key in ("prores4444xq", "cineform", "mov_png", "webm_vp9", "tiff_sequence"):
        assert key in PROFILES
    tif = PROFILES["tiff_sequence"]
    assert tif.is_sequence and tif.frame_pattern == "frame_%06d.tif" and tif.frame_glob == "frame_*.tif"
    assert PROFILES["png_sequence"].frame_pattern == "frame_%06d.png"
    assert PROFILES["webm_vp9"].output_target(Path("a/b.mov")) == Path("a/b.webm")
    assert PROFILES["tiff_sequence"].output_target(Path("a/b.tif")) == Path("a/b")
