"""Exportprofile: nur die Anzeigen mit Alpha-Kanal (PRD 3.6) oder das Video mit eingebrannten Anzeigen."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


@dataclass(frozen=True)
class ExportProfile:
    key: str
    title: str
    extension: str  # ".mov" oder "" für Bildsequenz (Ordner)
    codec_args: tuple[str, ...]
    muxer: str  # ffmpeg -f
    is_sequence: bool = False
    #: erwartete Pixelformate der Ausgabe (mit Alpha), für die Nachprüfung
    alpha_pix_fmts: tuple[str, ...] = ()
    note: str = ""
    #: ffmpeg-Encoder, der vorhanden sein muss (z. B. nur auf macOS)
    requires_encoder: str | None = None
    #: Breite und Höhe müssen gerade sein (Hardware-Encoder, 4:2:0)
    even_size: bool = False
    #: Endung der Einzelbilder bei Sequenzen
    frame_ext: str = ".png"
    #: Decoder, mit dem ffmpeg die Alpha-Ebene liest (VP9: nur libvpx, nicht der eingebaute)
    alpha_decoder: str | None = None
    #: Video wird mit exportiert (Anzeigen eingebrannt, keine Transparenz)
    with_video: bool = False
    #: bei ``with_video``: "h264" | "hevc" (Encoder wird beim Export gewählt) oder None (feste codec_args)
    codec_family: str | None = None
    #: Ton-Argumente bei ``with_video``
    audio_args: tuple[str, ...] = ()

    @property
    def frame_pattern(self) -> str:
        """ffmpeg-Muster der Einzelbilder (``frame_000001.png`` …)."""
        return f"frame_%06d{self.frame_ext}"

    @property
    def frame_glob(self) -> str:
        return f"frame_*{self.frame_ext}"

    def output_target(self, out: Path) -> Path:
        """Ziel: bei Videos Datei mit passender Endung, bei Sequenzen der Ordner.

        Nur bekannte Datei-Endungen werden ersetzt – Punkte im Namen (``Tour.2026_overlay``)
        bleiben erhalten.
        """
        out = Path(out)
        known = out.suffix.lower() in KNOWN_EXTENSIONS
        if self.is_sequence:
            return out.with_suffix("") if known else out
        if out.suffix.lower() == self.extension:
            return out
        return out.with_suffix(self.extension) if known else out.with_name(out.name + self.extension)


#: Endungen, die als Datei-Endung gelten (alles andere nach einem Punkt gehört zum Namen)
KNOWN_EXTENSIONS = {".mov", ".mp4", ".m4v", ".mkv", ".avi", ".mxf", ".png", ".webm", ".tif", ".tiff"}

#: BT.709 für HD/UHD – sonst verwendet swscale BT.601 und Farben verschieben sich in FCP/Resolve
_BT709 = (
    "-vf", "scale=out_color_matrix=bt709:out_range=tv",
    "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
)

PROFILES: dict[str, ExportProfile] = {
    "prores4444": ExportProfile(
        key="prores4444",
        title="Apple ProRes 4444 (.mov)",
        extension=".mov",
        codec_args=(
            *_BT709,
            "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
            "-alpha_bits", "16", "-vendor", "apl0",
        ),
        muxer="mov",
        alpha_pix_fmts=("yuva444p10le", "yuva444p12le"),
        note="Standard für Final Cut Pro und DaVinci Resolve",
    ),
    "prores4444xq": ExportProfile(
        key="prores4444xq",
        title="Apple ProRes 4444 XQ (.mov)",
        extension=".mov",
        codec_args=(
            *_BT709,
            "-c:v", "prores_ks", "-profile:v", "4444xq", "-pix_fmt", "yuva444p10le",
            "-alpha_bits", "16", "-vendor", "apl0",
        ),
        muxer="mov",
        alpha_pix_fmts=("yuva444p10le", "yuva444p12le"),
        note="höchste ProRes-Qualität, rund 1,5× so groß wie 4444 – meist nicht nötig",
        requires_encoder="prores_ks",
    ),
    "cineform": ExportProfile(
        key="cineform",
        title="GoPro CineForm mit Alpha (.mov)",
        extension=".mov",
        codec_args=("-c:v", "cfhd", "-pix_fmt", "gbrap12le", "-quality", "film3+"),
        muxer="mov",
        alpha_pix_fmts=("gbrap12le",),
        note="gut für DaVinci Resolve und Premiere unter Windows; Final Cut Pro braucht ein Plug-in",
        requires_encoder="cfhd",
    ),
    "qtrle": ExportProfile(
        key="qtrle",
        title="QuickTime Animation / RLE (.mov)",
        extension=".mov",
        codec_args=("-c:v", "qtrle", "-pix_fmt", "argb"),
        muxer="mov",
        alpha_pix_fmts=("argb",),
        note="verlustfrei; in Resolve vor Gebrauch testen",
    ),
    "mov_png": ExportProfile(
        key="mov_png",
        title="QuickTime PNG (.mov)",
        extension=".mov",
        codec_args=("-c:v", "png", "-pix_fmt", "rgba"),
        muxer="mov",
        alpha_pix_fmts=("rgba",),
        note="verlustfrei in einer Datei; langsamer zu schneiden als ProRes",
        requires_encoder="png",
    ),
    "webm_vp9": ExportProfile(
        key="webm_vp9",
        title="WebM VP9 mit Alpha (.webm)",
        extension=".webm",
        codec_args=(
            "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "24",
            "-deadline", "good", "-cpu-used", "4", "-row-mt", "1", "-auto-alt-ref", "0",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        ),
        muxer="webm",
        alpha_pix_fmts=(),  # ffprobe meldet yuv420p, die Alpha-Ebene steckt in Zusatzdaten
        note="sehr klein; für Web, OBS, Browser und Premiere – nicht für Final Cut Pro",
        requires_encoder="libvpx-vp9",
        even_size=True,
        alpha_decoder="libvpx-vp9",
    ),
    "png_sequence": ExportProfile(
        key="png_sequence",
        title="PNG-Bildsequenz (Ordner)",
        extension="",
        codec_args=("-c:v", "png", "-pix_fmt", "rgba", "-start_number", "1"),
        muxer="image2",
        is_sequence=True,
        alpha_pix_fmts=("rgba",),
        note="verlustfreier Fallback, sehr viele Dateien",
    ),
    "tiff_sequence": ExportProfile(
        key="tiff_sequence",
        title="TIFF-Bildsequenz (Ordner)",
        extension="",
        codec_args=("-c:v", "tiff", "-pix_fmt", "rgba", "-compression_algo", "deflate", "-start_number", "1"),
        muxer="image2",
        is_sequence=True,
        alpha_pix_fmts=("rgba",),
        note="verlustfrei, für Compositing (Fusion, After Effects, Nuke)",
        requires_encoder="tiff",
        frame_ext=".tif",
    ),
    "hevc_alpha": ExportProfile(
        key="hevc_alpha",
        title="HEVC mit Alpha (.mov, nur macOS)",
        extension=".mov",
        codec_args=(
            "-c:v", "hevc_videotoolbox", "-alpha_quality", "0.75", "-b:v", "25M",
            "-pix_fmt", "bgra", "-tag:v", "hvc1",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        ),
        muxer="mov",
        alpha_pix_fmts=(),  # ffprobe meldet die Alpha-Ebene von HEVC nicht zuverlässig
        note="deutlich kleinere Dateien; Final Cut Pro und Resolve auf dem Mac",
        requires_encoder="hevc_videotoolbox",
        even_size=True,
    ),
}

#: Profile für den Export mit Video (Overlay eingebrannt, ohne Transparenz)
VIDEO_PROFILES: dict[str, ExportProfile] = {
    "mp4_h264": ExportProfile(
        key="mp4_h264",
        title="MP4 H.264",
        extension=".mp4",
        codec_args=(),
        muxer="mp4",
        note="läuft überall (Handy, Web, Social Media); Hardware-Encoder, wenn vorhanden",
        even_size=True,
        with_video=True,
        codec_family="h264",
        audio_args=("-c:a", "aac", "-b:a", "192k"),
    ),
    "mp4_hevc": ExportProfile(
        key="mp4_hevc",
        title="MP4 H.265 / HEVC",
        extension=".mp4",
        codec_args=(),
        muxer="mp4",
        note="etwa halb so groß wie H.264 bei gleicher Qualität; auch für 5K und mehr",
        even_size=True,
        with_video=True,
        codec_family="hevc",
        audio_args=("-c:a", "aac", "-b:a", "192k"),
    ),
    "mov_prores422": ExportProfile(
        key="mov_prores422",
        title="ProRes 422 HQ (.mov)",
        extension=".mov",
        codec_args=(
            "-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le", "-vendor", "apl0",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        ),
        muxer="mov",
        note="zum Weiterschneiden in Final Cut Pro/Resolve; sehr große Dateien",
        with_video=True,
        audio_args=("-c:a", "pcm_s16le"),
    ),
}
PROFILES.update(VIDEO_PROFILES)

OVERLAY_PROFILE_KEYS = tuple(k for k, p in PROFILES.items() if not p.with_video)
VIDEO_PROFILE_KEYS = tuple(VIDEO_PROFILES)

DEFAULT_PROFILE = "prores4444"
DEFAULT_VIDEO_PROFILE = "mp4_h264"

_ENCODER_CACHE: dict[str, set[str]] = {}


def available_encoders(ffmpeg: str | None = None) -> set[str]:
    """Namen der Video-Encoder der installierten ffmpeg-Version (zwischengespeichert)."""
    import re

    from ..platform_utils import run
    from .ffmpeg_locator import find_executable

    exe = ffmpeg or find_executable("ffmpeg")
    key = str(exe)
    if key not in _ENCODER_CACHE:
        names: set[str] = set()
        if exe is not None:
            try:
                out = run([exe, "-hide_banner", "-encoders"], timeout=20).stdout
                names = set(re.findall(r"^\s*V\S*\s+(\S+)", out, re.MULTILINE))
            except Exception:  # noqa: BLE001
                names = set()
        _ENCODER_CACHE[key] = names
    return _ENCODER_CACHE[key]


def is_available(profile: ExportProfile, ffmpeg: str | None = None) -> bool:
    return profile.requires_encoder is None or profile.requires_encoder in available_encoders(ffmpeg)


def available_profiles(ffmpeg: str | None = None, with_video: bool = False) -> dict[str, ExportProfile]:
    """Profile, die diese ffmpeg-Version kann – nur Overlay (Standard) oder nur mit Video."""
    return {k: p for k, p in PROFILES.items() if p.with_video == with_video and is_available(p, ffmpeg)}

# Grobe Bitraten bei 1920x1080 und 29,97 FPS (Bit/s), werden mit Pixelanzahl und FPS skaliert.
# ProRes 4444: Apple-Richtwert (Obergrenze, transparente Flächen sind meist kleiner).
_BITRATE_1080P30 = {"prores4444": 330e6, "prores4444xq": 500e6, "cineform": 160e6, "qtrle": 60e6,
                    "mov_png": 45e6, "webm_vp9": 6e6, "png_sequence": 40e6, "tiff_sequence": 70e6,
                    "hevc_alpha": 25e6, "mov_prores422": 220e6}


def estimate_size_bytes(profile: ExportProfile, width: int, height: int, fps: Fraction | float, n_frames: int) -> int:
    """Grobe Schätzung der Dateigröße (eher Obergrenze)."""
    fps_f = float(fps)
    seconds = n_frames / fps_f if fps_f else 0.0
    if profile.codec_family:
        from .video_encoders import target_bitrate

        return int((target_bitrate(profile.codec_family, "high", width, height, fps_f) + 192e3) * seconds / 8)
    if profile.key == "hevc_alpha":  # feste Bitrate
        return int(_BITRATE_1080P30["hevc_alpha"] * seconds / 8)
    scale = (width * height) / (1920 * 1080) * fps_f / 29.97
    return int(_BITRATE_1080P30.get(profile.key, 330e6) * scale * seconds / 8)


def format_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} TB"
