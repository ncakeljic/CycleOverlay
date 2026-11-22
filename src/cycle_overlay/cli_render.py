"""Kommandozeilen-Befehle für Rendering und Export (Phase 2).

    cycle-overlay preview fahrt.gpx --video GX010123.MP4 --video-at 0:00:10 --out vorschau.png
    cycle-overlay render  fahrt.gpx --video GX010123.MP4
    cycle-overlay render  fahrt.gpx --start 1:05:00 --duration 30 --fps 29.97 --size 3840x2160 --out abfahrt.mov
"""

from __future__ import annotations

import argparse
import math
import shutil
import sys
import tempfile
import threading
import time
from fractions import Fraction
from pathlib import Path

import numpy as np

from .core.presets import list_presets, load_preset
from .core.processor import frame_times, parse_fps
from .core.units import format_duration
from .export.ffmpeg_exporter import ExportCancelled, ExportError, Progress, run_export
from .export.ffmpeg_locator import find_executable
from .export.profiles import DEFAULT_PROFILE, PROFILES, estimate_size_bytes, format_bytes, is_available
from .platform_utils import run


def parse_size(text: str) -> tuple[int, int]:
    try:
        w, h = (int(v) for v in text.lower().replace("×", "x").split("x"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Ungültige Auflösung {text!r}, erwartet z. B. 1920x1080") from exc
    if w <= 0 or h <= 0 or w > 8192 or h > 8192:
        raise argparse.ArgumentTypeError(f"Ungültige Auflösung {text!r}")
    return w, h


def _require_qt():
    try:
        import PySide6  # noqa: F401
    except ImportError:
        print("Fehler: PySide6 ist nicht installiert. Bitte im Projektordner 'uv sync' ausführen.", file=sys.stderr)
        raise SystemExit(1) from None


class Plan:
    """Was gerendert wird: Track-Zeiten je Frame, Framerate, Auflösung."""

    def __init__(self, track_times: np.ndarray, fps: Fraction, width: int, height: int, video=None, sync=None):
        self.track_times = track_times
        self.fps = fps
        self.width = width
        self.height = height
        self.video = video
        self.sync = sync

    @property
    def n_frames(self) -> int:
        return int(self.track_times.size)


def make_plan(args, tel) -> Plan:
    from .cli import _build_sync

    if getattr(args, "video", None):
        info, _profile, sync = _build_sync(args, tel)
        chk = sync.check(tel.duration)
        if chk.status != "ok":
            print(f"Warnung: {chk.message}", file=sys.stderr)
        w, h = args.size or (info.width, info.height)
        return Plan(sync.frame_track_times(), info.fps, w, h, info, sync)
    if args.start is None or args.duration is None:
        raise SystemExit("Fehler: Ohne --video werden --start und --duration benötigt.")
    fps = parse_fps(args.fps)
    n = int(math.floor(args.duration * fps + 1e-9))
    w, h = args.size or (1920, 1080)
    return Plan(frame_times(args.start, n, fps), fps, w, h)


def _renderer(args, tel, plan: Plan):
    _require_qt()
    from .rendering.canvas_renderer import CanvasRenderer
    from .rendering.values import FrameValues

    layout = load_preset(args.preset)
    values = FrameValues(tel, plan.track_times, units=args.units, tz=args.tz)
    r = CanvasRenderer(layout, values, plan.width, plan.height)
    if r.skipped:
        print(f"Hinweis: ausgeblendet, da nicht in der Datei: {', '.join(r.skipped)}", file=sys.stderr)
    return r


# --------------------------------------------------------------------------- preview


def _video_frame(video: Path, t: float, w: int, h: int, tmpdir: Path):
    from PySide6.QtGui import QImage

    exe = find_executable("ffmpeg")
    if exe is None:
        return None
    out = tmpdir / "bg.png"
    res = run([exe, "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1",
               "-vf", f"scale={w}:{h}", out], timeout=120)
    if res.returncode != 0 or not out.exists():
        print(f"Hinweis: Videobild konnte nicht gelesen werden: {res.stderr.strip()}", file=sys.stderr)
        return None
    img = QImage(str(out))
    return None if img.isNull() else img


def cmd_preview(args) -> int:
    from .cli import _load

    tel = _load(args.gpx, args)
    if args.video:
        args.start = args.duration = None
        plan = make_plan(args, tel)
        i = min(int(round(args.video_at * float(plan.fps))), plan.n_frames - 1)
        plan = Plan(plan.track_times[i : i + 1], plan.fps, plan.width, plan.height, plan.video, plan.sync)
    else:
        plan = Plan(np.array([args.at]), Fraction(30), *(args.size or (1920, 1080)))
    r = _renderer(args, tel, plan)
    out = Path(args.out)
    if args.transparent:
        img = r.render(0)
    else:
        bg = None
        if args.video:
            with tempfile.TemporaryDirectory() as td:
                bg = _video_frame(args.video, args.video_at, plan.width, plan.height, Path(td))
                img = r.render_preview(0, background=bg)
        else:
            img = r.render_preview(0)
    if not img.save(str(out)):
        print(f"Fehler: Bild konnte nicht gespeichert werden: {out}", file=sys.stderr)
        return 1
    print(f"Vorschau ({plan.width}x{plan.height}, Track-Zeit {format_duration(float(plan.track_times[0]))}) gespeichert: {out}")
    return 0


# --------------------------------------------------------------------------- render


def cmd_render(args) -> int:
    from .cli import _load

    tel = _load(args.gpx, args)
    videos = args.video or [None]
    if len(videos) > 1 and args.out and Path(args.out).suffix:
        print("Fehler: Bei mehreren Videos ist --out ein Ordner (ohne Endung).", file=sys.stderr)
        return 1
    codes = []
    for k, v in enumerate(videos):
        if len(videos) > 1:
            print(f"\n=== Clip {k + 1}/{len(videos)}: {v.name} ===")
        one = argparse.Namespace(**vars(args))
        one.video = v
        if len(videos) > 1 and getattr(args, "time_source", "auto") == "auto":
            one.time_source = "metadata"  # im Stapel fremde Clips erkennen statt an den Trackbeginn legen
        if len(videos) > 1:
            folder = Path(args.out) if args.out else v.parent
            folder.mkdir(parents=True, exist_ok=True)
            one.out = folder / f"{v.stem}_overlay"
        code = _render_one(one, tel)
        codes.append(code)
        if code == 130:
            break
    if len(videos) > 1:
        ok = sum(1 for c in codes if c == 0)
        print(f"\n{ok} von {len(videos)} Clips exportiert.")
    return max(codes) if codes else 1


def _render_one(args, tel) -> int:
    plan = make_plan(args, tel)
    if plan.sync is not None and plan.sync.check(tel.duration).status == "outside":
        print("Übersprungen: Der Clip liegt außerhalb der Fahrt.", file=sys.stderr)
        return 2
    profile = PROFILES[args.profile]
    if not is_available(profile):
        print(f"Fehler: {profile.title} ist mit dieser ffmpeg-Version nicht verfügbar.", file=sys.stderr)
        return 1
    if profile.with_video and plan.video is None:
        print(f"Fehler: {profile.title} exportiert das Video mit – bitte --video angeben.", file=sys.stderr)
        return 1
    if args.out:
        out = Path(args.out)
    elif plan.video is not None:
        out = plan.video.path.with_name(plan.video.path.stem + ("_mit_daten" if profile.with_video else "_overlay"))
    else:
        print("Fehler: Ohne --video bitte --out angeben.", file=sys.stderr)
        return 1
    target = profile.output_target(out)
    if target.exists() and not args.overwrite and not profile.is_sequence:
        print(f"Fehler: {target} existiert bereits (mit --overwrite überschreiben).", file=sys.stderr)
        return 1

    est = estimate_size_bytes(profile, plan.width, plan.height, plan.fps, plan.n_frames)
    print(f"Export:      {profile.title}")
    print(f"Ziel:        {target}")
    print(f"Auflösung:   {plan.width}x{plan.height} @ {plan.fps} FPS ({float(plan.fps):.3f})")
    print(f"Frames:      {plan.n_frames} ({format_duration(plan.n_frames / float(plan.fps))})")
    print(f"Track-Zeit:  {format_duration(float(plan.track_times[0]))} bis {format_duration(float(plan.track_times[-1]))}")
    print(f"Größe ca.:   bis {format_bytes(est)}")
    try:
        free = shutil.disk_usage(target.parent).free
        if free < est:
            print(f"Warnung: Nur {format_bytes(free)} frei auf dem Ziellaufwerk.", file=sys.stderr)
    except OSError:
        pass

    r = _renderer(args, tel, plan)
    cancel = threading.Event()
    last = [0.0]

    def progress(pr: Progress) -> None:
        now = time.perf_counter()
        if now - last[0] >= 0.5 or pr.frame == pr.total:
            last[0] = now
            pct = pr.frame / pr.total * 100
            sys.stderr.write(f"\r  Frame {pr.frame}/{pr.total} ({pct:5.1f} %)  {pr.fps:5.1f} FPS  Rest {format_duration(pr.eta_s)}   ")
            sys.stderr.flush()

    try:
        res = run_export(r.frame_bytes, plan.n_frames, plan.width, plan.height, plan.fps, profile, target,
                         background=plan.video.path if profile.with_video and plan.video is not None else None,
                         quality=args.quality,
                         progress=progress, cancel=cancel)
    except KeyboardInterrupt:
        sys.stderr.write("\n")
        print("Abgebrochen – unvollständige Dateien wurden entfernt.", file=sys.stderr)
        return 130
    except ExportCancelled:
        return 130
    except ExportError as exc:
        sys.stderr.write("\n")
        print(f"Fehler beim Export: {exc}", file=sys.stderr)
        return 1
    sys.stderr.write("\n")
    print(f"Fertig in {format_duration(res.seconds)} ({res.n_frames / max(res.seconds, 1e-9):.1f} FPS): {res.path}")
    alpha = "mit Alpha" if res.pix_fmt in profile.alpha_pix_fmts else "Alpha-Kanal"
    print(f"Geprüft:     Codec {res.codec}, Pixelformat {res.pix_fmt} ({alpha}), {format_bytes(res.size_bytes)}")
    return 0


def cmd_presets(args) -> int:
    for name, path in list_presets().items():
        print(f"{name:<24} {path}")
    return 0


# --------------------------------------------------------------------------- Parser


def add_commands(sub, common, sync_opts) -> None:
    render_opts = argparse.ArgumentParser(add_help=False)
    render_opts.add_argument("--preset", default=None, help="Layout-Preset (Name oder .json-Datei), Standard: rennrad_minimal")
    render_opts.add_argument("--size", type=parse_size, default=None, help="Auflösung, z. B. 3840x2160 (Standard: wie Video bzw. 1920x1080)")

    sp = sub.add_parser("preview", parents=[common, sync_opts, render_opts], help="Ein Frame als PNG-Vorschau speichern")
    sp.add_argument("gpx", type=Path)
    sp.add_argument("--at", type=_clock, default=0.0, help="ohne Video: Track-Zeit HH:MM:SS")
    sp.add_argument("--video", type=Path, help="Referenzvideo – das Videobild wird als Hintergrund verwendet")
    sp.add_argument("--video-at", type=_clock, default=0.0, help="mit Video: Zeit im Video HH:MM:SS")
    sp.add_argument("--transparent", action="store_true", help="PNG mit transparentem Hintergrund statt Schachbrett/Video")
    sp.add_argument("--out", type=Path, default=Path("vorschau.png"))
    sp.set_defaults(func=cmd_preview)

    sp = sub.add_parser("render", parents=[common, sync_opts, render_opts], help="Overlay-Video mit Alpha-Kanal exportieren")
    sp.add_argument("gpx", type=Path)
    sp.add_argument("--video", type=Path, nargs="+",
                    help="Referenzvideo(s) – bestimmen Zeitraum, Framerate und Auflösung; mehrere = Stapel-Export")
    sp.add_argument("--start", type=_clock, help="ohne Video: Start als Track-Zeit HH:MM:SS")
    sp.add_argument("--duration", type=float, help="ohne Video: Dauer in Sekunden")
    sp.add_argument("--fps", default="30", help="ohne Video: Framerate, z. B. 30, 29.97, 60000/1001")
    sp.add_argument("--profile", choices=list(PROFILES), default=DEFAULT_PROFILE,
                    help="Exportformat; mp4_h264, mp4_hevc und mov_prores422 brennen die Anzeigen ins Video ein")
    sp.add_argument("--quality", choices=["standard", "high"], default="standard",
                    help="Qualität für mp4_h264/mp4_hevc")
    sp.add_argument("--out", type=Path,
                    help="Zieldatei (Standard: <Video>_overlay.mov neben dem Video); bei mehreren Videos ein Ordner")
    sp.add_argument("--overwrite", action="store_true", help="vorhandene Datei überschreiben")
    sp.set_defaults(func=cmd_render)

    sp = sub.add_parser("presets", help="verfügbare Layout-Presets anzeigen")
    sp.set_defaults(func=cmd_presets)


def _clock(text: str) -> float:
    from .cli import parse_clock

    return parse_clock(text)
