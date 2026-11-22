"""Video analysieren für die Sync-Zeitleiste (ohne Qt).

Ein einziger ffmpeg-Durchlauf liefert

- die **Bewegungskurve**: wie stark sich das (stark verkleinerte) Bild von Bild zu Bild ändert –
  steht das Rad, ist sie flach, beim Fahren schlägt sie aus;
- den **Filmstreifen**: kleine Vorschaubilder in gleichmäßigen Abständen.

:func:`suggest_offset` vergleicht die Bewegungskurve mit der Geschwindigkeit der Fahrt
(normierte Kreuzkorrelation) und schlägt einen Versatz vor.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Callable

import numpy as np

from ..export.ffmpeg_locator import find_executable
from ..platform_utils import popen

log = logging.getLogger(__name__)

MOTION_RATE = 10.0  # Messungen je Sekunde
MOTION_SIZE = (128, 72)
#: Bildstreifen für die Drehung (Anteile der Höhe): Horizontbereich – dort wirkt fast nur die
#: Drehung, kaum die Vorwärtsfahrt (die Straße unten „fließt“ stark auseinander)
YAW_BAND = (0.15, 0.55)
THUMB_HEIGHT = 48
MAX_THUMBS = 160
CACHE_VERSION = 3
#: Fahrtwind: Tonenergie in diesem Frequenzband (Hz) – wächst stark mit dem Tempo
WIND_BAND = (50.0, 500.0)
AUDIO_RATE = 16000


class AnalysisCancelled(RuntimeError):
    pass


@dataclass
class VideoAnalysis:
    motion_t: np.ndarray  # Video-Zeit (s)
    motion: np.ndarray  # Bewegungsenergie, 0…1 normiert
    thumb_t: np.ndarray  # Video-Zeit der Vorschaubilder
    thumbs: np.ndarray  # (N, H, W, 3) uint8
    #: seitliche Bildverschiebung je Sekunde (Bildbreiten/s); + = Bild wandert nach links (Kamera dreht rechts)
    yaw: np.ndarray | None = None
    #: Fahrtwind = log10 der Tonenergie im Band WIND_BAND, auf motion_t (None = Video ohne Ton)
    wind: np.ndarray | None = None
    from_cache: bool = False  # aus dem Zwischenspeicher geladen (nicht neu berechnet)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.stem + ".tmp.npz")
        np.savez(tmp, motion_t=self.motion_t, motion=self.motion, thumb_t=self.thumb_t, thumbs=self.thumbs,
                 yaw=self.yaw if self.yaw is not None else np.zeros(0),
                 wind=self.wind if self.wind is not None else np.zeros(0), version=CACHE_VERSION)
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "VideoAnalysis | None":
        try:
            with np.load(path) as d:
                if int(d["version"]) != CACHE_VERSION:
                    return None
                yaw = d["yaw"] if d["yaw"].size else None
                wind = d["wind"] if d["wind"].size else None
                return cls(d["motion_t"], d["motion"], d["thumb_t"], d["thumbs"], yaw, wind)
        except (OSError, KeyError, ValueError):
            return None


def thumb_size(width: int, height: int, thumb_h: int = THUMB_HEIGHT) -> tuple[int, int]:
    w = max(2, int(round(width * thumb_h / max(height, 1))))
    return w + (w % 2), thumb_h


def thumb_interval(duration: float, max_thumbs: int = MAX_THUMBS) -> float:
    """Abstand der Vorschaubilder: höchstens ``max_thumbs`` Stück, mindestens 1 s."""
    return max(1.0, float(duration) / max_thumbs)


def cache_key(path: str | PathLike, width: int, height: int, duration: float) -> str:
    p = Path(path)
    try:
        st = p.stat()
        stamp = f"{st.st_size}-{int(st.st_mtime)}"
    except OSError:
        stamp = "x"
    raw = f"{p.resolve()}|{stamp}|{width}x{height}|{duration:.3f}|{MOTION_RATE}|{MOTION_SIZE}|{THUMB_HEIGHT}|v{CACHE_VERSION}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def motion_energy(frames: np.ndarray) -> np.ndarray:
    """Mittlere absolute Änderung zwischen aufeinanderfolgenden Graustufenbildern (erstes = zweites)."""
    if frames.shape[0] < 2:
        return np.zeros(frames.shape[0])
    f = frames.astype(np.float32)
    d = np.abs(np.diff(f, axis=0)).mean(axis=(1, 2))
    return np.r_[d[:1], d].astype(float)


def _hann2d(h: int, w: int) -> np.ndarray:
    return np.outer(np.hanning(h), np.hanning(w)).astype(np.float32)


def horizontal_shift(prev: np.ndarray, cur: np.ndarray, window: np.ndarray | None = None) -> tuple[float, float]:
    """Seitliche Verschiebung von ``prev`` zu ``cur`` per Phasenkorrelation (Pixel, auf Subpixel genau).

    Positiv = Bildinhalt wandert nach links (wie beim Schwenk der Kamera nach rechts).
    Gibt zusätzlich die Schärfe des Korrelationsgipfels zurück (0…1, klein = unzuverlässig).
    """
    a = prev.astype(np.float32)
    b = cur.astype(np.float32)
    a = a - a.mean()
    b = b - b.mean()
    if window is None:
        window = _hann2d(*a.shape)
    fa = np.fft.rfft2(a * window)
    fb = np.fft.rfft2(b * window)
    r = fa * np.conj(fb)
    mag = np.abs(r)
    if float(mag.max()) < 1e-6:
        return 0.0, 0.0
    corr = np.fft.irfft2(r / np.maximum(mag, 1e-9), s=a.shape)
    iy, ix = np.unravel_index(int(np.argmax(corr)), corr.shape)
    h, w = corr.shape
    peak = float(corr[iy, ix])
    # Subpixel: Parabel durch die Nachbarn in x
    left, right = corr[iy, (ix - 1) % w], corr[iy, (ix + 1) % w]
    den = left - 2 * peak + right
    frac = 0.5 * (left - right) / den if abs(den) > 1e-12 else 0.0
    dx = ix + float(np.clip(frac, -0.5, 0.5))
    if dx > w / 2:
        dx -= w
    return float(dx), float(np.clip(peak, 0.0, 1.0))


def normalize_motion(e: np.ndarray) -> np.ndarray:
    """Auf 0…1 bringen (robust gegen einzelne Ausreißer wie Schnitte oder Lichtblitze)."""
    e = np.asarray(e, dtype=float)
    if e.size == 0:
        return e
    hi = float(np.percentile(e, 98)) or float(e.max()) or 1.0
    return np.clip(e / hi, 0.0, 1.0)


def analyze_video(path: str | PathLike, width: int, height: int, duration: float, *,
                  cache_dir: Path | None = None, ffmpeg: str | None = None,
                  progress: Callable[[float], None] | None = None,
                  cancel: threading.Event | None = None) -> VideoAnalysis:
    """Bewegungskurve und Filmstreifen in einem ffmpeg-Durchlauf berechnen (mit Zwischenspeicher)."""
    key = cache_key(path, width, height, duration)
    cpath = cache_dir / f"video_{key}.npz" if cache_dir is not None else None
    if cpath is not None and cpath.exists():
        cached = VideoAnalysis.load(cpath)
        if cached is not None:
            cached.from_cache = True
            if progress:
                progress(1.0)
            return cached
    exe = str(ffmpeg or find_executable("ffmpeg") or "ffmpeg")
    mw, mh = MOTION_SIZE
    tw, th = thumb_size(width, height)
    interval = thumb_interval(duration)
    with tempfile.TemporaryDirectory(prefix="cyov_") as td:
        thumb_file = Path(td) / "thumbs.rgb"
        graph = (f"[0:v]split=2[a][b];"
                 f"[a]fps={MOTION_RATE:g},scale={mw}:{mh}:flags=area,format=gray[m];"
                 f"[b]fps=1/{interval:.6f}:start_time=0,scale={tw}:{th}:flags=area,format=rgb24[t]")
        cmd = [exe, "-v", "error", "-nostdin", "-i", str(path), "-filter_complex", graph, "-an",
               "-map", "[m]", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
               "-map", "[t]", "-f", "rawvideo", "-pix_fmt", "rgb24", "-y", str(thumb_file)]
        proc = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        err_chunks: list[bytes] = []
        err_thread = threading.Thread(target=lambda: err_chunks.append(proc.stderr.read() if proc.stderr else b""),
                                      daemon=True)
        err_thread.start()
        n = mw * mh
        expected = max(int(duration * MOTION_RATE), 1)
        energy: list[float] = []
        shifts: list[float] = []
        y0, y1 = int(mh * YAW_BAND[0]), max(int(mh * YAW_BAND[1]), int(mh * YAW_BAND[0]) + 4)
        win = _hann2d(y1 - y0, mw)
        prev: np.ndarray | None = None
        count = 0
        try:
            assert proc.stdout is not None
            while True:
                if cancel is not None and cancel.is_set():
                    raise AnalysisCancelled("Videoanalyse abgebrochen.")
                data = proc.stdout.read(n)
                if not data or len(data) < n:
                    break
                frame = np.frombuffer(data, np.uint8).reshape(mh, mw)
                if prev is None:
                    energy.append(0.0)
                    shifts.append(0.0)
                else:
                    energy.append(float(np.abs(frame.astype(np.int16) - prev.astype(np.int16)).mean()))
                    dx, sharp = horizontal_shift(prev[y0:y1], frame[y0:y1], win)
                    shifts.append(dx if sharp >= 0.05 else np.nan)
                prev = frame
                count += 1
                if progress and count % 50 == 0:
                    progress(min(count / expected, 0.99))
            proc.wait(timeout=60)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
            err_thread.join(timeout=5)
        if proc.returncode != 0 or not energy:
            msg = b"".join(err_chunks).decode("utf-8", "replace").strip()[-400:]
            raise RuntimeError(f"Video konnte nicht analysiert werden. {msg}".strip())
        raw = thumb_file.read_bytes() if thumb_file.exists() else b""
    e = np.asarray(energy)
    if e.size > 1:
        e[0] = e[1]
    motion = normalize_motion(e)
    motion_t = np.arange(motion.size) / MOTION_RATE
    yaw = np.asarray(shifts, dtype=float)
    if yaw.size > 1:
        yaw[0] = yaw[1]
    yaw = _fill_nan(yaw) * MOTION_RATE / mw  # Bildbreiten je Sekunde
    tb = tw * th * 3
    k = len(raw) // tb
    thumbs = np.frombuffer(raw[: k * tb], np.uint8).reshape(k, th, tw, 3) if k else np.zeros((0, th, tw, 3), np.uint8)
    thumb_t = np.arange(k) * interval
    wind = None
    try:
        wind = audio_wind(path, motion_t, ffmpeg=exe, cancel=cancel)
    except AnalysisCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 – ohne Ton geht es trotzdem
        log.info("Kein Fahrtwind-Signal: %s", exc)
    res = VideoAnalysis(motion_t, motion, thumb_t, thumbs, yaw, wind)
    if cpath is not None:
        try:
            res.save(cpath)
        except OSError as exc:
            log.warning("Videoanalyse nicht zwischengespeichert: %s", exc)
    if progress:
        progress(1.0)
    return res


def audio_wind(path: str | PathLike, times: np.ndarray, *, ffmpeg: str | None = None,
               cancel: threading.Event | None = None, hop_s: float = 0.1) -> np.ndarray | None:
    """Fahrtwind aus der Tonspur: log10 der Energie im Band ``WIND_BAND`` je ``hop_s``, auf ``times``.

    Das Windgeräusch am Mikrofon wächst stark mit dem Tempo – unabhängig von Stabilisierung,
    Mitfahrern im Bild oder Licht. Gibt None zurück, wenn das Video keinen Ton hat.
    """
    exe = str(ffmpeg or find_executable("ffmpeg") or "ffmpeg")
    cmd = [exe, "-v", "error", "-nostdin", "-i", str(path), "-vn", "-map", "0:a:0?", "-ac", "1",
           "-ar", str(AUDIO_RATE), "-f", "s16le", "pipe:1"]
    proc = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    hop = int(AUDIO_RATE * hop_s)
    win = np.hanning(hop)
    freqs = np.fft.rfftfreq(hop, 1 / AUDIO_RATE)
    band = (freqs >= WIND_BAND[0]) & (freqs < WIND_BAND[1])
    energy: list[np.ndarray] = []
    try:
        assert proc.stdout is not None
        chunk = hop * 2 * 200  # 20 s je Lesevorgang
        rest = b""
        while True:
            if cancel is not None and cancel.is_set():
                raise AnalysisCancelled("Videoanalyse abgebrochen.")
            data = proc.stdout.read(chunk)
            if not data:
                break
            data = rest + data
            n = len(data) // (hop * 2)
            rest = data[n * hop * 2:]
            if n:
                x = np.frombuffer(data[: n * hop * 2], dtype="<i2").astype(np.float32).reshape(n, hop) / 32768.0
                spec = np.abs(np.fft.rfft(x * win, axis=1)) ** 2
                energy.append(spec[:, band].sum(axis=1))
        proc.wait(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
    if not energy:
        return None
    e = np.concatenate(energy)
    if e.size < 10 or not np.any(e > 0):
        return None
    le = np.log10(e + 1e-10)
    # Stille am Anfang (Encoder-Vorlauf) oder Aussetzer liefern extreme Tiefen, die den Vergleich
    # verzerren – auf das 1-%-Perzentil begrenzen
    le = np.clip(le, float(np.percentile(le, 1)), None)
    te = (np.arange(le.size) + 0.5) * hop_s
    return np.interp(np.asarray(times, dtype=float), te, le)


def _fill_nan(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=float).copy()
    ok = np.isfinite(v)
    if not ok.any():
        return np.zeros(v.size)
    if not ok.all():
        idx = np.arange(v.size)
        v[~ok] = np.interp(idx[~ok], idx[ok], v[ok])
    return v


# --------------------------------------------------------------------------- Auto-Sync


@dataclass
class SyncSuggestion:
    offset_s: float  # vorgeschlagener Versatz (wie Project.offset_s)
    track_start: float  # Track-Zeit, an der das Video dann beginnt
    score: float  # kombinierte Korrelation am Vorschlag (−1…1)
    runner_up: float  # beste Korrelation an einer deutlich anderen Stelle
    confidence: str  # "hoch" | "mittel" | "niedrig"
    speed_score: float | None = None  # Bewegung ↔ Geschwindigkeit
    turn_score: float | None = None  # Drehung im Video ↔ Kurven der Strecke
    rank: int = 1
    still_fraction: float = 0.0  # Anteil des Clips, der dort auf Stillstand in der Fahrt fällt
    wind_score: float | None = None  # Fahrtwind im Ton ↔ Tempo

    @property
    def usable(self) -> bool:
        return self.confidence != "niedrig"

    @property
    def basis(self) -> str:
        parts = []
        if self.wind_score is not None:
            parts.append(f"Fahrtwind r = {self.wind_score:.2f}")
        if self.turn_score is not None:
            parts.append(f"Kurven r = {self.turn_score:.2f}")
        if self.speed_score is not None:
            parts.append(f"Tempo r = {self.speed_score:.2f}")
        parts.append(f"Stillstand {self.still_fraction * 100:.0f} %")
        return ", ".join(parts)


GRID_DT = 0.2  # Raster für den Vergleich (s)
#: Höchstens dieser Anteil der Cliplänge darf auf Stillstand in der Fahrt fallen
MAX_STILL_FRACTION = 0.10
#: Tempo, unter dem die Fahrt als Stillstand gilt (m/s, ≈ 3,6 km/h)
STILL_SPEED = 1.0
#: Bewegung im Video (0…1), unter der das Bild als „steht“ gilt
STILL_MOTION = 0.12
#: Mindest-Tempo, ab dem die GPS-Fahrtrichtung zuverlässig ist (m/s, ≈ 8 km/h)
TURN_MIN_SPEED = 2.2


def _zscore(x: np.ndarray) -> np.ndarray | None:
    x = np.asarray(x, dtype=float)
    sd = float(x.std())
    if not np.isfinite(sd) or sd < 1e-9:
        return None
    return (x - x.mean()) / sd


def _box(v: np.ndarray, n: int) -> np.ndarray:
    if n <= 1 or v.size < n:
        return np.asarray(v, dtype=float)
    k = np.ones(n) / n
    return np.convolve(v, k, mode="same")


def normalized_xcorr(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Normierte Kreuzkorrelation von ``a`` (kurz) an jeder Stelle von ``b`` (lang), nur volle Überlappung.

    Ergebnis[k] = Pearson-Korrelation von a mit b[k : k+len(a)]. Abschnitte von ``b`` ohne Schwankung → 0.
    """
    m, n = a.size, b.size
    if m < 2 or n < m:
        return np.zeros(0)
    za = _zscore(a)
    if za is None:
        return np.zeros(n - m + 1)
    size = 1 << int(np.ceil(np.log2(n + m)))
    num = np.fft.irfft(np.fft.rfft(b, size) * np.conj(np.fft.rfft(za, size)), size)[: n - m + 1]
    s = np.r_[0.0, np.cumsum(b)]
    q = np.r_[0.0, np.cumsum(b * b)]
    mean = (s[m:] - s[:-m]) / m
    var = (q[m:] - q[:-m]) / m - mean ** 2
    out = np.zeros(n - m + 1)
    ok = var > 1e-9 * max(float(np.max(np.abs(b))) ** 2, 1.0)
    out[ok] = num[ok] / (m * np.sqrt(var[ok]))
    return np.clip(out, -1.0, 1.0)


def _speed_feature(v: np.ndarray) -> np.ndarray:
    # Bewegung im Bild wächst unterproportional mit dem Tempo – Wurzel gleicht das etwas an
    return np.sqrt(np.clip(np.nan_to_num(np.asarray(v, dtype=float)), 0.0, None))


def track_turn_rate(t: np.ndarray, lat: np.ndarray, lon: np.ndarray, distance: np.ndarray,
                    speed: np.ndarray) -> np.ndarray:
    """Drehrate der Fahrtrichtung aus der GPX (Grad/s, + = Rechtskurve); langsame Abschnitte = 0."""
    from ..rendering.minimap_geometry import compute_heading

    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    ok = np.isfinite(lat) & np.isfinite(lon)
    if ok.sum() < 3:
        return np.zeros(np.asarray(t).size)
    lat0, lon0 = float(np.nanmean(lat[ok])), float(np.nanmean(lon[ok]))
    x = (lon - lon0) * 111_195.0 * np.cos(np.radians(lat0))
    y = (lat - lat0) * 111_195.0
    h = compute_heading(x, y, np.asarray(distance, dtype=float), window_m=15.0, smooth_s=1)
    rate = np.gradient(h, np.asarray(t, dtype=float))
    rate = np.where(np.nan_to_num(np.asarray(speed, dtype=float)) >= TURN_MIN_SPEED, rate, 0.0)
    rate = np.clip(np.nan_to_num(rate), -90.0, 90.0)
    return _box(rate, 3)


def _video_on_grid(t: np.ndarray, v: np.ndarray, grid: np.ndarray, smooth_s: float) -> np.ndarray:
    rate = 1.0 / max(float(np.median(np.diff(t))) if t.size > 1 else 1.0, 1e-6)
    vs = _box(np.asarray(v, dtype=float), int(round(smooth_s * rate)))
    return np.interp(grid, t, vs)


def _confidence(best: float, gap: float) -> str:
    if best >= 0.7 and gap >= 0.12:
        return "hoch"
    if best >= 0.45 and gap >= 0.04:
        return "mittel"
    return "niedrig"


SIGNAL_MODES = {"auto": "Automatisch", "audio": "Ton", "video": "Bild", "all": "Alle"}
#: Glättung des Fahrtwinds (s) – einzelne Geräusche (Schalten, Rufe) fallen weniger ins Gewicht
WIND_SMOOTH_S = 2.4


def _wind_track_feature(speed: np.ndarray) -> np.ndarray:
    # Windgeräusch wächst etwa logarithmisch hörbar mit dem Tempo; +3 m/s dämpft den Stillstand
    return np.log(np.clip(np.nan_to_num(np.asarray(speed, dtype=float)), 0.0, None) + 3.0)


def _wind_curve(motion_t, wind, vid_grid, tr_grid, track_t, speed) -> np.ndarray | None:
    if wind is None or np.asarray(wind).size != np.asarray(motion_t).size:
        return None
    a = _video_on_grid(motion_t, np.nan_to_num(np.asarray(wind, dtype=float)), vid_grid, WIND_SMOOTH_S)
    if _zscore(a) is None:
        return None
    c = normalized_xcorr(a, np.interp(tr_grid, track_t, _wind_track_feature(speed)))
    return c if np.any(c) else None


def _choose_curves(signals: str, c_speed, c_turn, c_wind) -> list[np.ndarray]:
    if signals not in SIGNAL_MODES:
        raise ValueError(f"Unbekannte Signalwahl: {signals}")
    video = [c for c in (c_speed if c_speed is not None and np.any(c_speed) else None, c_turn) if c is not None]
    if signals == "audio":
        return [c_wind] if c_wind is not None else []
    if signals == "video":
        return video
    if signals == "all":
        return video + ([c_wind] if c_wind is not None else [])
    return [c_wind] if c_wind is not None else video


def suggest_offsets(motion_t: np.ndarray, motion: np.ndarray, track_t: np.ndarray, speed: np.ndarray,
                    base_offset_s: float, fps: float = 30.0, *, yaw: np.ndarray | None = None,
                    turn_rate: np.ndarray | None = None, count: int = 5,
                    exclusion_s: float = 15.0, track_moving: np.ndarray | None = None,
                    max_still_fraction: float = MAX_STILL_FRACTION, wind: np.ndarray | None = None,
                    signals: str = "auto") -> list[SyncSuggestion]:
    """Bis zu ``count`` Versatz-Vorschläge, beste zuerst.

    Verglichen werden (je nachdem, was vorhanden ist) die Bewegung im Video mit der Geschwindigkeit
    und die Drehung im Video mit den Kurven der Strecke. Das Vorzeichen der Drehung ist unbekannt
    (Kamera gespiegelt?) – beide Richtungen werden geprüft.

    ``signals`` wählt die Vergleiche: ``"audio"`` (Fahrtwind ↔ Tempo), ``"video"`` (Bewegung und
    Drehung im Bild), ``"all"`` oder ``"auto"`` = Fahrtwind, wenn das Video Ton hat, sonst Bild.
    Bei stabilisierten Kameras (Insta360 FlowState u. a.) tragen die Bildsignale kaum – dort zählt
    der Fahrtwind.

    Stellen, an denen die Fahrt zu lange stillsteht, scheiden aus: höchstens ``max_still_fraction``
    der Cliplänge darf auf Stillstand fallen – mehr nur, wenn auch das Video selbst so lange steht
    (z. B. ein Ampelstopp im Clip). ``track_moving`` (bool je Track-Punkt) überschreibt die
    Erkennung über das Tempo (z. B. mit Aufzeichnungspausen).
    """
    motion_t = np.asarray(motion_t, dtype=float)
    track_t = np.asarray(track_t, dtype=float)
    if motion_t.size < 10 or track_t.size < 2:
        return []
    vid_grid = np.arange(0.0, motion_t[-1] + 1e-9, GRID_DT)
    tr_grid = np.arange(float(track_t[0]), float(track_t[-1]) + 1e-9, GRID_DT)
    a = np.sqrt(np.clip(_video_on_grid(motion_t, motion, vid_grid, 0.4), 0.0, None))
    b = np.interp(tr_grid, track_t, _speed_feature(speed))
    c_speed = normalized_xcorr(a, b)
    if c_speed.size == 0:
        return []
    c_turn = None
    if yaw is not None and turn_rate is not None and np.asarray(yaw).size == motion_t.size:
        ay = _video_on_grid(motion_t, np.nan_to_num(np.asarray(yaw, dtype=float)), vid_grid, 1.0)
        by = np.interp(tr_grid, track_t, np.asarray(turn_rate, dtype=float))
        pos = normalized_xcorr(ay, by)
        if pos.size == c_speed.size and np.any(pos):
            c_turn = pos if pos.max() >= (-pos).max() else -pos
    c_wind = _wind_curve(motion_t, wind, vid_grid, tr_grid, track_t, speed)
    if c_wind is not None and c_wind.size != c_speed.size:
        c_wind = None
    chosen = _choose_curves(signals, c_speed, c_turn, c_wind)
    if not chosen:
        return []
    combined = np.mean(chosen, axis=0)
    combined = combined.copy()
    # --- Stillstand: Fenster mit zu viel Stillstand in der Fahrt ausschließen
    moving = (np.nan_to_num(np.asarray(speed, dtype=float)) >= STILL_SPEED) if track_moving is None \
        else np.asarray(track_moving, dtype=bool)
    still_grid = np.interp(tr_grid, track_t, (~moving).astype(float)) > 0.5
    m = vid_grid.size
    cs = np.r_[0.0, np.cumsum(still_grid)]
    still_frac = (cs[m:] - cs[:-m]) / m
    video_still = float(np.mean(np.interp(vid_grid, motion_t, np.asarray(motion, dtype=float)) < STILL_MOTION))
    allowed = max(max_still_fraction, min(video_still + 0.05, 0.9))
    still_frac = still_frac[: combined.size]
    excluded = still_frac > allowed + 1e-9
    # Übereinstimmung „fährt/steht“ zwischen Video und Fahrt als kleiner Bonus/Malus
    combined -= 0.3 * np.abs(still_frac - min(video_still, 1.0))
    combined[excluded] = -2.0
    if not np.any(combined > -2.0):
        return []
    excl = int(round(exclusion_s / GRID_DT))
    work = combined.copy()
    peaks: list[int] = []
    for _ in range(max(count, 1) + 1):
        k = int(np.argmax(work))
        if work[k] <= -1.5:
            break
        peaks.append(k)
        work[max(k - excl, 0): k + excl + 1] = -2.0
    if not peaks:
        return []
    has_speed = any(c is c_speed for c in chosen)
    use_turn = c_turn if any(c is c_turn for c in chosen) else None
    use_wind = c_wind if any(c is c_wind for c in chosen) else None
    out: list[SyncSuggestion] = []
    for rank, k in enumerate(peaks[:count], start=1):
        frac = 0.0
        if 0 < k < combined.size - 1 and combined[k - 1] > -2.0 and combined[k + 1] > -2.0:
            y0, y1, y2 = combined[k - 1], combined[k], combined[k + 1]
            den = y0 - 2 * y1 + y2
            if abs(den) > 1e-12:
                frac = float(np.clip(0.5 * (y0 - y2) / den, -0.5, 0.5))
        start = float(tr_grid[0]) + (k + frac) * GRID_DT
        best = float(combined[k])
        # „zweitbester“ Vergleichswert: für Platz 1 der nächste Gipfel, für die anderen Platz 1
        other = float(combined[peaks[1]]) if rank == 1 and len(peaks) > 1 else (
            -1.0 if rank == 1 else float(combined[peaks[0]]))
        conf = _confidence(best, best - other) if rank == 1 else "Alternative"
        offset = round((start - base_offset_s) * fps) / fps if fps > 0 else start - base_offset_s
        out.append(SyncSuggestion(offset, start, best, other, conf,
                                  speed_score=float(c_speed[k]) if has_speed else None,
                                  turn_score=float(use_turn[k]) if use_turn is not None else None, rank=rank,
                                  still_fraction=float(still_frac[k]),
                                  wind_score=float(use_wind[k]) if use_wind is not None else None))
    return out


def suggest_offset(motion_t: np.ndarray, motion: np.ndarray, track_t: np.ndarray, speed: np.ndarray,
                   base_offset_s: float, fps: float = 30.0, exclusion_s: float = 15.0,
                   **kw) -> SyncSuggestion | None:
    """Bester Vorschlag (siehe :func:`suggest_offsets`)."""
    res = suggest_offsets(motion_t, motion, track_t, speed, base_offset_s, fps, exclusion_s=exclusion_s, **kw)
    return res[0] if res else None


@dataclass
class MatchScore:
    """Wie gut Video und Fahrt an einer bestimmten Stelle zusammenpassen."""

    combined: float  # −1…1 (wie SyncSuggestion.score)
    speed: float | None  # Korrelation Bewegung ↔ Tempo
    turn: float | None  # Korrelation Drehung ↔ Kurven (Betrag: Vorzeichen der Kamera unbekannt)
    still_fraction: float  # Anteil des Clips auf Stillstand in der Fahrt
    wind: float | None = None  # Korrelation Fahrtwind ↔ Tempo

    @property
    def percent(self) -> float:
        return max(0.0, min(self.combined, 1.0)) * 100.0

    def text(self) -> str:
        parts = []
        if self.wind is not None:
            parts.append(f"Fahrtwind {max(self.wind, 0) * 100:.0f} %")
        if self.speed is not None:
            parts.append(f"Tempo {max(self.speed, 0) * 100:.0f} %")
        if self.turn is not None:
            parts.append(f"Kurven {max(self.turn, 0) * 100:.0f} %")
        return f"{self.percent:.0f} %" + (f" ({', '.join(parts)})" if parts else "")


def _pearson(a: np.ndarray, b: np.ndarray) -> float | None:
    za, zb = _zscore(a), _zscore(b)
    if za is None or zb is None:
        return None
    return float(np.clip(np.mean(za * zb), -1.0, 1.0))


def match_at(track_start: float, motion_t: np.ndarray, motion: np.ndarray, track_t: np.ndarray,
             speed: np.ndarray, *, yaw: np.ndarray | None = None, turn_rate: np.ndarray | None = None,
             track_moving: np.ndarray | None = None, wind: np.ndarray | None = None,
             signals: str = "auto") -> MatchScore | None:
    """Übereinstimmung, wenn das Video bei Track-Zeit ``track_start`` beginnt (gleiche Merkmale wie Auto-Sync)."""
    motion_t = np.asarray(motion_t, dtype=float)
    track_t = np.asarray(track_t, dtype=float)
    if motion_t.size < 10 or track_t.size < 2:
        return None
    vid_grid = np.arange(0.0, motion_t[-1] + 1e-9, GRID_DT)
    at = track_start + vid_grid
    inside = (at >= track_t[0]) & (at <= track_t[-1])
    if inside.mean() < 0.5:
        return None
    vid_grid, at = vid_grid[inside], at[inside]
    a = np.sqrt(np.clip(_video_on_grid(motion_t, motion, vid_grid, 0.4), 0.0, None))
    b = np.interp(at, track_t, _speed_feature(speed))
    r_speed = _pearson(a, b)
    r_turn = None
    if yaw is not None and turn_rate is not None and np.asarray(yaw).size == motion_t.size:
        ay = _video_on_grid(motion_t, np.nan_to_num(np.asarray(yaw, dtype=float)), vid_grid, 1.0)
        by = np.interp(at, track_t, np.asarray(turn_rate, dtype=float))
        r = _pearson(ay, by)
        r_turn = abs(r) if r is not None else None
    r_wind = None
    if wind is not None and np.asarray(wind).size == motion_t.size:
        aw = _video_on_grid(motion_t, np.nan_to_num(np.asarray(wind, dtype=float)), vid_grid, WIND_SMOOTH_S)
        r_wind = _pearson(aw, np.interp(at, track_t, _wind_track_feature(speed)))
    use_video = signals in ("video", "all") or (signals == "auto" and r_wind is None)
    use_wind = signals in ("audio", "all") or (signals == "auto" and r_wind is not None)
    if not use_video:
        r_speed = r_turn = None
    if not use_wind:
        r_wind = None
    vals = [r for r in (r_speed, r_turn, r_wind) if r is not None]
    if not vals:
        return None
    moving = (np.nan_to_num(np.asarray(speed, dtype=float)) >= STILL_SPEED) if track_moving is None \
        else np.asarray(track_moving, dtype=bool)
    still = float(np.mean(np.interp(at, track_t, (~moving).astype(float)) > 0.5))
    video_still = float(np.mean(np.interp(vid_grid, motion_t, np.asarray(motion, dtype=float)) < STILL_MOTION))
    combined = float(np.mean(vals)) - 0.3 * abs(still - video_still)
    return MatchScore(combined, r_speed, r_turn, still, r_wind)


def sync_diagnostics(path: str | PathLike, analysis: VideoAnalysis, track_t: np.ndarray, channels: dict,
                     pause_mask: np.ndarray, base_offset_s: float, fps: float,
                     true_start: float | None = None) -> str:
    """Rohsignale und Auto-Sync-Ergebnis für die Auswertung speichern (``.npz`` + ``.txt``).

    ``true_start`` = Track-Zeit des Videobeginns beim (bekannt) richtigen Sync. Gibt den Bericht zurück.
    """
    from .units import format_duration

    ch = channels
    turn = track_turn_rate(track_t, ch["lat"], ch["lon"], ch["distance"], ch["speed"])
    moving = (np.nan_to_num(ch["speed"]) >= 1.0) & ~np.asarray(pause_mask, dtype=bool)
    kw = dict(yaw=analysis.yaw, turn_rate=turn, track_moving=moving, wind=analysis.wind)
    sugg = suggest_offsets(analysis.motion_t, analysis.motion, track_t, ch["speed"], base_offset_s, fps, **kw)
    yaw = analysis.yaw if analysis.yaw is not None else np.zeros(0)
    lines = [f"Clip: {analysis.motion_t[-1]:.1f} s, {analysis.motion.size} Messungen",
             f"Bewegung: Ø {analysis.motion.mean():.3f}, Anteil < {STILL_MOTION}: "
             f"{np.mean(analysis.motion < STILL_MOTION) * 100:.0f} %",
             f"Drehung: Ø |{np.abs(yaw).mean() if yaw.size else 0:.4f}| Bildbreiten/s, "
             f"Anteil exakt 0: {np.mean(yaw == 0) * 100 if yaw.size else 100:.0f} %",
             "Ton (Fahrtwind): " + ("vorhanden" if analysis.wind is not None else "kein Ton im Video")]
    for sg in sugg:
        lines.append(f"Vorschlag {sg.rank}: Track {format_duration(sg.track_start)} · {sg.score * 100:.0f} % "
                     f"({sg.basis}) · {sg.confidence}")
    if true_start is not None:
        sc = match_at(true_start, analysis.motion_t, analysis.motion, track_t, ch["speed"], **kw)
        lines.append(f"Richtige Stelle Track {format_duration(true_start)} ({true_start:.3f} s): "
                     f"{sc.text() if sc else 'nicht berechenbar'}")
        if sc is not None:
            lines.append(f"  Kurven roh (mit Vorzeichen): {_raw_turn_r(true_start, analysis, track_t, turn):+.3f}, "
                         f"Tempo r: {sc.speed if sc.speed is not None else float('nan'):+.3f}, "
                         f"Stillstand {sc.still_fraction * 100:.0f} %")
    report = "\n".join(lines)
    p = Path(path)
    np.savez_compressed(p, motion_t=analysis.motion_t, motion=analysis.motion, yaw=yaw,
                        wind=analysis.wind if analysis.wind is not None else np.zeros(0), track_t=track_t,
                        speed=ch["speed"], turn=turn, moving=moving, lat=ch["lat"], lon=ch["lon"],
                        distance=ch["distance"], base=base_offset_s, fps=fps,
                        true_start=np.nan if true_start is None else true_start)
    p.with_suffix(".txt").write_text(report + "\n", encoding="utf-8")
    return report


def _raw_turn_r(start: float, analysis: VideoAnalysis, track_t: np.ndarray, turn: np.ndarray) -> float:
    if analysis.yaw is None:
        return float("nan")
    vid_grid = np.arange(0.0, analysis.motion_t[-1] + 1e-9, GRID_DT)
    ay = _video_on_grid(analysis.motion_t, np.nan_to_num(analysis.yaw), vid_grid, 1.0)
    by = np.interp(start + vid_grid, track_t, turn)
    r = _pearson(ay, by)
    return float("nan") if r is None else r
