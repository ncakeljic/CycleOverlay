"""Video-Metadaten per ffprobe auslesen (PRD 3.3.1)."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from fractions import Fraction
from os import PathLike
from pathlib import Path

from ..export.ffmpeg_locator import find_executable
from ..platform_utils import run
from .models import VideoInfo

log = logging.getLogger(__name__)


class VideoProbeError(RuntimeError):
    pass


_TZ_NO_COLON = re.compile(r"([+-]\d{2})(\d{2})$")


def parse_iso_datetime(text: str | None) -> datetime | None:
    """ISO-Zeit lesen; behält die Zeitzonenangabe (oder bleibt ohne Zeitzone)."""
    if not text:
        return None
    s = text.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    s = _TZ_NO_COLON.sub(r"\1:\2", s)
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        log.warning("Zeitangabe nicht lesbar: %r", text)
        return None


def _parse_fraction(text: str | None) -> Fraction | None:
    if not text:
        return None
    try:
        f = Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None
    return f if f > 0 else None


def _detect_camera(all_tags: dict[str, str], handler: str | None) -> str:
    blob = " ".join([*(f"{k}={v}" for k, v in all_tags.items()), handler or ""]).lower()
    if "gopro" in blob:
        return "gopro"
    if "insta360" in blob:
        return "insta360"
    if "dji" in blob:
        return "dji"
    if "com.apple.quicktime.make=apple" in blob or "com.apple.quicktime.creationdate" in blob:
        return "apple"
    return "generic"


def parse_ffprobe_json(data: dict, path: Path) -> VideoInfo:
    """ffprobe-JSON (``-show_format -show_streams``) in ein VideoInfo umwandeln."""
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    if video is None:
        raise VideoProbeError(f"{path.name}: keine Videospur gefunden.")
    fmt = data.get("format", {})
    fmt_tags = {k.lower(): str(v) for k, v in (fmt.get("tags") or {}).items()}
    v_tags = {k.lower(): str(v) for k, v in (video.get("tags") or {}).items()}
    all_tags = {**v_tags, **fmt_tags}

    r_fps = _parse_fraction(video.get("r_frame_rate"))
    avg_fps = _parse_fraction(video.get("avg_frame_rate"))
    fps = r_fps or avg_fps
    # r_frame_rate ist bei variabler Bildrate (Handys) oft ein Zeitbasis-Wert wie 90000/1 –
    # dann die mittlere Bildrate nehmen
    if r_fps and avg_fps and (r_fps > 240 or abs(float(r_fps) / float(avg_fps) - 1) > 0.05):
        fps = avg_fps
    if fps is None:
        raise VideoProbeError(f"{path.name}: Framerate konnte nicht bestimmt werden.")

    duration = None
    for src in (video.get("duration"), fmt.get("duration")):
        try:
            duration = float(src)
            break
        except (TypeError, ValueError):
            continue
    if duration is None:
        raise VideoProbeError(f"{path.name}: Dauer konnte nicht bestimmt werden.")

    try:
        n_frames = int(video.get("nb_frames"))
    except (TypeError, ValueError):
        n_frames = int(round(duration * fps))

    rotation = 0
    for sd in video.get("side_data_list", []) or []:
        if "rotation" in sd:
            try:
                rotation = int(float(sd["rotation"]))
            except (TypeError, ValueError):
                pass
    if not rotation and "rotate" in v_tags:
        try:
            rotation = int(float(v_tags["rotate"]))
        except ValueError:
            pass
    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    if abs(rotation) % 180 == 90:
        width, height = height, width

    ct_raw = fmt_tags.get("creation_time") or v_tags.get("creation_time")
    handler = v_tags.get("handler_name")
    return VideoInfo(
        path=path,
        width=width,
        height=height,
        fps=fps,
        duration=duration,
        n_frames=n_frames,
        codec=video.get("codec_name"),
        creation_time=parse_iso_datetime(ct_raw),
        creation_time_raw=ct_raw,
        apple_creation_date=parse_iso_datetime(all_tags.get("com.apple.quicktime.creationdate")),
        timecode=v_tags.get("timecode") or fmt_tags.get("timecode"),
        rotation=rotation,
        make=all_tags.get("com.apple.quicktime.make") or all_tags.get("make"),
        model=all_tags.get("com.apple.quicktime.model") or all_tags.get("model"),
        handler_name=handler,
        camera=_detect_camera(all_tags, handler),
    )


def probe(path: str | PathLike, ffprobe: str | PathLike | None = None) -> VideoInfo:
    path = Path(path)
    if not path.is_file():
        raise VideoProbeError(f"Videodatei nicht gefunden: {path}")
    exe = Path(ffprobe) if ffprobe else find_executable("ffprobe")
    if exe is None:
        raise VideoProbeError("ffprobe wurde nicht gefunden – bitte ffmpeg installieren.")
    res = run(
        [exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        timeout=60,
    )
    if res.returncode != 0:
        raise VideoProbeError(f"ffprobe konnte {path.name} nicht lesen: {res.stderr.strip()}")
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError as exc:
        raise VideoProbeError(f"Ungültige Ausgabe von ffprobe: {exc}") from exc
    return parse_ffprobe_json(data, path)
