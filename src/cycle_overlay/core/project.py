"""Projekt: alle Einstellungen einer Bearbeitung + geladene Daten (ohne Qt).

Die GUI arbeitet ausschließlich über ``ProjectModel``; so ist die Logik ohne
Oberfläche testbar. Projektdateien (``.cyov``) sind JSON.

Zeitbegriffe:
- Track-Zeit: Sekunden seit Beginn der GPX-Aufzeichnung.
- Timeline-Zeit: Position des Schiebereglers. Mit Video = Zeit im Video,
  ohne Video = Track-Zeit (der ganze Track ist sichtbar, exportiert wird In bis Out).
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field, fields, replace
from fractions import Fraction
from os import PathLike
from pathlib import Path

import numpy as np

from ..export.profiles import (
    DEFAULT_PROFILE,
    DEFAULT_VIDEO_PROFILE,
    OVERLAY_PROFILE_KEYS,
    PROFILES,
    VIDEO_PROFILE_KEYS,
    ExportProfile,
    estimate_size_bytes,
)
from ..export.video_encoders import QUALITIES
from ..rendering.layout import MAP_CHANNEL, Layout, WidgetConfig, Zone
from ..rendering.values import CLOCK_CHANNEL, FrameValues
from .osm import MapData, MapDataError, fetch_map_data
from .track_loader import load_track_bytes
from .models import RawTrack, Telemetry, VideoInfo
from .presets import load_preset
from .processor import (
    FILTER_DEFAULTS,
    FILTER_LEVELS,
    FILTER_TABLE,
    ProcessingConfig,
    config_for,
    frame_times,
    parse_fps,
    process,
)
from .sync import CAMERA_PROFILES, TIME_SOURCES, SyncCheck, VideoSync, get_profile
from .units import CHANNELS
from .video_probe import probe

log = logging.getLogger(__name__)

PROJECT_SUFFIX = ".cyov"
PROJECT_VERSION = 1

SIZE_PRESETS: list[tuple[str, int, int]] = [
    ("1280x720 (HD)", 1280, 720),
    ("1920x1080 (Full HD)", 1920, 1080),
    ("2560x1440 (1440p QHD)", 2560, 1440),
    ("2704x1520 (GoPro 2.7K)", 2704, 1520),
    ("3840x2160 (4K UHD)", 3840, 2160),
    ("1080x1920 (Hochformat)", 1080, 1920),
]
FPS_PRESETS = ["23.976", "24", "25", "29.97", "30", "50", "59.94", "60"]


@dataclass
class ExportSettings:
    size_from_video: bool = True
    width: int = 1920
    height: int = 1080
    fps_from_video: bool = True
    fps: str = "30"
    profile: str = DEFAULT_PROFILE  # Format für „Nur Anzeigen“ (mit Transparenz)
    out_dir: str | None = None
    filename: str | None = None  # ohne Endung; None = automatisch
    #: "overlay" = nur die Anzeigen (transparent) · "video" = Video mit eingebrannten Anzeigen
    content: str = "overlay"
    #: Format für „Video mit Anzeigen“
    video_profile: str = DEFAULT_VIDEO_PROFILE
    #: Qualität für H.264/H.265 ("standard" | "high")
    quality: str = "standard"

    def normalize(self) -> None:
        """Unbekannte Werte (ältere/fremde Projektdateien) auf Standard setzen."""
        if self.profile not in OVERLAY_PROFILE_KEYS:
            self.profile = DEFAULT_PROFILE
        if self.video_profile not in VIDEO_PROFILE_KEYS:
            self.video_profile = DEFAULT_VIDEO_PROFILE
        if self.content not in EXPORT_CONTENTS:
            self.content = "overlay"
        if self.quality not in QUALITIES:
            self.quality = "standard"


#: Was exportiert wird
EXPORT_CONTENTS = {"overlay": "Nur Anzeigen", "video": "Video mit Anzeigen"}


@dataclass
class Project:
    gpx_path: str | None = None
    video_path: str | None = None
    camera: str = "auto"
    timezone: str | None = None
    camera_time_is_end: bool = False
    #: "auto" | "metadata" | "track_start" (siehe sync.TIME_SOURCES)
    time_source: str = "auto"
    offset_s: float = 0.0
    in_s: float = 0.0  # ohne Video: Export-Bereich (Track-Zeit)
    out_s: float | None = None
    units: str = "metric"
    pause_mode: str = "zero"
    #: Filterstärke je Kanal ("off" | "low" | "medium" | "strong"); fehlende Kanäle = Standard
    filters: dict = field(default_factory=dict)
    #: Zahlen zwischen zwei Aktualisierungen durchzählen statt springen
    count_numbers: bool = True
    layout: Layout = field(default_factory=lambda: load_preset())
    export: ExportSettings = field(default_factory=ExportSettings)

    def to_dict(self, base: Path | None = None) -> dict:
        d = {f.name: getattr(self, f.name) for f in fields(self) if f.name not in ("layout", "export")}
        d["layout"] = self.layout.to_dict()
        d["export"] = asdict(self.export)
        d["version"] = PROJECT_VERSION
        if base is not None:  # zusätzlich relative Pfade, falls der Ordner verschoben wird
            for key in ("gpx_path", "video_path"):
                if d[key]:
                    try:
                        d[key + "_rel"] = str(Path(d[key]).resolve().relative_to(base.resolve()))
                    except ValueError:
                        pass
        return d

    @classmethod
    def from_dict(cls, d: dict, base: Path | None = None) -> "Project":
        known = {f.name for f in fields(cls)} - {"layout", "export"}
        p = cls(**{k: v for k, v in d.items() if k in known})
        if p.timezone:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            try:
                ZoneInfo(str(p.timezone))
            except (ZoneInfoNotFoundError, ValueError):
                log.warning("Unbekannte Zeitzone im Projekt ignoriert: %s", p.timezone)
                p.timezone = None
        if p.time_source not in TIME_SOURCES:
            p.time_source = "auto"
        if not isinstance(p.filters, dict):
            p.filters = {}
        p.filters = {k: v for k, v in p.filters.items() if k in FILTER_TABLE and v in FILTER_LEVELS}
        if p.pause_mode not in ("zero", "hold"):
            p.pause_mode = "zero"
        p.count_numbers = bool(p.count_numbers)
        if "time_source" not in d and p.offset_s:
            # ältere Projekte: Offset bezog sich immer auf die Aufnahmezeit – so lassen
            p.time_source = "metadata"
        if "layout" in d:
            p.layout = Layout.from_dict(d["layout"])
        if "export" in d:
            ek = {f.name for f in fields(ExportSettings)}
            p.export = ExportSettings(**{k: v for k, v in d["export"].items() if k in ek})
            p.export.normalize()
        if base is not None:
            for key in ("gpx_path", "video_path"):
                path = getattr(p, key)
                rel = d.get(key + "_rel")
                if path and not Path(path).exists() and rel and (base / rel).exists():
                    setattr(p, key, str(base / rel))
        return p

    def save(self, path: str | PathLike) -> Path:
        path = Path(path)
        if path.suffix.lower() != PROJECT_SUFFIX:
            path = path.with_suffix(PROJECT_SUFFIX)
        path.write_text(json.dumps(self.to_dict(path.parent), indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | PathLike) -> "Project":
        path = Path(path)
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Projektdatei nicht lesbar: {exc}") from exc
        return cls.from_dict(d, path.parent)


@dataclass
class BatchJob:
    """Ein Clip im Stapel-Export."""

    video_path: Path
    video: VideoInfo | None = None
    sync: VideoSync | None = None
    check: SyncCheck | None = None
    track_times: np.ndarray | None = None
    fps: Fraction | None = None
    size: tuple[int, int] | None = None
    target: Path | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.check is not None and self.check.status != "outside"


@dataclass
class SignalRow:
    channel: str
    title: str
    available: bool
    coverage: float
    summary: str


class ProjectModel:
    """Projekt + geladene GPX/Video-Daten + abgeleitete Größen."""

    def __init__(self, project: Project | None = None):
        self.project = project or Project()
        self.raw: RawTrack | None = None
        self.telemetry: Telemetry | None = None
        self.video: VideoInfo | None = None
        self.sync: VideoSync | None = None
        self.sync_error: str | None = None
        #: Original-Bytes der Fahrtdatei (werden ins Projektpaket eingebettet)
        self.track_bytes: bytes | None = None
        self.track_name: str | None = None
        #: OpenStreetMap-Daten für die Minimap
        self.map_data: MapData | None = None

    # ------------------------------------------------------------------ Laden

    def load_gpx(self, path: str | PathLike) -> Telemetry:
        """GPX- oder FIT-Datei laden."""
        p = Path(path)
        try:
            data = p.read_bytes()
        except OSError as exc:
            raise ValueError(f"Datei nicht lesbar: {exc}") from exc
        tel = self._load_track_data(data, p.name)
        self.project.gpx_path = str(p)
        return tel

    def _load_track_data(self, data: bytes, name: str) -> Telemetry:
        raw = load_track_bytes(data, name)
        tel = process(raw, self.processing_config())
        changed_track = self.track_bytes is not None and self.track_bytes != data
        self.raw, self.telemetry = raw, tel
        self.track_bytes, self.track_name = data, name
        if changed_track:
            self.map_data = None  # Kartendaten gehören zur alten Strecke
        if self.project.out_s is None or self.project.out_s > tel.duration:
            self.project.in_s = 0.0
            self.project.out_s = min(tel.duration, 60.0)
        self._update_sync()
        return tel

    # ------------------------------------------------------------------ Kartendaten

    def download_map_data(self, buffer_m: float | None = None, progress=None, post=None, cancel=None) -> MapData:
        """OpenStreetMap-Daten für die Umgebung der Strecke laden (Internet nötig, mit Zwischenspeicher)."""
        if self.telemetry is None or not self.telemetry.available.get(MAP_CHANNEL):
            raise MapDataError("Für Kartendaten wird eine Fahrt mit GPS-Positionen benötigt.")
        from ..app_paths import session_cache_dir

        # Zwischenspeicher nur für diese Sitzung – wird beim Schließen des Projekts gelöscht
        from .osm import DEFAULT_BUFFER_M

        md = fetch_map_data(self.raw.lat, self.raw.lon,  # type: ignore[union-attr]
                            buffer_m=DEFAULT_BUFFER_M if buffer_m is None else buffer_m,
                            cache_dir=session_cache_dir() / "mapcache", progress=progress, post=post,
                            cancel=cancel)
        self.map_data = md
        return md

    def map_status(self) -> str:
        if self.map_data is None:
            return "keine Kartendaten (Minimap zeigt nur die Route)"
        c = self.map_data.counts()
        roads = sum(v for k, v in c.items() if k in ("major", "primary", "minor", "cycle", "path"))
        cov = self.map_data.coverage
        if cov is None and self.raw is not None:  # ältere Projekte: nachträglich bestimmen
            from .osm import route_coverage

            cov = self.map_data.coverage = route_coverage(self.map_data.features, self.raw.lat, self.raw.lon)[0]
        cov_txt = f" · Abdeckung {cov * 100:.0f} %" if cov is not None else ""
        warn = " – Lücken! Bitte neu laden." if cov is not None and cov < 0.9 else ""
        return (f"{roads} Wege, {c.get('water', 0) + c.get('waterline', 0)} Gewässer, "
                f"{c.get('green', 0)} Grünflächen{cov_txt}{warn}")

    # ------------------------------------------------------------------ Projektpaket

    def save(self, path: str | PathLike) -> Path:
        """Projekt als eigenständiges Paket speichern (Fahrt + Kartendaten eingebettet)."""
        return save_bundle(self, path)

    def relink_video(self, path: str | PathLike) -> VideoInfo:
        """Fehlendes Video neu verknüpfen (Offset und Einstellungen bleiben)."""
        offset = self.project.offset_s
        info = self.load_video(path)
        self.set_sync_options(offset_s=offset)
        return info

    @property
    def missing_media(self) -> list[str]:
        p = self.project
        out = []
        if p.video_path and self.video is None:
            out.append(p.video_path)
        if p.gpx_path and self.telemetry is None:
            out.append(p.gpx_path)
        return out

    def load_video(self, path: str | PathLike) -> VideoInfo:
        info = probe(path)
        self.video = info
        self.project.video_path = str(Path(path))
        self._update_sync()
        return info

    def clear_video(self) -> None:
        self.video = None
        self.sync = None
        self.sync_error = None
        self.project.video_path = None

    def reprocess(self) -> None:
        """Nach Änderung der Verarbeitungsoptionen (z. B. Pausenmodus) neu berechnen."""
        if self.raw is not None:
            self.telemetry = process(self.raw, self.processing_config())
            self._update_sync()

    def processing_config(self) -> ProcessingConfig:
        return config_for(self.project.filters, self.project.pause_mode)

    def filter_level(self, channel: str) -> str:
        return self.project.filters.get(channel, FILTER_DEFAULTS[channel])

    def set_filter(self, channel: str, level: str) -> None:
        """Filterstärke eines Kanals setzen und die Fahrt neu berechnen."""
        if channel not in FILTER_TABLE or level not in FILTER_LEVELS:
            raise ValueError(f"Unbekannter Filter: {channel}={level}")
        if level == FILTER_DEFAULTS[channel]:
            self.project.filters.pop(channel, None)
        else:
            self.project.filters[channel] = level
        self.reprocess()

    def set_pause_mode(self, mode: str) -> None:
        if mode not in ("zero", "hold"):
            raise ValueError(f"Unbekannter Pausenmodus: {mode}")
        self.project.pause_mode = mode
        self.reprocess()

    @classmethod
    def open(cls, path: str | PathLike) -> "ProjectModel":
        """Projekt öffnen: Paket (ZIP) oder ältere JSON-Projektdatei."""
        path = Path(path)
        if zipfile.is_zipfile(path):
            return open_bundle(path)
        m = cls(Project.load(path))
        p = m.project
        offset = p.offset_s
        in_s, out_s = p.in_s, p.out_s
        if p.gpx_path and Path(p.gpx_path).exists():
            m.load_gpx(p.gpx_path)
            p.in_s, p.out_s = in_s, out_s
        if p.video_path and Path(p.video_path).exists():
            m.load_video(p.video_path)
        p.offset_s = offset
        m._update_sync()
        return m

    # ------------------------------------------------------------------ Sync

    @property
    def camera_key(self) -> str:
        if self.project.camera != "auto":
            return self.project.camera
        return self.video.camera if self.video else "generic"

    def camera_profile(self):
        prof = get_profile(self.camera_key, self.project.timezone)
        if self.project.camera_time_is_end:
            prof = replace(prof, creation_time_is_end=True)
        return prof

    def _update_sync(self) -> None:
        self.sync = None
        self.sync_error = None
        if self.video is None or self.telemetry is None:
            return
        try:
            self.sync = VideoSync.from_video(self.video, self.telemetry.start_time, self.camera_profile(),
                                             self.project.offset_s, time_source=self.project.time_source,
                                             track_duration=self.telemetry.duration)
        except Exception as exc:  # noqa: BLE001 – Meldung für die Oberfläche
            self.sync_error = str(exc)

    def set_sync_options(self, *, camera: str | None = None, timezone: str | None | bool = False,
                         offset_s: float | None = None, camera_time_is_end: bool | None = None,
                         time_source: str | None = None) -> None:
        # erst alles prüfen, dann übernehmen – bei einem Fehler bleibt das Projekt unverändert
        if camera is not None and camera != "auto" and camera not in CAMERA_PROFILES:
            raise ValueError(f"Unbekanntes Kamera-Profil: {camera}")
        if time_source is not None and time_source not in TIME_SOURCES:
            raise ValueError(f"Unbekannter Zeitbezug: {time_source}")
        if timezone is not False and timezone:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            try:
                ZoneInfo(str(timezone))
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError(f"Unbekannte Zeitzone: {timezone} (z. B. Europe/Berlin)") from exc
        if camera is not None:
            self.project.camera = camera
        if timezone is not False:
            self.project.timezone = timezone or None
        if offset_s is not None:
            self.project.offset_s = float(offset_s)
        if camera_time_is_end is not None:
            self.project.camera_time_is_end = bool(camera_time_is_end)
        if time_source is not None:
            self.project.time_source = time_source
        self._update_sync()

    def sync_check(self) -> SyncCheck | None:
        if self.sync is None or self.telemetry is None:
            return None
        return self.sync.check(self.telemetry.duration)

    def apply_suggestion(self) -> bool:
        chk = self.sync_check()
        if chk is None or chk.suggested_offset_s is None:
            return False
        self.set_sync_options(offset_s=self.project.offset_s + chk.suggested_offset_s)
        return True

    def set_sync_point(self, video_t: float, track_t: float) -> float:
        """Moment im Video (``video_t``) gehört zu Track-Zeit ``track_t`` -> Offset setzen."""
        if self.sync is None:
            raise ValueError("Kein Video synchronisiert.")
        off = self.sync.offset_for_sync_point(video_t, track_t)
        self.set_sync_options(offset_s=off)
        return off

    # ------------------------------------------------------------------ Zeitachse

    @property
    def has_video(self) -> bool:
        return self.video is not None and self.sync is not None

    @property
    def ready(self) -> bool:
        return self.telemetry is not None

    def fps(self) -> Fraction:
        e = self.project.export
        if self.has_video and e.fps_from_video:
            return self.video.fps  # type: ignore[union-attr]
        return parse_fps(e.fps)

    def frame_size(self) -> tuple[int, int]:
        e = self.project.export
        if self.has_video and e.size_from_video:
            return self.video.width, self.video.height  # type: ignore[union-attr]
        return int(e.width), int(e.height)

    def timeline_duration(self) -> float:
        if self.has_video:
            return float(self.video.duration)  # type: ignore[union-attr]
        return self.telemetry.duration if self.telemetry else 0.0

    def track_time(self, timeline_t: float) -> float:
        if self.has_video:
            return float(self.sync.track_time(timeline_t))  # type: ignore[union-attr]
        return float(timeline_t)

    def timeline_time_for_track(self, track_t: float) -> float:
        if self.has_video:
            return track_t - float(self.sync.track_time(0.0))  # type: ignore[union-attr]
        return track_t

    def export_range(self) -> tuple[float, float]:
        """Export-Bereich in Timeline-Zeit."""
        if self.has_video:
            return 0.0, self.timeline_duration()
        dur = self.telemetry.duration if self.telemetry else 0.0
        a = min(max(self.project.in_s, 0.0), dur)
        b = self.project.out_s if self.project.out_s is not None else dur
        b = min(max(b, a), dur)
        return a, b

    def set_in_out(self, in_s: float | None = None, out_s: float | None = None) -> None:
        """Export-Bereich setzen. Wird nur In (oder nur Out) über die andere Marke hinaus
        verschoben, wandert die andere Marke mit und die Clip-Länge bleibt erhalten."""
        dur = self.telemetry.duration if self.telemetry else math.inf
        cur_in = self.project.in_s
        cur_out = self.project.out_s if self.project.out_s is not None else dur
        length = max(cur_out - cur_in, 1.0)
        if in_s is not None and out_s is not None:
            a, b = sorted((max(0.0, float(in_s)), max(0.0, float(out_s))))
        elif in_s is not None:
            a = max(0.0, float(in_s))
            b = cur_out if cur_out > a else min(a + length, dur)
        elif out_s is not None:
            b = max(0.0, float(out_s))
            a = cur_in if cur_in < b else max(b - length, 0.0)
        else:
            return
        self.project.in_s, self.project.out_s = a, b

    def n_frames(self) -> int:
        if self.has_video and self.project.export.fps_from_video:
            return int(self.video.n_frames)  # type: ignore[union-attr]
        a, b = self.export_range()
        return int(math.floor((b - a) * self.fps() + 1e-9))

    def export_track_times(self) -> np.ndarray:
        a, _ = self.export_range()
        return frame_times(self.track_time(a), self.n_frames(), self.fps())

    def clip_track_start(self) -> float:
        return self.track_time(self.export_range()[0])

    def frame_values(self, track_times, t0: float | None = None) -> FrameValues:
        if self.telemetry is None:
            raise ValueError("Keine GPX-Datei geladen.")
        a, b = self.export_range()
        return FrameValues(self.telemetry, np.atleast_1d(np.asarray(track_times, dtype=float)),
                           units=self.project.units, tz=self.project.timezone,
                           t0=self.clip_track_start() if t0 is None else t0,
                           clip=(self.track_time(a), self.track_time(b)), map_data=self.map_data,
                           count_numbers=self.project.count_numbers)

    # ------------------------------------------------------------------ Export

    @property
    def export_with_video(self) -> bool:
        """Video wird mit exportiert (nur wenn eines geladen ist)."""
        return self.has_video and self.project.export.content == "video"

    def active_profile(self) -> ExportProfile:
        e = self.project.export
        key = e.video_profile if self.export_with_video else e.profile
        return PROFILES.get(key) or PROFILES[DEFAULT_VIDEO_PROFILE if self.export_with_video else DEFAULT_PROFILE]

    def set_export_content(self, content: str) -> None:
        if content not in EXPORT_CONTENTS:
            raise ValueError(f"Unbekannter Exportinhalt: {content!r}")
        self.project.export.content = content

    def default_output(self) -> Path:
        e = self.project.export
        profile = self.active_profile()
        suffix = "_mit_daten" if profile.with_video else "_overlay"
        if e.out_dir:
            folder = Path(e.out_dir)
        elif self.project.video_path:
            folder = Path(self.project.video_path).parent
        elif self.project.gpx_path:
            folder = Path(self.project.gpx_path).parent
        else:
            folder = Path.home()
        if e.filename:
            name = e.filename
        elif self.project.video_path:
            name = Path(self.project.video_path).stem + suffix
        elif self.project.gpx_path:
            a, _ = self.export_range()
            hms = f"{int(a) // 3600:02d}{int(a) % 3600 // 60:02d}{int(a) % 60:02d}"
            name = f"{Path(self.project.gpx_path).stem}_{hms}_overlay"
        else:
            name = "overlay"
        return profile.output_target(folder / name)

    def estimated_size(self) -> int:
        w, h = self.frame_size()
        return estimate_size_bytes(self.active_profile(), w, h, self.fps(), self.n_frames())

    def batch_jobs(self, video_paths: list[str | PathLike]) -> list[BatchJob]:
        """Exportaufträge für mehrere Clips derselben Fahrt (z. B. GoPro-Kapitel).

        Kamera-Profil, Zeitzone und Offset des Projekts gelten für alle Clips.
        Die Ausgabe landet je Clip als ``<Clip>_overlay`` (im Exportordner bzw. neben dem Clip).
        """
        if self.telemetry is None:
            raise ValueError("Keine Fahrt geladen.")
        e = self.project.export
        profile = self.active_profile()
        suffix = "_mit_daten" if profile.with_video else "_overlay"
        jobs: list[BatchJob] = []
        for vp in video_paths:
            job = BatchJob(Path(vp))
            try:
                info = probe(vp)
                cam = self.project.camera if self.project.camera != "auto" else info.camera
                prof = get_profile(cam, self.project.timezone)
                if self.project.camera_time_is_end:
                    prof = replace(prof, creation_time_is_end=True)
                # Im Stapel würde „Auto“ alle unpassenden Clips an den Trackbeginn legen – dort zählt
                # die Aufnahmezeit, damit fremde Clips erkannt und übersprungen werden.
                ts = "metadata" if self.project.time_source == "auto" else self.project.time_source
                sync = VideoSync.from_video(info, self.telemetry.start_time, prof, self.project.offset_s,
                                            time_source=ts,
                                            track_duration=self.telemetry.duration)
                fps = info.fps if e.fps_from_video else parse_fps(e.fps)
                n = info.n_frames if e.fps_from_video else int(math.floor(info.duration * fps + 1e-9))
                job.video, job.sync, job.fps = info, sync, fps
                job.check = sync.check(self.telemetry.duration)
                job.track_times = frame_times(float(sync.track_time(0.0)), n, fps)
                job.size = (info.width, info.height) if e.size_from_video else (int(e.width), int(e.height))
                folder = Path(e.out_dir) if e.out_dir else Path(vp).parent
                job.target = profile.output_target(folder / f"{Path(vp).stem}{suffix}")
            except Exception as exc:  # noqa: BLE001 – pro Clip melden
                job.error = str(exc)
            jobs.append(job)
        return jobs

    # ------------------------------------------------------------------ Widgets

    def available_channels(self) -> list[str]:
        if self.telemetry is None:
            return []
        chans = [c for c in CHANNELS if self.telemetry.available.get(c)]
        if self.telemetry.available.get(MAP_CHANNEL):
            chans.append(MAP_CHANNEL)
        return chans + [CLOCK_CHANNEL]

    def signal_rows(self) -> list[SignalRow]:
        from .units import convert, unit_for

        rows: list[SignalRow] = []
        if self.telemetry is None:
            return rows
        u = self.project.units
        for ch, info in CHANNELS.items():
            if info.kind == "duration":
                continue
            st = self.telemetry.stats[ch]
            ok = bool(self.telemetry.available.get(ch))
            if ok and not math.isnan(st.max):
                unit = unit_for(ch, u)
                d = info.decimals
                mx = float(convert(ch, st.max, u)[0])
                if ch in ("distance", "elevation_gain", "elevation_loss"):
                    summary = f"{mx:.{d}f} {unit}"
                else:
                    mn = float(convert(ch, st.min, u)[0])
                    av = float(convert(ch, st.mean, u)[0])
                    summary = f"Ø {av:.{d}f}  ({mn:.{d}f}–{mx:.{d}f}) {unit}"
            else:
                summary = "nicht vorhanden"
            rows.append(SignalRow(ch, info.title, ok, st.coverage, summary))
        return rows

    def widget(self, wid: str) -> WidgetConfig | None:
        return next((w for w in self.project.layout.widgets if w.id == wid), None)

    def add_widget(self, channel: str, wtype: str = "text") -> WidgetConfig:
        # Streckenkarte und Signal "position" gehören zusammen
        if wtype in ("map", "minimap") or channel == MAP_CHANNEL:
            wtype, channel = (wtype if wtype in ("map", "minimap") else "minimap"), MAP_CHANNEL
        if wtype == "profile" and channel in (CLOCK_CHANNEL,):
            channel = "elevation"
        if wtype == "climb":
            channel = "elevation"
        base = channel
        n = 1
        ids = {w.id for w in self.project.layout.widgets}
        wid = base
        while wid in ids:
            n += 1
            wid = f"{base}_{n}"
        size = {"text": 0.085, "arc": 0.24, "bar": 0.09, "map": 0.3, "profile": 0.16, "minimap": 0.3, "climb": 0.13,
                "progress": 0.075, "gainloss": 0.11, "scope": 0.12}[wtype]
        cfg = WidgetConfig(id=wid, type=wtype, channel=channel, anchor="center", x=0.0, y=0.0, size=size)
        if wtype == "bar" and channel in ("heart_rate", "power"):
            cfg.update_rate_hz = 2.0
        if wtype in ("map", "profile", "minimap", "climb"):
            cfg.label_position = "top" if wtype == "profile" else "none"
            cfg.update_rate_hz = 2.0
        cfg.validate()
        self.project.layout.widgets.append(cfg)
        return cfg

    def add_from_template(self, key: str, channel: str | None = None) -> WidgetConfig:
        """Widget aus einer Vorlage (``rendering.templates``) anlegen – mit deren Einstellungen."""
        from ..rendering.templates import TEMPLATES, channels_for, even_zones

        tpl = TEMPLATES[key]
        if channel is None:
            options = channels_for(tpl, self.available_channels())
            if not options:
                raise ValueError(f"„{tpl.title}“ braucht ein Signal, das in dieser Fahrt fehlt.")
            channel = options[0]
        if not tpl.allows(channel):
            raise ValueError(f"„{tpl.title}“ passt nicht zum Signal {channel!r}.")
        cfg = self.add_widget(channel, tpl.wtype)
        try:
            for k, val in tpl.cfg.items():
                setattr(cfg, k, val)
            for k, val in tpl.style.items():
                setattr(cfg.style, k, val)
            if tpl.size:
                cfg.size = tpl.size
            if tpl.auto_zones and self.telemetry is not None:
                lo, hi = self.frame_values([0.0]).range(cfg)
                cfg.zones = [Zone(v, c) for v, c in even_zones(lo, hi)]
            cfg.validate()
        except Exception:
            self.remove_widget(cfg.id)
            raise
        return cfg

    def remove_widget(self, wid: str) -> None:
        self.project.layout.widgets = [w for w in self.project.layout.widgets if w.id != wid]

    def duplicate_widget(self, wid: str) -> WidgetConfig | None:
        src = self.widget(wid)
        if src is None:
            return None
        cfg = WidgetConfig.from_dict(src.to_dict())
        ids = {w.id for w in self.project.layout.widgets}
        n = 2
        while f"{src.id}_{n}" in ids:
            n += 1
        cfg.id = f"{src.id}_{n}"
        cfg.y = max(cfg.y - 0.05, 0.0) if cfg.y > 0.05 else cfg.y + 0.05
        self.project.layout.widgets.append(cfg)
        return cfg

    def widget_at(self, x: float, y: float, frame_w: float, frame_h: float) -> WidgetConfig | None:
        """Oberstes sichtbares Widget an Pixelposition (Export-Koordinaten)."""
        for cfg in reversed(self.project.layout.widgets):
            if not cfg.visible:
                continue
            rx, ry, rw, rh = cfg.rect(frame_w, frame_h)
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return cfg
        return None

    def set_layout(self, layout: Layout) -> None:
        self.project.layout = layout


# --------------------------------------------------------------------------- Projektpaket

BUNDLE_FORMAT = "cycleoverlay-project"
BUNDLE_VERSION = 2


def save_bundle(model: ProjectModel, path: str | PathLike) -> Path:
    """Projektpaket (.cyov) schreiben.

    Inhalt (ZIP):
      manifest.json        Formatkennung, Versionen, Inhaltsverzeichnis
      project.json         alle Einstellungen (Layout, Sync, Export, In/Out …)
      media/<Fahrtdatei>   Originaldatei der Fahrt (GPX/FIT)
      mapdata/osm.json     heruntergeladene Kartendaten (falls vorhanden)
    Videos werden – wie in Schnittprogrammen – nur verknüpft (absoluter + relativer Pfad).
    """
    from .. import __version__

    path = Path(path)
    if path.suffix.lower() != PROJECT_SUFFIX:
        path = path.with_suffix(PROJECT_SUFFIX)
    files: dict[str, str] = {}
    fd, tmp_name = tempfile.mkstemp(prefix=".cyov_", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.writestr("project.json", json.dumps(model.project.to_dict(path.parent), indent=2, ensure_ascii=False))
            if model.track_bytes is not None:
                name = "media/" + (Path(model.track_name or "track.gpx").name)
                z.writestr(name, model.track_bytes)
                files["track"] = name
            if model.map_data is not None:
                z.writestr("mapdata/osm.json", model.map_data.to_json_bytes())
                files["mapdata"] = "mapdata/osm.json"
            manifest = {
                "format": BUNDLE_FORMAT,
                "version": BUNDLE_VERSION,
                "app_version": __version__,
                "saved_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
                "files": files,
                "video": model.project.video_path,
            }
            z.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return path


def open_bundle(path: Path) -> ProjectModel:
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            if "manifest.json" not in names or "project.json" not in names:
                raise ValueError("Die Datei ist kein CycleOverlay-Projekt.")
            manifest = json.loads(z.read("manifest.json").decode("utf-8"))
            if manifest.get("format") != BUNDLE_FORMAT:
                raise ValueError("Die Datei ist kein CycleOverlay-Projekt.")
            if int(manifest.get("version", 0)) > BUNDLE_VERSION:
                raise ValueError("Das Projekt wurde mit einer neueren Version von CycleOverlay gespeichert.")
            pdata = json.loads(z.read("project.json").decode("utf-8"))
            files = manifest.get("files", {})
            track = z.read(files["track"]) if files.get("track") in names else None
            mapdata = z.read(files["mapdata"]) if files.get("mapdata") in names else None
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"Projektdatei ist beschädigt: {exc}") from exc

    m = ProjectModel(Project.from_dict(pdata, path.parent))
    p = m.project
    offset, in_s, out_s = p.offset_s, p.in_s, p.out_s
    if track is not None:
        m._load_track_data(track, Path(files["track"]).name)
    elif p.gpx_path and Path(p.gpx_path).exists():
        m.load_gpx(p.gpx_path)
    p.in_s, p.out_s = in_s, out_s
    if mapdata is not None:
        try:
            m.map_data = MapData.from_json_bytes(mapdata)
        except MapDataError as exc:
            log.warning("Kartendaten im Projekt unlesbar: %s", exc)
    if p.video_path and Path(p.video_path).exists():
        try:
            m.load_video(p.video_path)
        except Exception as exc:  # noqa: BLE001
            log.warning("Video nicht lesbar: %s", exc)
    p.offset_s = offset
    m._update_sync()
    return m
