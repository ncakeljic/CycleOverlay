"""Kommandozeile (bis die GUI in Phase 3 kommt). Render-Befehle: siehe cli_render.py.

Beispiele::

    cycle-overlay check
    cycle-overlay info fahrt.gpx
    cycle-overlay values fahrt.gpx --at 0:10:00 --at 0:42:30
    cycle-overlay sync fahrt.gpx GX010123.MP4 --camera gopro --tz Europe/Berlin
    cycle-overlay frames fahrt.gpx --video GX010123.MP4 --out werte.csv
    cycle-overlay frames fahrt.gpx --start 0:10:00 --duration 30 --fps 30 --out werte.csv
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from . import __version__
from .app_paths import user_log_dir
from .core.gpx_parser import GPXParseError
from .core.track_loader import load_track
from .core.models import Telemetry
from .core.processor import ProcessingConfig, frame_times, parse_fps, process
from .core.settings import AppSettings, settings_file
from .core.sync import CAMERA_PROFILES, SyncError, VideoSync, get_profile
from .core.units import CHANNELS, convert, format_duration, format_value, unit_for
from .core.video_probe import VideoProbeError, probe
from .export.ffmpeg_locator import install_hint, locate
from .logging_setup import setup_logging

DISPLAY_CHANNELS = [c for c in CHANNELS]


# --------------------------------------------------------------------------- Hilfen


def parse_clock(text: str) -> float:
    """'1:02:03.5', '02:03' oder '75.5' -> Sekunden."""
    parts = text.strip().split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Ungültige Zeitangabe: {text!r} (erwartet HH:MM:SS)") from exc
    if len(nums) > 3:
        raise argparse.ArgumentTypeError(f"Ungültige Zeitangabe: {text!r}")
    sec = 0.0
    for n in nums:
        sec = sec * 60 + n
    return sec


def _tz(name: str | None):
    return ZoneInfo(name) if name else None


def _fmt_dt(dt: datetime, tz_name: str | None) -> str:
    local = dt.astimezone(_tz(tz_name)) if tz_name else dt.astimezone()
    return f"{dt.astimezone(timezone.utc):%d.%m.%Y %H:%M:%S} UTC  ({local:%H:%M:%S} {local.tzname()})"


def _load(path: Path, args) -> Telemetry:
    return _load_raw(path, args)[1]


def _load_raw(path: Path, args):
    raw = load_track(path)
    cfg = ProcessingConfig(pause_mode=args.pause_mode)
    return raw, process(raw, cfg)


def _num(v: float, d: int) -> str:
    return "--" if v != v else f"{v:.{d}f}"


# --------------------------------------------------------------------------- Befehle


def cmd_check(args) -> int:
    settings = AppSettings.load()
    st = locate(settings.ffmpeg_path, settings.ffprobe_path)
    print(f"CycleOverlay {__version__}")
    print(f"ffmpeg:          {st.ffmpeg or 'NICHT GEFUNDEN'}")
    print(f"ffprobe:         {st.ffprobe or 'NICHT GEFUNDEN'}")
    print(f"ffmpeg-Version:  {st.version or '-'}")
    print(f"ProRes (prores_ks): {'ja' if st.has_prores_ks else 'nein'}")
    print(f"Einstellungen:   {settings_file()}")
    print(f"Log-Ordner:      {user_log_dir()}")
    if not st.ok:
        for e in st.errors:
            print(f"  ! {e}")
        print(f"  {install_hint()}")
        return 1
    print("Alles bereit.")
    return 0


def cmd_info(args) -> int:
    raw, tel = _load_raw(args.gpx, args)
    raw_n = raw.n_points
    u = args.units
    s = tel.stats
    print(f"Datei:        {args.gpx.name}")
    print(f"Start:        {_fmt_dt(tel.start_time, args.tz)}")
    print(f"Ende:         {_fmt_dt(tel.end_time, args.tz)}")
    print(f"Dauer:        {format_duration(tel.duration)}  (Fahrzeit {format_duration(tel.channels['moving_time'][-1])})")
    print(f"Distanz:      {format_value('distance', tel.channels['distance'][-1], u)}")
    if tel.available["elevation_gain"]:
        print(f"Höhenmeter:   {format_value('elevation_gain', tel.channels['elevation_gain'][-1], u)}")
    print(f"Trackpunkte:  {raw_n}   Pausen: {tel.n_pauses}   Geschwindigkeit aus: {'Gerät' if tel.speed_source == 'device' else 'GPS-Positionen'}")
    print()
    print(f"{'Signal':<18}{'da?':<6}{'Abdeckung':>10}{'Min':>10}{'Ø':>10}{'Max':>10}  Einheit")
    print("-" * 72)
    for ch in DISPLAY_CHANNELS:
        if ch in ("elapsed_time", "moving_time"):
            continue
        info = CHANNELS[ch]
        st = s[ch]
        avail = tel.available[ch]
        unit = unit_for(ch, u)
        if avail:
            mn, _ = convert(ch, st.min, u)
            av, _ = convert(ch, st.mean, u)
            mx, _ = convert(ch, st.max, u)
            d = info.decimals
            if ch in ("distance", "elevation_gain"):  # kumulierte Werte: nur Endwert sinnvoll
                mn = av = math.nan
            print(f"{info.title:<18}{'ja':<6}{st.coverage * 100:>9.0f}%{_num(mn, d):>10}{_num(av, d):>10}{_num(mx, d):>10}  {unit}")
        else:
            print(f"{info.title:<18}{'nein':<6}{'-':>10}{'-':>10}{'-':>10}{'-':>10}  {unit}")
    if tel.warnings:
        print()
        for w in tel.warnings:
            print(f"Hinweis: {w}")
    return 0


def _print_values(tel: Telemetry, times: list[float], units: str, tz: str | None, labels: list[str] | None = None) -> None:
    sample = tel.sample(times)
    headers = labels or [format_duration(t) for t in times]
    print(f"{'':<18}" + "".join(f"{h:>16}" for h in headers))
    tod = []
    for t in times:
        if 0 <= t <= tel.duration:
            dt = tel.absolute_time(t)
            local = dt.astimezone(_tz(tz)) if tz else dt.astimezone()
            tod.append(f"{local:%H:%M:%S}")
        else:
            tod.append("--")
    print(f"{'Uhrzeit':<18}" + "".join(f"{x:>16}" for x in tod))
    for ch in DISPLAY_CHANNELS:
        if not tel.available.get(ch):
            continue
        vals = [format_value(ch, float(sample[ch][i]), units) for i in range(len(times))]
        print(f"{CHANNELS[ch].title:<18}" + "".join(f"{v:>16}" for v in vals))


def cmd_values(args) -> int:
    tel = _load(args.gpx, args)
    times = args.at or [0.0, tel.duration / 2, tel.duration]
    _print_values(tel, times, args.units, args.tz)
    return 0


def _offset_arg(text: str) -> float:
    from .core.units import parse_offset

    try:
        return parse_offset(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _build_sync(args, tel: Telemetry):
    info = probe(args.video)
    cam = info.camera if args.camera == "auto" else args.camera
    tz = args.tz or AppSettings.load().default_timezone
    profile = get_profile(cam, tz)
    if args.end:
        profile = replace(profile, creation_time_is_end=True)
    sync = VideoSync.from_video(info, tel.start_time, profile, args.offset,
                                time_source=getattr(args, "time_source", "auto"), track_duration=tel.duration)
    return info, profile, sync


_SELFTEST_GPX = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="selftest" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
{points}
</trkseg></trk></gpx>"""


def cmd_selftest(args) -> int:
    """Prüft die (installierte) App ohne Fenster: Qt, Schriften, Presets, ffmpeg, Zeichnen eines Frames.

    Gedacht für das Installer-Build-Skript: Ergebnis landet in ``--out`` (die Fenster-App hat keine Konsole).
    """
    import platform
    import tempfile
    import traceback

    from .app_paths import is_frozen, resource_path

    out = Path(args.out) if args.out else Path(tempfile.gettempdir()) / "cycleoverlay_selftest.txt"
    lines = [f"CycleOverlay {__version__} · Python {platform.python_version()} · {platform.platform()}",
             f"gebündelt: {is_frozen()} · Ressourcen: {resource_path()}"]
    ok = True

    def check(title, fn):
        nonlocal ok
        try:
            lines.append(f"OK    {title}: {fn()}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            lines.append(f"FEHLER {title}: {exc}")
            lines.append(traceback.format_exc())

    def qt():
        from PySide6 import __version__ as pv

        from .rendering.qt_support import ensure_app, load_bundled_fonts

        ensure_app()
        fams = load_bundled_fonts()
        return f"PySide6 {pv}, Schriften {', '.join(fams) or '(bereits geladen)'}"

    def presets():
        from .core.presets import list_presets

        names = list(list_presets())
        if not names:
            raise RuntimeError("keine Presets gefunden")
        return ", ".join(names)

    def ffmpeg():
        from .export.ffmpeg_locator import locate

        st = locate()
        if not st.ok:
            raise RuntimeError("; ".join(st.errors))
        return f"{st.version} ({st.ffmpeg})"

    def render():
        from .core.presets import load_preset
        from .core.processor import frame_times, process
        from .core.track_loader import load_track_bytes
        from .rendering.canvas_renderer import CanvasRenderer
        from .rendering.values import FrameValues

        pts = "\n".join(
            f'<trkpt lat="{48.0 + k * 0.0001:.6f}" lon="9.0"><ele>{300 + k * 0.2:.1f}</ele>'
            f"<time>2026-04-26T08:{k // 60:02d}:{k % 60:02d}Z</time></trkpt>" for k in range(120))
        tel = process(load_track_bytes(_SELFTEST_GPX.format(points=pts).encode(), "selftest.gpx"))
        values = FrameValues(tel, frame_times(30.0, 1, 30))
        img = CanvasRenderer(load_preset(), values, 640, 360).render(0)
        if img.isNull():
            raise RuntimeError("leeres Bild")
        return f"{img.width()}×{img.height()} gezeichnet"

    check("Qt", qt)
    check("Presets", presets)
    check("ffmpeg", ffmpeg)
    check("Zeichnen", render)
    lines.append("ERGEBNIS: " + ("alles in Ordnung" if ok else "FEHLER – siehe oben"))
    text = "\n".join(lines) + "\n"
    out.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if ok else 1


def cmd_syncdiag(args) -> int:
    """Auto-Sync an einem echten Clip prüfen: Vorschläge, Übereinstimmung am (bekannten) richtigen
    Versatz und alle Rohsignale als .npz zur Auswertung."""
    from .app_paths import analysis_cache_dir
    from .core.video_motion import analyze_video, sync_diagnostics

    tel = _load(args.gpx, args)
    info, _profile, sync = _build_sync(args, tel)
    print(f"Video: {info.path.name} · {format_duration(info.duration)} · {info.fps}")
    print("Analysiere Video …", flush=True)
    an = analyze_video(info.path, info.width, info.height, info.duration, cache_dir=analysis_cache_dir(),
                       progress=lambda f: print(f"\r  {f * 100:5.1f} %", end="", flush=True))
    print()
    true_start = sync.base_offset_s + args.offset if args.offset else None
    report = sync_diagnostics(args.out, an, tel.t, tel.channels, tel.pause_mask, sync.base_offset_s,
                              float(info.fps), true_start)
    print(report)
    if args.offset:
        print(f"Richtiger Versatz {args.offset:+.3f} s")
    print(f"Gespeichert: {args.out}")
    return 0


def cmd_sync(args) -> int:
    tel = _load(args.gpx, args)
    info, profile, sync = _build_sync(args, tel)
    print(f"Video:          {info.path.name}")
    print(f"Auflösung:      {info.width}x{info.height}   Framerate: {info.fps} ({info.fps_float:.3f} FPS)   Frames: {info.n_frames}")
    print(f"Dauer:          {format_duration(info.duration)} ({info.duration:.3f} s)")
    print(f"creation_time:  {info.creation_time_raw or '-'}")
    print(f"Kamera:         erkannt '{info.camera}', verwendet '{profile.title}'"
          f" – Zeitzone {profile.timezone or 'des Rechners'}, creation_time ist {'Ortszeit' if profile.creation_time_is_local else 'UTC'}")
    print(f"Video-Start:    {_fmt_dt(sync.video_start, args.tz)}"
          + ("  (am Trackbeginn ausgerichtet)" if sync.anchored else ""))
    if sync.anchored and sync.anchor_reason:
        print(f"                {sync.anchor_reason}")
    print(f"Track-Start:    {_fmt_dt(tel.start_time, args.tz)}")
    start, end = sync.window()
    print(f"Offset:         {sync.offset_s:+.3f} s")
    print(f"Clip im Track:  {format_duration(start)} bis {format_duration(end)}")
    chk = sync.check(tel.duration)
    marker = {"ok": "OK", "partial": "PRÜFEN", "outside": "FEHLER"}[chk.status]
    print(f"Prüfung:        [{marker}] {chk.message}")
    if chk.suggested_offset_s is not None:
        print(f"                Vorschlag: --offset {chk.suggested_offset_s + sync.offset_s:+.0f}")
    print()
    vts = [0.0, info.duration / 2, max(info.duration - 1e-3, 0.0)]
    tts = [float(sync.track_time(v)) for v in vts]
    _print_values(tel, tts, args.units, args.tz, labels=[f"Video {format_duration(v)}" for v in vts])
    return 0 if chk.status == "ok" else 2


def cmd_frames(args) -> int:
    tel = _load(args.gpx, args)
    if args.video:
        info, profile, sync = _build_sync(args, tel)
        ttimes = sync.frame_track_times()
        fps = info.fps
    else:
        if args.start is None or args.duration is None:
            print("Ohne --video werden --start und --duration benötigt.", file=sys.stderr)
            return 1
        fps = parse_fps(args.fps)
        n = int(math.floor(args.duration * fps))
        ttimes = frame_times(args.start, n, fps)
    vtimes = np.arange(ttimes.size) * fps.denominator / fps.numerator
    sample = tel.sample(ttimes)
    channels = [c for c in DISPLAY_CHANNELS if tel.available.get(c)]
    out: Path = args.out
    step = max(int(args.every), 1)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["frame", "video_time_s", "track_time_s", "uhrzeit"] + [f"{c} [{unit_for(c, args.units)}]".replace(" []", "") for c in channels])
        for i in range(0, ttimes.size, step):
            tt = float(ttimes[i])
            if 0 <= tt <= tel.duration:
                dt = tel.absolute_time(tt)
                local = dt.astimezone(_tz(args.tz)) if args.tz else dt.astimezone()
                tod = f"{local:%H:%M:%S.%f}"[:-3]
            else:
                tod = ""
            row = [i, f"{vtimes[i]:.4f}", f"{tt:.4f}", tod]
            for c in channels:
                v, _ = convert(c, float(sample[c][i]), args.units)
                row.append("" if v != v else f"{v:.{max(CHANNELS[c].decimals, 2)}f}")
            w.writerow(row)
    print(f"{ttimes.size} Frames ({fps} FPS) berechnet, {math.ceil(ttimes.size / step)} Zeilen geschrieben: {out}")
    return 0


def cmd_mapdata(args) -> int:
    """Kartendaten probeweise laden und ausführlich berichten (Diagnose)."""
    import platform
    import ssl
    import time as _time
    import traceback

    if getattr(args, "dump", None):
        os.environ["CYCLE_OVERLAY_OSM_DUMP"] = str(args.dump)
    from .core.osm import MapDataError, check_servers, fetch_map_data, ssl_context

    print(f"CycleOverlay {__version__} · Python {platform.python_version()} · {platform.platform()}")
    _, backend = ssl_context()
    print(f"TLS: {ssl.OPENSSL_VERSION} · Zertifikate: {backend}")
    proxies = [k for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY") if os.environ.get(k)]
    print(f"Proxy-Variablen gesetzt: {', '.join(proxies) or 'keine'}")
    print("\nServer:")
    for line in check_servers():
        print(f"  {line}")
    raw = load_track(args.gpx)
    print(f"\nFahrt: {args.gpx.name} · {raw.n_points} Punkte · GPS: {'ja' if raw.has_position else 'nein'}")
    if not raw.has_position:
        print("Keine GPS-Positionen – Kartendaten nicht möglich.")
        return 1
    t0 = _time.monotonic()
    try:
        md = fetch_map_data(raw.lat, raw.lon, buffer_m=args.buffer, cache_dir=None, timeout=args.timeout,
                            chunked=args.chunked,
                            progress=lambda m: print(f"  [{_time.monotonic() - t0:5.1f} s] {m}", flush=True))
    except MapDataError as exc:
        print(f"\nFEHLER: {exc}")
        return 1
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
        return 130
    except Exception:  # noqa: BLE001 – Diagnose: alles ausgeben
        print("\nUNERWARTETER FEHLER:")
        traceback.print_exc(file=sys.stdout)
        return 1
    print(f"\nOK in {_time.monotonic() - t0:.1f} s: {len(md)} Elemente {md.counts()}")
    print(f"Abdeckung der Strecke: {(md.coverage or 0) * 100:.0f} %")
    if args.out:
        args.out.write_bytes(md.to_json_bytes())
        print(f"Gespeichert: {args.out} ({args.out.stat().st_size / 1e6:.1f} MB)")
    return 0


def cmd_netcheck(args) -> int:
    """Verbindung zu den Kartendiensten auf mehreren Wegen prüfen (ca. 15 s)."""
    import platform

    from .core.osm import network_check, ssl_context

    print(f"CycleOverlay {__version__} · Python {platform.python_version()} · {platform.platform()}")
    print(f"Zertifikate: {ssl_context()[1]}")
    print("Prüfe Verbindungen (parallel, max. ~15 s) …\n", flush=True)
    for line in network_check(timeout=args.timeout):
        print(line)
    return 0


# --------------------------------------------------------------------------- Parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cycle-overlay", description="CycleOverlay – GPX-Telemetrie als transparentes Video-Overlay")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--units", choices=["metric", "imperial"], default="metric", help="Einheiten der Ausgabe")
    common.add_argument("--tz", default=None, help="Zeitzone für Ortszeit, z. B. Europe/Berlin (Standard: Rechner)")
    common.add_argument("--pause-mode", choices=["zero", "hold"], default="zero", help="Werte in Pausen: 0 oder halten")

    sync_opts = argparse.ArgumentParser(add_help=False)
    sync_opts.add_argument("--camera", choices=["auto", *CAMERA_PROFILES], default="auto", help="Kamera-Profil")
    sync_opts.add_argument("--offset", type=_offset_arg, default=0.0,
                           help="manuelle Korrektur in Sekunden oder als [-]H:MM:SS(.ms)")
    sync_opts.add_argument("--time-source", choices=["auto", "metadata", "track_start"], default="auto",
                           help="Zeitbezug: auto = Aufnahmezeit, wenn sie zur Fahrt passt, sonst Trackbeginn")
    sync_opts.add_argument("--end", action="store_true", help="creation_time ist das Ende der Aufnahme")

    sub = p.add_subparsers(dest="command")

    sub.add_parser("gui", help="grafische Oberfläche starten (Standard ohne Befehl)")

    sp = sub.add_parser("check", help="ffmpeg/ffprobe und Ordner prüfen")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("info", parents=[common], help="Zusammenfassung und verfügbare Signale einer GPX-Datei")
    sp.add_argument("gpx", type=Path)
    sp.set_defaults(func=cmd_info)

    sp = sub.add_parser("values", parents=[common], help="Werte zu bestimmten Track-Zeiten anzeigen")
    sp.add_argument("gpx", type=Path)
    sp.add_argument("--at", type=parse_clock, action="append", help="Track-Zeit HH:MM:SS (mehrfach möglich)")
    sp.set_defaults(func=cmd_values)

    sp = sub.add_parser("sync", parents=[common, sync_opts], help="Video mit GPX synchronisieren und prüfen")
    sp.add_argument("gpx", type=Path)
    sp.add_argument("video", type=Path)
    sp.set_defaults(func=cmd_sync)

    sp = sub.add_parser("selftest", help="Installation prüfen (Qt, Schriften, ffmpeg, Zeichnen) – ohne Fenster")
    sp.add_argument("--out", default=None, help="Ergebnisdatei (Standard: Temp-Ordner/cycleoverlay_selftest.txt)")
    sp.set_defaults(func=cmd_selftest)

    sp = sub.add_parser("syncdiag", parents=[common, sync_opts],
                        help="Auto-Sync an einem Clip prüfen (mit --offset = bekannter richtiger Versatz)")
    sp.add_argument("gpx", type=Path)
    sp.add_argument("video", type=Path)
    sp.add_argument("--out", default="syncdiag.npz", help="Datei für die Rohsignale (.npz)")
    sp.set_defaults(func=cmd_syncdiag)

    sp = sub.add_parser("frames", parents=[common, sync_opts], help="Werte für jedes Frame als CSV schreiben")
    sp.add_argument("gpx", type=Path)
    sp.add_argument("--video", type=Path, help="Referenzvideo (bestimmt Zeitraum und Framerate)")
    sp.add_argument("--start", type=parse_clock, help="ohne Video: Start als Track-Zeit HH:MM:SS")
    sp.add_argument("--duration", type=float, help="ohne Video: Dauer in Sekunden")
    sp.add_argument("--fps", default="30", help="ohne Video: Framerate, z. B. 30, 29.97, 60000/1001")
    sp.add_argument("--every", type=int, default=1, help="nur jedes n-te Frame schreiben")
    sp.add_argument("--out", type=Path, required=True, help="Ziel-CSV-Datei")
    sp.set_defaults(func=cmd_frames)

    sp = sub.add_parser("netcheck", help="Verbindung zu den Kartendiensten prüfen (Diagnose)")
    sp.add_argument("--timeout", type=float, default=12.0)
    sp.set_defaults(func=cmd_netcheck)

    sp = sub.add_parser("mapdata", help="OpenStreetMap-Kartendaten probeweise laden (Diagnose)")
    sp.add_argument("gpx", type=Path)
    sp.add_argument("--buffer", type=float, default=1000.0, help="Korridorbreite in Metern (je Seite)")
    sp.add_argument("--timeout", type=float, default=300.0, help="Wartezeit je Anfrage in Sekunden")
    sp.add_argument("--chunked", action="store_true", help="in Abschnitten laden (statt am Stück)")
    sp.add_argument("--out", type=Path, help="Kartendaten zusätzlich als JSON speichern")
    sp.add_argument("--dump", default=None, help="Ordner: Abfragen und Rohantworten der Server dort speichern")
    sp.set_defaults(func=cmd_mapdata)

    from .cli_render import add_commands

    add_commands(sub, common, sync_opts)
    return p


def main(argv: list[str] | None = None) -> int:
    # Windows-Konsolen können nicht immer alle Zeichen (°, Ø, –) darstellen
    for stream in (sys.stdout, sys.stderr):
        try:
            # zeilenweise ausgeben, damit Berichte auch beim Umleiten in eine Datei sofort ankommen
            stream.reconfigure(errors="replace", line_buffering=True)  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        print("\nOhne Befehl startet 'cycle-overlay' die grafische Oberfläche.")
        return 0
    try:
        return int(args.func(args) or 0)
    except (GPXParseError, VideoProbeError, SyncError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"Fehler: Datei nicht gefunden: {exc.filename}", file=sys.stderr)
        return 1
    except ValueError as exc:  # z. B. ungültiges Preset
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
