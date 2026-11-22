"""Export per ffmpeg-Pipe (PRD 3.6) – ohne Qt.

Frames werden als rohe, nicht vormultiplizierte RGBA-Bytes nach ``stdin`` geschrieben.
``stderr`` wird in einem eigenen Thread gelesen, sonst kann der Pipe-Puffer volllaufen
und ffmpeg (und damit der Export) hängen bleiben.
Videos werden zuerst als ``<name>.part`` geschrieben und erst nach Erfolg umbenannt.
"""

from __future__ import annotations

import collections
import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable

from ..platform_utils import ExecutableBlocked, popen, run
from .ffmpeg_locator import find_executable
from .profiles import ExportProfile, is_available

log = logging.getLogger(__name__)

SEQUENCE_PATTERN = "frame_%06d.png"  # PNG-Sequenz; andere Sequenzen: ``ExportProfile.frame_pattern``


class ExportError(RuntimeError):
    pass


class ExportCancelled(ExportError):
    pass


@dataclass
class Progress:
    frame: int  # Anzahl fertiger Frames
    total: int
    elapsed_s: float
    fps: float
    eta_s: float


@dataclass
class ExportResult:
    path: Path
    n_frames: int
    seconds: float
    codec: str | None
    pix_fmt: str | None
    size_bytes: int
    #: beim Export mit Video: verwendeter Encoder (z. B. "h264_videotoolbox")
    encoder: str | None = None


def _fps_fraction(fps) -> Fraction:
    from ..core.processor import parse_fps

    return parse_fps(fps)


def _composite_format(video_args: tuple[str, ...] | list[str]) -> str:
    """Pixelformat der Ausgabe (aus den Encoder-Argumenten), Standard yuv420p."""
    args = list(video_args)
    for i, a in enumerate(args[:-1]):
        if a == "-pix_fmt":
            return args[i + 1]
    return "yuv420p"


def composite_graph(width: int, height: int, fps, out_pix_fmt: str) -> str:
    """Filtergraph: Video (Eingang 0) auf Exportgröße/-framerate, Anzeigen (Eingang 1) darüber.

    Gemischt wird in 4:4:4 mit 10 Bit; die Anzeigen werden mit BT.709 umgerechnet (wie das Video),
    sonst verschieben sich ihre Farben. Abweichendes Seitenverhältnis: Video einpassen, Rand schwarz.
    """
    f = _fps_fraction(fps)
    return (
        f"[0:v]setpts=PTS-STARTPTS,fps={f.numerator}/{f.denominator},"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv444p10le[bg];"
        f"[1:v]setpts=PTS-STARTPTS,scale=out_color_matrix=bt709:out_range=tv,format=yuva444p10le[ov];"
        f"[bg][ov]overlay=0:0:format=yuv444p10:shortest=1:eof_action=endall,format={out_pix_fmt}[v]"
    )


def build_command(ffmpeg: str | Path, profile: ExportProfile, width: int, height: int, fps,
                  output: Path, metadata: dict[str, str] | None = None, *,
                  background: str | os.PathLike | None = None, video_args: tuple[str, ...] | None = None) -> list[str]:
    f = _fps_fraction(fps)
    raw_in = ["-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{width}x{height}",
              "-framerate", f"{f.numerator}/{f.denominator}", "-i", "-"]
    cmd = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y"]
    if profile.with_video:
        if background is None:
            raise ExportError("Für dieses Format wird ein Video gebraucht.")
        vargs = tuple(video_args if video_args is not None else profile.codec_args)
        cmd += ["-i", str(background), *raw_in,
                "-filter_complex", composite_graph(width, height, f, _composite_format(vargs)),
                "-map", "[v]", "-map", "0:a:0?", *vargs, *profile.audio_args, "-shortest"]
        if profile.muxer in ("mp4", "mov"):
            cmd += ["-movflags", "+faststart"]
    else:
        cmd += [*raw_in, "-an", *profile.codec_args]
    for k, v in (metadata or {}).items():
        cmd += ["-metadata", f"{k}={v}"]
    cmd += ["-f", profile.muxer, str(output)]
    return cmd


class FFmpegWriter:
    """Startet ffmpeg und nimmt Frames entgegen."""

    def __init__(self, cmd: list[str], frame_bytes: int):
        self.cmd = cmd
        self.frame_bytes = frame_bytes
        self.proc: subprocess.Popen | None = None
        self._stderr: collections.deque[str] = collections.deque(maxlen=200)
        self._reader: threading.Thread | None = None

    def start(self) -> "FFmpegWriter":
        log.info("ffmpeg: %s", " ".join(self.cmd))
        try:
            self.proc = popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              bufsize=0)
        except ExecutableBlocked as exc:
            raise ExportError(str(exc)) from exc
        except OSError as exc:
            raise ExportError(f"ffmpeg konnte nicht gestartet werden: {exc}") from exc
        self._reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._reader.start()
        return self

    def _read_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        for raw in iter(self.proc.stderr.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                self._stderr.append(line)
                log.debug("ffmpeg: %s", line)

    @property
    def stderr_text(self) -> str:
        return "\n".join(self._stderr)

    def write(self, data: bytes | memoryview) -> None:
        if len(data) != self.frame_bytes:
            raise ExportError(f"Frame hat {len(data)} Bytes, erwartet {self.frame_bytes}")
        assert self.proc and self.proc.stdin
        try:
            self.proc.stdin.write(data)
        except (BrokenPipeError, OSError) as exc:
            self.proc.wait(timeout=10)
            if self._reader:
                self._reader.join(timeout=5)
            raise ExportError(f"ffmpeg hat den Export abgebrochen: {self.stderr_text or exc}") from exc

    def finish(self, timeout: float = 600) -> None:
        assert self.proc and self.proc.stdin
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            rc = self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            self.abort()
            raise ExportError("ffmpeg reagiert nicht mehr (Zeitüberschreitung).") from exc
        if self._reader:
            self._reader.join(timeout=5)
        if rc != 0:
            raise ExportError(f"ffmpeg wurde mit Fehlercode {rc} beendet: {self.stderr_text}")

    def abort(self) -> None:
        if not self.proc:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        if self._reader:
            self._reader.join(timeout=5)


def _cleanup(profile: ExportProfile, target: Path, tmp: Path | None) -> None:
    try:
        if profile.is_sequence:
            for f in target.glob(profile.frame_glob):
                f.unlink()
            if target.exists() and not any(target.iterdir()):
                target.rmdir()
        elif tmp and tmp.exists():
            tmp.unlink()
    except OSError as exc:
        log.warning("Aufräumen nach Abbruch unvollständig: %s", exc)


def verify_output(target: Path, profile: ExportProfile, n_frames: int, ffprobe: str | Path | None = None) -> tuple[str | None, str | None]:
    """Prüft mit ffprobe Codec, Alpha-Pixelformat und Frame-Anzahl. Gibt (codec, pix_fmt) zurück."""
    exe = ffprobe or find_executable("ffprobe")
    if exe is None:
        log.warning("ffprobe nicht gefunden – Ausgabe wurde nicht geprüft")
        return None, None
    if profile.is_sequence:
        files = sorted(target.glob(profile.frame_glob))
        if len(files) != n_frames:
            raise ExportError(f"Es wurden {len(files)} statt {n_frames} Bilder geschrieben.")
        probe_target = files[0]
    else:
        probe_target = target
    try:
        res = run([exe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                   "stream=codec_name,pix_fmt,nb_frames", "-of", "json", probe_target], timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        # Die Datei ist fertig geschrieben – dass ffprobe nicht startet (z. B. von Windows blockiert),
        # darf den Export nicht nachträglich scheitern lassen.
        log.warning("Ausgabe nicht geprüft: %s", exc)
        return None, None
    if res.returncode != 0:
        raise ExportError(f"Ausgabedatei ist nicht lesbar: {res.stderr.strip()}")
    streams = json.loads(res.stdout).get("streams") or [{}]
    st = streams[0]
    pix = st.get("pix_fmt")
    if profile.alpha_pix_fmts and pix not in profile.alpha_pix_fmts:
        raise ExportError(f"Ausgabe hat kein Alpha-Pixelformat (pix_fmt={pix}).")
    if not profile.is_sequence and st.get("nb_frames") not in (None, "N/A"):
        if profile.with_video:
            # Video mit schwankender Bildrate: einzelne Frames mehr/weniger sind normal
            if abs(int(st["nb_frames"]) - n_frames) > max(2, n_frames // 100):
                raise ExportError(f"Video enthält {st['nb_frames']} statt {n_frames} Frames.")
        elif int(st["nb_frames"]) != n_frames:
            raise ExportError(f"Video enthält {st['nb_frames']} statt {n_frames} Frames.")
    return st.get("codec_name"), pix


def run_export(
    frame_source: Callable[[int], bytes],
    n_frames: int,
    width: int,
    height: int,
    fps,
    profile: ExportProfile,
    out: str | os.PathLike,
    *,
    ffmpeg: str | Path | None = None,
    ffprobe: str | Path | None = None,
    progress: Callable[[Progress], None] | None = None,
    cancel: threading.Event | None = None,
    metadata: dict[str, str] | None = None,
    verify: bool = True,
    background: str | os.PathLike | None = None,
    quality: str = "standard",
) -> ExportResult:
    """Rendert ``n_frames`` Frames über ``frame_source(i)`` und schreibt sie mit ffmpeg.

    Bei Profilen mit ``with_video`` werden die Frames über ``background`` (Video ab Sekunde 0,
    Frame i des Exports = Frame i des Videos) gelegt; der Ton des Videos wird übernommen.
    """
    if n_frames <= 0:
        raise ExportError("Der Export enthält keine Frames.")
    exe = ffmpeg or find_executable("ffmpeg")
    if exe is None:
        raise ExportError("ffmpeg wurde nicht gefunden – bitte installieren.")
    if not is_available(profile, str(exe)):
        raise ExportError(f"Das Format '{profile.title}' wird von dieser ffmpeg-Version nicht unterstützt "
                          f"(Encoder {profile.requires_encoder} fehlt).")
    if profile.even_size and (width % 2 or height % 2):
        raise ExportError(f"{profile.title} braucht eine gerade Breite und Höhe (eingestellt: {width}×{height}).")
    target = profile.output_target(Path(out))
    video_args: tuple[str, ...] | None = None
    encoder: str | None = None
    if profile.with_video:
        if background is None:
            raise ExportError("Für den Export mit Video muss ein Video geladen sein.")
        bg = Path(background)
        if not bg.is_file():
            raise ExportError(f"Video nicht gefunden: {bg}")
        try:
            same = target.resolve() == bg.resolve()
        except OSError:
            same = False
        if same:
            raise ExportError("Die Zieldatei wäre das Originalvideo – bitte einen anderen Dateinamen wählen.")
        if profile.codec_family:
            from .profiles import available_encoders
            from .video_encoders import choose_encoder, encoder_args

            encoder = choose_encoder(profile.codec_family, str(exe), available_encoders(str(exe)), width, height)
            if encoder is None:
                raise ExportError(f"Kein nutzbarer Encoder für {profile.title} gefunden "
                                  f"(weder Hardware noch Software). Bitte ein anderes Format wählen.")
            choice = encoder_args(profile.codec_family, encoder, quality, width, height, _fps_fraction(fps))
            video_args = choice.args
            log.info("Export mit Video: Encoder %s", encoder)
        else:
            encoder = next((a for p_, a in zip(profile.codec_args, profile.codec_args[1:]) if p_ == "-c:v"), None)
    if not target.parent.exists():
        raise ExportError(f"Zielordner existiert nicht: {target.parent}")
    if not os.access(target.parent, os.W_OK):
        raise ExportError(f"Zielordner ist schreibgeschützt: {target.parent}")

    tmp: Path | None = None
    if profile.is_sequence:
        target.mkdir(exist_ok=True)
        # Bilder eines früheren Exports in denselben Ordner entfernen – sonst mischen sich alte und neue
        for old in target.glob(profile.frame_glob):
            old.unlink()
        ff_out = target / profile.frame_pattern
    else:
        tmp = target.with_name(target.name + ".part")
        ff_out = tmp

    writer = FFmpegWriter(build_command(exe, profile, width, height, fps, ff_out, metadata,
                                        background=background, video_args=video_args), width * height * 4)
    writer.start()
    t_start = time.perf_counter()
    try:
        for i in range(n_frames):
            if cancel is not None and cancel.is_set():
                raise ExportCancelled("Export abgebrochen.")
            writer.write(frame_source(i))
            if progress is not None:
                el = time.perf_counter() - t_start
                rate = (i + 1) / el if el > 0 else 0.0
                progress(Progress(i + 1, n_frames, el, rate, (n_frames - i - 1) / rate if rate else 0.0))
        writer.finish()
    except BaseException:
        writer.abort()
        _cleanup(profile, target, tmp)
        raise

    if tmp is not None:
        os.replace(tmp, target)
    codec = pix = None
    if verify:
        codec, pix = verify_output(target, profile, n_frames, ffprobe)
    size = sum(f.stat().st_size for f in target.glob(profile.frame_glob)) if profile.is_sequence else target.stat().st_size
    return ExportResult(target, n_frames, time.perf_counter() - t_start, codec, pix, size, encoder)
