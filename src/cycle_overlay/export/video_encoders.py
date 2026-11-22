"""Encoder-Auswahl für den Export mit Video (Overlay eingebrannt) – ohne Qt.

ffmpeg listet Hardware-Encoder (NVENC, AMF, Quick Sync) oft auch dann, wenn die passende
Grafikkarte fehlt. Deshalb wird jeder Kandidat einmal kurz probeweise gestartet; der erste,
der funktioniert, wird genommen. Reihenfolge: Hardware zuerst (schnell), dann Software.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from fractions import Fraction

from ..platform_utils import run

log = logging.getLogger(__name__)

#: Kandidaten je Codec-Familie, in dieser Reihenfolge probiert
CANDIDATES: dict[str, tuple[str, ...]] = {
    "h264": ("h264_videotoolbox", "h264_nvenc", "h264_amf", "h264_qsv", "libx264", "h264_mf", "libopenh264"),
    "hevc": ("hevc_videotoolbox", "hevc_nvenc", "hevc_amf", "hevc_qsv", "libx265", "hevc_mf"),
}

HARDWARE = {"videotoolbox": "Apple (VideoToolbox)", "nvenc": "NVIDIA (NVENC)", "amf": "AMD (AMF)",
            "qsv": "Intel (Quick Sync)", "mf": "Windows Media Foundation"}

QUALITIES = ("standard", "high")
QUALITY_TITLES = {"standard": "Standard", "high": "Hoch"}

#: Ziel-Bitrate bei 1920×1080 und 30 FPS (Bit/s); wird mit Pixelzahl und FPS skaliert
BITRATE_1080P30 = {("h264", "standard"): 16e6, ("h264", "high"): 30e6,
                   ("hevc", "standard"): 10e6, ("hevc", "high"): 20e6}

#: Hardware-Encoder für H.264 schaffen meist höchstens 4096 Pixel Breite/Höhe
H264_HW_MAX = 4096

#: Farbangaben der Ausgabe (Action-Cams filmen in SDR mit BT.709; die Anzeigen werden so umgerechnet)
BT709_TAGS = ("-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv")

_PROBE: dict[tuple[str, str], bool] = {}


def _pix_fmt(encoder: str) -> str:
    # Quick Sync, AMF und Media Foundation erwarten NV12; alle anderen nehmen yuv420p
    return "nv12" if encoder.endswith(("_qsv", "_amf", "_mf")) else "yuv420p"


def is_hardware(encoder: str) -> bool:
    return any(encoder.endswith("_" + k) for k in HARDWARE)


def encoder_title(encoder: str) -> str:
    for k, title in HARDWARE.items():
        if encoder.endswith("_" + k):
            return f"{encoder} · Hardware {title}"
    return f"{encoder} · Software"


def probe_encoder(ffmpeg: str, encoder: str) -> bool:
    """Startet den Encoder kurz mit einem Testbild (zwischengespeichert)."""
    key = (str(ffmpeg), encoder)
    if key not in _PROBE:
        ok = False
        try:
            res = run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                       "-i", "color=c=black:s=640x360:r=30", "-frames:v", "5",
                       "-c:v", encoder, "-pix_fmt", _pix_fmt(encoder), "-f", "null", "-"], timeout=30)
            ok = res.returncode == 0
            if not ok:
                log.info("Encoder %s nicht nutzbar: %s", encoder, res.stderr.strip()[-300:])
        except Exception as exc:  # noqa: BLE001 – dann eben der nächste Kandidat
            log.info("Encoder %s nicht nutzbar: %s", encoder, exc)
        _PROBE[key] = ok
    return _PROBE[key]


def choose_encoder(family: str, ffmpeg: str, available: set[str], width: int = 1920, height: int = 1080) -> str | None:
    """Erster funktionierender Encoder der Familie (``h264``/``hevc``) oder None."""
    for enc in CANDIDATES.get(family, ()):
        if enc not in available:
            continue
        if family == "h264" and is_hardware(enc) and max(width, height) > H264_HW_MAX:
            continue  # zu groß für die Hardware – Software probieren
        if probe_encoder(ffmpeg, enc):
            return enc
    return None


def target_bitrate(family: str, quality: str, width: int, height: int, fps: Fraction | float) -> int:
    base = BITRATE_1080P30.get((family, quality), BITRATE_1080P30[(family, "standard")])
    scale = (width * height) / (1920 * 1080) * max(float(fps), 1.0) / 30.0
    return int(min(max(base * scale, 2e6), 250e6))


@dataclass(frozen=True)
class EncoderChoice:
    encoder: str
    args: tuple[str, ...]
    bitrate: int | None  # None = Qualität über CRF


def encoder_args(family: str, encoder: str, quality: str, width: int, height: int,
                 fps: Fraction | float) -> EncoderChoice:
    """ffmpeg-Argumente für den gewählten Encoder."""
    high = quality == "high"
    tag: tuple[str, ...] = ("-tag:v", "hvc1") if family == "hevc" else ()  # damit Apple-Geräte HEVC abspielen
    tag += BT709_TAGS
    pix = ("-pix_fmt", _pix_fmt(encoder))
    if encoder == "libx264":
        return EncoderChoice(encoder, ("-c:v", "libx264", "-preset", "medium", "-crf", "16" if high else "20",
                                       "-profile:v", "high", *pix), None)
    if encoder == "libx265":
        return EncoderChoice(encoder, ("-c:v", "libx265", "-preset", "medium", "-crf", "19" if high else "23",
                                       *pix, *tag), None)
    b = target_bitrate(family, quality, width, height, fps)
    rate = ("-b:v", str(b), "-maxrate", str(int(b * 1.5)), "-bufsize", str(int(b * 2)))
    extra: tuple[str, ...] = ()
    if encoder.endswith("_nvenc"):
        extra = ("-preset", "p5", "-rc", "vbr")
    elif encoder.endswith("_qsv"):
        extra = ("-preset", "medium")
    elif encoder.endswith("_amf"):
        extra = ("-quality", "quality" if high else "balanced")
    elif encoder.endswith("_videotoolbox"):
        extra = ("-allow_sw", "1")
    if family == "h264" and not encoder.endswith("_mf") and encoder != "libopenh264":
        extra += ("-profile:v", "high")
    return EncoderChoice(encoder, ("-c:v", encoder, *extra, *rate, *pix, *tag), b)
