import subprocess

import numpy as np
import pytest

from cycle_overlay.core.sync_timeline import (
    OffsetDrag,
    OffsetHistory,
    TimeAxis,
    choose_step,
    minmax_envelope,
    snap_to_frame,
    tick_label,
    ticks,
    timeline_bounds,
)
from cycle_overlay.core.video_motion import (
    analyze_video,
    motion_energy,
    normalized_xcorr,
    suggest_offset,
    thumb_interval,
)

from .conftest import needs_ffmpeg

FPS = 30000 / 1001


def axis(width=1000, lo=-100.0, hi=36000.0):
    a = TimeAxis(width=width, fps=FPS)
    a.set_bounds(lo, hi)
    a.show_all()
    return a


def test_pixel_time_roundtrip_and_show_all():
    a = axis()
    assert a.start <= -100 + 1e-6 and a.end >= 36000 - 1e-6
    for x in (0, 1, 333.3, 999):
        assert a.x_of(a.t_of(x)) == pytest.approx(x)


def test_zoom_keeps_anchor_fixed():
    a = axis()
    a.zoom(50, anchor_x=700)
    t = a.t_of(700)
    for f in (3, 0.5, 20, 1000):
        a.zoom(f, anchor_x=700)
        assert a.t_of(700) == pytest.approx(t, abs=a.spp)


def test_zoom_limits():
    a = axis()
    a.zoom(1e12)
    assert a.spp == pytest.approx(a.min_spp)
    assert 1 / FPS / a.spp == pytest.approx(14)  # ein Frame ~14 px
    assert a.zoom_level == pytest.approx(1.0)
    a.zoom(1e-12)
    assert a.zoom_level == pytest.approx(0.0)
    a.set_zoom_level(0.5)
    assert 0.45 < a.zoom_level < 0.55


def test_scroll_is_clamped():
    a = axis()
    a.zoom(100)
    a.scroll_px(-1e9)
    assert a.start == pytest.approx(a.lo)
    a.scroll_px(1e9)
    assert a.end == pytest.approx(a.hi)


def test_show_range_and_width_change():
    a = axis()
    a.show(1000, 1427, pad=0.1)
    assert a.start <= 1000 and a.end >= 1427 and a.span < 600
    mid = a.start + a.span / 2
    a.set_width(500)
    assert a.start + a.span / 2 == pytest.approx(mid)


def test_bounds_include_far_away_clip():
    lo, hi = timeline_bounds(36000, -5 * 365 * 86400, 427)
    assert lo < -5 * 365 * 86400 and hi > 36000


def test_tick_steps_adapt():
    a = axis()
    tk = ticks(a)
    assert tk.step >= 3600 and not tk.frames
    a.zoom(1e12)
    tk = ticks(a)
    assert tk.frames
    assert tk.step * FPS == pytest.approx(round(tk.step * FPS))
    gaps = np.diff(a.x_of(np.array(tk.major)))
    assert gaps.min() >= 89
    a.show(100, 160)
    tk = ticks(a)
    assert 1 <= tk.step <= 15 and not tk.frames


def test_choose_step_monotonic():
    prev = 0
    for spp in np.geomspace(1e-4, 200, 40):
        s, _ = choose_step(float(spp), FPS)
        assert s >= prev - 1e-12
        prev = s


def test_tick_labels():
    assert tick_label(3723, 60, FPS, False) == "1:02:03"
    assert tick_label(3720, 60, FPS, False) == "1:02"
    assert tick_label(-30, 10, FPS, False) == "-0:00:30"
    assert tick_label(10 + 5 / FPS, 1 / FPS, FPS, True) == "0:00:10.05"


def test_drag_snaps_to_frames_and_fine_mode():
    d = OffsetDrag(start_offset=12.0, start_x=100, fps=FPS)
    off = d.offset_at(150, spp=0.1)
    assert off == pytest.approx(snap_to_frame(17.0, FPS))
    assert (off * FPS) == pytest.approx(round(off * FPS))
    # Shift ab x=150: 10x feiner, ohne Sprung
    off2 = d.offset_at(150, spp=0.1, fine=True)
    assert off2 == pytest.approx(off, abs=1 / FPS)
    off3 = d.offset_at(250, spp=0.1, fine=True)
    assert off3 == pytest.approx(snap_to_frame(18.0, FPS))
    off4 = d.offset_at(260, spp=0.1, fine=False)
    assert off4 == pytest.approx(snap_to_frame(19.0, FPS))


def test_history():
    h = OffsetHistory()
    h.record(0, 1)
    h.record(1, 1)  # keine Änderung
    h.record(1, 2.5)
    assert h.undo(2.5) == 1
    assert h.undo(1) == 0
    assert h.undo(0) is None
    assert h.redo(0) == 1
    h.record(1, 7)
    assert not h.can_redo


def test_envelope_reduces_long_curves():
    t = np.arange(0, 36000.0)
    v = np.sin(t / 100)
    a = axis()
    x, lo, hi = minmax_envelope(t, v, a)
    assert x.size <= a.width + 2
    assert lo.min() == pytest.approx(-1, abs=1e-3) and hi.max() == pytest.approx(1, abs=1e-3)
    a.show(100, 120)
    x, lo, hi = minmax_envelope(t, v, a)
    assert np.array_equal(lo, hi) and 20 <= x.size <= 24


def _ride(seed=1, duration=3 * 3600):
    """Fahrt mit Ampelstopps und Tempowechseln (1 Hz)."""
    rng = np.random.default_rng(seed)
    t = np.arange(0, duration, 1.0)
    v = np.full(t.size, 8.0)
    pos = 0
    while pos < t.size:
        seg = int(rng.integers(30, 400))
        v[pos:pos + seg] = rng.uniform(4, 14)
        pos += seg
        stop = int(rng.integers(5, 60))
        if rng.random() < 0.5:
            v[pos:pos + stop] = 0
        pos += stop
    v = np.convolve(v, np.ones(5) / 5, mode="same")
    return t, v


def test_xcorr_finds_shift():
    rng = np.random.default_rng(0)
    b = rng.normal(size=2000)
    a = b[700:900].copy()
    c = normalized_xcorr(a, b)
    assert int(np.argmax(c)) == 700 and c.max() == pytest.approx(1.0)


@pytest.mark.parametrize("true_start", [123.4, 4321.0, 9876.6])
def test_suggest_offset_recovers_start(true_start):
    t, v = _ride()
    rng = np.random.default_rng(3)
    clip = 427.0
    mt = np.arange(0, clip, 0.2)
    speed_at = np.interp(true_start + mt, t, v)
    motion = np.clip(0.08 * speed_at ** 0.7 + rng.normal(0, 0.03, mt.size), 0, None)
    base = -1.0e8  # Aufnahmezeit völlig falsch
    s = suggest_offset(mt, motion, t, v, base_offset_s=base, fps=FPS)
    assert s is not None and s.usable
    assert s.track_start == pytest.approx(true_start, abs=0.5)
    assert base + s.offset_s == pytest.approx(true_start, abs=0.5)
    assert s.offset_s * FPS == pytest.approx(round(s.offset_s * FPS), abs=1e-6)


def test_suggest_offset_low_confidence_without_features():
    t = np.arange(0, 3600.0)
    v = np.full(t.size, 8.0) + 0.01 * np.sin(t)
    mt = np.arange(0, 120, 0.2)
    motion = np.random.default_rng(1).uniform(0.4, 0.5, mt.size)
    s = suggest_offset(mt, motion, t, v, 0.0, FPS)
    assert s is None or not s.usable


def test_motion_energy():
    f = np.zeros((4, 10, 10), np.uint8)
    f[2] = 100
    e = motion_energy(f)
    assert e.tolist() == [0, 0, 100, 100]


def test_thumb_interval():
    assert thumb_interval(10) == 1.0
    assert thumb_interval(427) == pytest.approx(427 / 160)


@needs_ffmpeg
def test_analyze_video(tmp_path):
    clip = tmp_path / "moving.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30",
                    "-f", "lavfi", "-i", "color=c=gray:s=320x180:r=30", "-filter_complex",
                    "[0:v]trim=0:2,setpts=PTS-STARTPTS,rotate=PI*t:c=black[a];[1:v]trim=0:2,setpts=PTS-STARTPTS[b];[a][b]concat=n=2[v]",
                    "-map", "[v]", "-c:v", "mpeg4", str(clip)], check=True)
    seen = []
    res = analyze_video(clip, 320, 180, 4.0, cache_dir=tmp_path / "cache", progress=seen.append)
    assert 38 <= res.motion.size <= 41
    assert res.motion_t[1] == pytest.approx(0.1)
    assert res.yaw is not None and res.yaw.size == res.motion.size
    moving, still = res.motion[3:18].mean(), res.motion[24:].mean()
    assert moving > 0.3 and still < 0.05
    assert res.thumbs.shape[1:] == (48, 86, 3) and res.thumbs.shape[0] >= 3
    assert seen[-1] == 1.0
    # zweiter Aufruf aus dem Zwischenspeicher
    res2 = analyze_video(clip, 320, 180, 4.0, cache_dir=tmp_path / "cache")
    assert np.array_equal(res2.motion, res.motion)


@needs_ffmpeg
def test_analyze_video_cancel(tmp_path):
    import threading

    from cycle_overlay.core.video_motion import AnalysisCancelled

    clip = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30",
                    "-t", "3", "-c:v", "mpeg4", str(clip)], check=True)
    ev = threading.Event()
    ev.set()
    with pytest.raises(AnalysisCancelled):
        analyze_video(clip, 320, 180, 3.0, cancel=ev)


def test_horizontal_shift_subpixel():
    from cycle_overlay.core.video_motion import horizontal_shift

    rng = np.random.default_rng(5)
    wide = rng.normal(size=(30, 400))
    wide = np.cumsum(np.cumsum(wide, axis=0), axis=1)  # glatte, natürliche Struktur
    wide = (wide - wide.min()) / np.ptp(wide) * 255
    a = wide[:, 100:228]
    for shift in (3, -5, 0):
        b = wide[:, 100 + shift: 228 + shift]
        dx, sharp = horizontal_shift(a, b)
        assert dx == pytest.approx(shift, abs=0.35)
        assert sharp > 0.05


def _curvy_ride(seed=2, duration=2 * 3600):
    """Fahrt ohne Stopps (konstantes Tempo) mit vielen Kurven: lat/lon/Distanz/Tempo bei 1 Hz."""
    rng = np.random.default_rng(seed)
    t = np.arange(0, duration, 1.0)
    v = np.full(t.size, 8.0) + rng.normal(0, 0.05, t.size)
    turn = np.zeros(t.size)
    pos = 0
    while pos < t.size:
        straight = int(rng.integers(10, 90))
        pos += straight
        dur = int(rng.integers(4, 20))
        turn[pos:pos + dur] = rng.uniform(-25, 25)
        pos += dur
    heading = np.radians(np.cumsum(turn))
    x = np.cumsum(v * np.sin(heading))
    y = np.cumsum(v * np.cos(heading))
    lat = 48.0 + y / 111_195.0
    lon = 9.0 + x / (111_195.0 * np.cos(np.radians(48.0)))
    dist = np.r_[0, np.cumsum(np.hypot(np.diff(x), np.diff(y)))]
    return t, lat, lon, dist, v, turn


def test_turn_rate_from_track():
    from cycle_overlay.core.video_motion import track_turn_rate

    t, lat, lon, dist, v, turn = _curvy_ride()
    rate = track_turn_rate(t, lat, lon, dist, v)
    inner = slice(100, -100)
    r = np.corrcoef(rate[inner], np.convolve(turn, np.ones(3) / 3, "same")[inner])[0, 1]
    assert r > 0.8


@pytest.mark.parametrize("true_start,sign", [(777.3, -1), (4100.0, 1)])
def test_suggest_by_curves_without_stops(true_start, sign):
    from cycle_overlay.core.video_motion import suggest_offsets, track_turn_rate

    t, lat, lon, dist, v, turn = _curvy_ride()
    rate = track_turn_rate(t, lat, lon, dist, v)
    rng = np.random.default_rng(9)
    mt = np.arange(0, 300, 0.1)
    # wahre Drehrate am Messpunkt: Richtung im Punkt = Mittel der angrenzenden Abschnitte
    h = np.cumsum(turn)
    h_pt = (h + np.r_[h[1:], h[-1]]) / 2
    true_turn = np.interp(true_start + mt, t, np.gradient(h_pt, t))
    yaw = sign * 0.004 * true_turn + rng.normal(0, 0.02, mt.size)  # Bildbreiten/s, verrauscht
    motion = 0.5 + rng.normal(0, 0.05, mt.size)  # ohne Stopps: kaum Information
    res = suggest_offsets(mt, motion, t, v, base_offset_s=0.0, fps=FPS, yaw=yaw, turn_rate=rate)
    assert res and res[0].track_start == pytest.approx(true_start, abs=0.6)
    assert res[0].usable and res[0].turn_score > 0.5
    assert 1 <= len(res) <= 5 and [s.rank for s in res] == list(range(1, len(res) + 1))
    assert all(abs(s.track_start - res[0].track_start) > 14 for s in res[1:])
    assert "Kurven" in res[0].basis


def test_speed_only_still_works_with_new_api():
    from cycle_overlay.core.video_motion import suggest_offsets

    t, v = _ride()
    mt = np.arange(0, 427, 0.1)
    motion = 0.08 * np.interp(2000 + mt, t, v) ** 0.7
    res = suggest_offsets(mt, motion, t, v, 0.0, FPS)
    assert res[0].track_start == pytest.approx(2000, abs=0.5) and res[0].turn_score is None


@needs_ffmpeg
def test_analyze_detects_pan_direction(tmp_path):
    clip = tmp_path / "pan.mp4"
    # Ausschnitt wandert nach rechts über ein breites Bild = Kamera schwenkt nach rechts (2 s), dann still
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x180:rate=30",
                    "-vf", "crop=320:180:x='if(lt(t,2),t*200,400)':y=0", "-t", "4", "-c:v", "mpeg4",
                    "-q:v", "3", str(clip)], check=True)
    res = analyze_video(clip, 320, 180, 4.0)
    panning = res.yaw[3:18]
    still = res.yaw[24:]
    # 200 px/s bei 320 px Breite = 0,625 Bildbreiten/s
    assert np.median(panning) == pytest.approx(0.625, rel=0.25)
    assert abs(np.median(still)) < 0.05


def _ride_with_long_stops(seed=4):
    """Fahrt mit langen Stillständen (z. B. Pausen an Verpflegungsstellen)."""
    t, v = _ride(seed)
    v = v.copy()
    for a in (1500, 4000, 7000):
        v[a:a + 900] = 0.0  # je 15 min Stillstand
    return t, v


def test_suggestions_never_land_on_standstill():
    from cycle_overlay.core.video_motion import suggest_offsets

    t, v = _ride_with_long_stops()
    rng = np.random.default_rng(1)
    mt = np.arange(0, 420, 0.1)
    # Video ohne erkennbare Tempowechsel (gleichmäßig verrauschte Bewegung) – früher passte das oft
    # „zufällig“ am besten zu einem Stillstand mit Anfahren am Rand
    motion = 0.5 + rng.normal(0, 0.08, mt.size)
    moving = v >= 1.0
    res = suggest_offsets(mt, motion, t, v, 0.0, FPS)
    assert res
    for s in res:
        i0, i1 = int(s.track_start), int(s.track_start + 420)
        still = 1 - moving[max(i0, 0):i1].mean()
        assert still <= 0.10 + 1e-6 and s.still_fraction <= 0.10 + 1e-6


def test_true_position_with_stop_in_clip_is_still_found():
    """Steht man im Clip selbst an der Ampel, darf der Stillstand entsprechend länger sein."""
    from cycle_overlay.core.video_motion import suggest_offsets

    t, v = _ride_with_long_stops()
    true_start = 4000 - 300  # Clip: 5 min fahren, dann 2 min stehen (Anfang der Pause)
    mt = np.arange(0, 420, 0.1)
    rng = np.random.default_rng(2)
    motion = np.clip(0.08 * np.interp(true_start + mt, t, v) ** 0.7 + rng.normal(0, 0.01, mt.size), 0, None)
    res = suggest_offsets(mt, motion, t, v, 0.0, FPS)
    assert res[0].track_start == pytest.approx(true_start, abs=0.6)
    assert 0.2 < res[0].still_fraction < 0.35


def test_no_suggestion_when_everything_is_standstill():
    from cycle_overlay.core.video_motion import suggest_offsets

    t = np.arange(0, 3600.0)
    v = np.zeros(t.size)
    v[1000:1100] = 5.0
    mt = np.arange(0, 300, 0.1)
    motion = 0.5 + np.random.default_rng(3).normal(0, 0.1, mt.size)
    assert suggest_offsets(mt, motion, t, v, 0.0, FPS) == []


def test_match_at_is_high_at_truth_and_low_elsewhere():
    from cycle_overlay.core.video_motion import match_at, suggest_offsets

    t, v = _ride()
    mt = np.arange(0, 427, 0.1)
    rng = np.random.default_rng(7)
    true_start = 3333.0
    motion = np.clip(0.08 * np.interp(true_start + mt, t, v) ** 0.7 + rng.normal(0, 0.01, mt.size), 0, None)
    good = match_at(true_start, mt, motion, t, v)
    bad = match_at(true_start + 900, mt, motion, t, v)
    assert good.percent > 80 and bad.percent < good.percent - 30
    assert "Tempo" in good.text()
    s = suggest_offsets(mt, motion, t, v, 0.0, FPS)[0]
    assert s.score == pytest.approx(good.combined, abs=0.08)
    assert match_at(-5000, mt, motion, t, v) is None  # Clip außerhalb der Fahrt


@needs_ffmpeg
def test_syncdiag_cli(tmp_path, capsys):
    from cycle_overlay.cli import main

    from .conftest import SAMPLE_GPX, make_clip

    clip = make_clip(tmp_path / "GX010001.MP4", creation_time="2026-06-14T10:05:00.000000Z", handler="GoPro AVC",
                     seconds=2.0, size="320x180")
    out = tmp_path / "diag.npz"
    code = main(["syncdiag", str(SAMPLE_GPX), str(clip), "--tz", "Europe/Berlin", "--offset", "0:00:01",
                 "--out", str(out)])
    assert code == 0 and out.exists()
    text = capsys.readouterr().out
    assert "Richtiger Versatz +1.000 s" in text
    with np.load(out) as d:
        assert d["motion"].size == d["yaw"].size and np.isfinite(d["true_start"])


def test_real_insta360_clip_found_by_wind_noise():
    """Echter Clip (Insta360, stabilisiert): Bild-Signale tragen nicht, der Fahrtwind im Ton schon.

    Daten: Hügeltour 26, richtiger Versatz vom Nutzer bestätigt (Track 2:29:32.430).
    """
    from pathlib import Path

    from cycle_overlay.core.video_motion import match_at, suggest_offsets

    d = np.load(Path(__file__).parent / "fixtures" / "insta360_tour_sync.npz")
    ts = float(d["true_start"])
    args = (d["motion_t"].astype(float), d["motion"].astype(float), d["track_t"].astype(float),
            d["speed"].astype(float))
    kw = dict(yaw=d["yaw"].astype(float), turn_rate=d["turn"].astype(float), track_moving=d["moving"])
    res = suggest_offsets(*args, 0.0, FPS, wind=d["wind"].astype(float), **kw)
    assert res[0].track_start == pytest.approx(ts, abs=1.0)
    assert res[0].wind_score is not None and res[0].speed_score is None  # automatisch nur der Ton
    good = match_at(ts, *args, wind=d["wind"].astype(float), **kw)
    assert good.percent > 55 and good.wind is not None
    # nur Bild: findet die Stelle bei dieser Kamera nicht (dokumentiert, warum der Ton gebraucht wird)
    video = suggest_offsets(*args, 0.0, FPS, wind=d["wind"].astype(float), signals="video", **kw)
    assert all(abs(s.track_start - ts) > 5 for s in video)


@needs_ffmpeg
def test_audio_wind_follows_loudness(tmp_path):
    from cycle_overlay.core.video_motion import audio_wind

    clip = tmp_path / "wind.mp4"
    # 3 s leises, 3 s lautes, 3 s leises Rauschen (wie Fahrtwind bei wenig/viel Tempo)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=160x90:r=30:d=9",
                    "-f", "lavfi", "-i", "anoisesrc=d=9:c=brown:a=0.5",
                    "-af", "volume='if(between(t,3,6),1,0.05)':eval=frame", "-shortest",
                    "-c:v", "mpeg4", "-c:a", "aac", str(clip)], check=True)
    t = np.arange(0, 9, 0.1)
    w = audio_wind(clip, t)
    assert w is not None and w.size == t.size
    assert w[35:55].mean() - w[5:25].mean() > 1.5  # > 15 dB lauter
    # Video ohne Ton -> None
    silent = tmp_path / "stumm.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=160x90:r=30:d=2",
                    "-c:v", "mpeg4", str(silent)], check=True)
    assert audio_wind(silent, np.arange(0, 2, 0.1)) is None


def test_signal_mode_validation():
    from cycle_overlay.core.video_motion import suggest_offsets

    t, v = _ride()
    with pytest.raises(ValueError):
        suggest_offsets(np.arange(0, 100, 0.1), np.ones(1000), t, v, 0.0, FPS, signals="irgendwas")
