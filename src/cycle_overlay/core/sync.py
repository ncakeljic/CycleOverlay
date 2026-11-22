"""Synchronisation von Video und GPX (PRD 3.3).

Modell::

    track_zeit(video_t) = (video_start_utc - track_start) + video_t + offset

- ``video_start_utc`` kommt aus den Video-Metadaten (``creation_time``),
  ausgewertet über ein Kamera-Profil (Ortszeit oder UTC, Aufnahmebeginn oder -ende).
- ``offset`` ist die manuelle Korrektur in Sekunden.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np

from .models import VideoInfo
from .processor import frame_times
from .units import format_duration


class SyncError(ValueError):
    pass


@dataclass(frozen=True)
class CameraProfile:
    key: str
    title: str
    #: True: creation_time enthält die lokale Uhrzeit der Kamera (obwohl als UTC markiert)
    creation_time_is_local: bool
    #: True: creation_time markiert das Ende der Aufnahme
    creation_time_is_end: bool = False
    #: Apple-Feld com.apple.quicktime.creationdate (mit echter Zeitzone) bevorzugen
    use_apple_creationdate: bool = False
    #: IANA-Zeitzone der Kamerauhr; None = Zeitzone des Rechners
    timezone: str | None = None

    def with_timezone(self, tz: str | None) -> "CameraProfile":
        return replace(self, timezone=tz)


#: Voreinstellungen; im Zweifel per Offset korrigieren und Profil anpassen
CAMERA_PROFILES: dict[str, CameraProfile] = {
    "gopro": CameraProfile("gopro", "GoPro", creation_time_is_local=True),
    "dji": CameraProfile("dji", "DJI (Osmo Action / Drohne)", creation_time_is_local=True),
    "insta360": CameraProfile("insta360", "Insta360", creation_time_is_local=True),
    "apple": CameraProfile("apple", "iPhone / Apple", creation_time_is_local=False, use_apple_creationdate=True),
    "generic": CameraProfile("generic", "Allgemein (creation_time ist UTC)", creation_time_is_local=False),
}


def get_profile(key: str, tz: str | None = None) -> CameraProfile:
    try:
        return CAMERA_PROFILES[key].with_timezone(tz)
    except KeyError as exc:
        raise SyncError(f"Unbekanntes Kamera-Profil: {key} (verfügbar: {', '.join(CAMERA_PROFILES)})") from exc


def localize(naive: datetime, tz_name: str | None) -> datetime:
    """Naive Ortszeit einer Zeitzone zuordnen (inkl. Sommer-/Winterzeit)."""
    if tz_name:
        try:
            return naive.replace(tzinfo=ZoneInfo(tz_name))
        except ZoneInfoNotFoundError as exc:
            raise SyncError(f"Unbekannte Zeitzone: {tz_name}") from exc
    # Zeitzonenregeln des Betriebssystems
    return naive.astimezone()


def video_start_utc(info: VideoInfo, profile: CameraProfile) -> datetime:
    """Aufnahmebeginn des Videos in UTC."""
    if profile.use_apple_creationdate and info.apple_creation_date and info.apple_creation_date.tzinfo:
        start = info.apple_creation_date.astimezone(timezone.utc)
    else:
        ct = info.creation_time
        if ct is None:
            raise SyncError(
                f"{info.path.name}: Das Video enthält keine Aufnahmezeit (creation_time). "
                "Bitte manuell synchronisieren."
            )
        if profile.creation_time_is_local:
            start = localize(ct.replace(tzinfo=None), profile.timezone).astimezone(timezone.utc)
        else:
            start = (ct if ct.tzinfo else ct.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    if profile.creation_time_is_end:
        start -= timedelta(seconds=info.duration)
    return start


@dataclass
class SyncCheck:
    status: str  # "ok" | "partial" | "outside"
    overlap_s: float
    message: str
    suggested_offset_s: float | None = None


@dataclass
class VideoSync:
    track_start: datetime
    video_start: datetime  # UTC
    duration: float
    fps: Fraction
    n_frames: int
    offset_s: float = 0.0
    #: Aufnahmezeit laut Video-Metadaten (None = keine vorhanden)
    metadata_start: datetime | None = None
    #: True = Video-Zeitstempel wird ignoriert, das Video beginnt mit dem Track (+ Offset)
    anchored: bool = False
    #: Begründung, warum am Trackbeginn ausgerichtet wurde (für die Oberfläche)
    anchor_reason: str = ""

    @classmethod
    def from_video(cls, info: VideoInfo, track_start: datetime, profile: CameraProfile, offset_s: float = 0.0,
                   time_source: str = "metadata", track_duration: float | None = None) -> "VideoSync":
        """Sync aufbauen.

        ``time_source``: ``"metadata"`` (Aufnahmezeit des Videos), ``"track_start"`` (Video beginnt mit dem
        Track) oder ``"auto"`` – Aufnahmezeit, wenn sie zum Track passt, sonst Trackbeginn.
        """
        if time_source not in TIME_SOURCES:
            raise SyncError(f"Unbekannter Zeitbezug: {time_source}")
        meta: datetime | None
        has_time = info.creation_time is not None or (profile.use_apple_creationdate and info.apple_creation_date)
        meta = None
        if time_source == "track_start":
            try:  # nur zur Anzeige – darf hier nicht scheitern
                meta = video_start_utc(info, profile) if has_time else None
            except SyncError:
                meta = None
        elif has_time or time_source == "metadata":
            meta = video_start_utc(info, profile)  # Fehler (z. B. unbekannte Zeitzone) nicht verschlucken
        anchored, reason = False, ""
        if time_source == "track_start":
            anchored, reason = True, "Zeitbezug „Video beginnt mit dem Track“ gewählt."
        elif time_source == "auto":
            if meta is None:
                anchored, reason = True, "Das Video hat keine Aufnahmezeit."
            elif track_duration is not None and not metadata_plausible(
                    (meta - track_start).total_seconds(), info.duration, track_duration):
                anchored = True
                reason = (f"Die Aufnahmezeit des Videos ({meta.astimezone():%d.%m.%Y %H:%M}) passt nicht zur "
                          f"Fahrt – vermutlich falsch eingestellte Kamera-Uhr.")
        return cls(
            track_start=track_start,
            video_start=track_start if anchored else meta,  # type: ignore[arg-type]
            duration=info.duration,
            fps=info.fps,
            n_frames=info.n_frames,
            offset_s=offset_s,
            metadata_start=meta,
            anchored=anchored,
            anchor_reason=reason,
        )

    @property
    def base_offset_s(self) -> float:
        """Abstand Video-Beginn zu Track-Beginn laut Metadaten (ohne manuelle Korrektur)."""
        return (self.video_start - self.track_start).total_seconds()

    def track_time(self, video_t):
        return self.base_offset_s + self.offset_s + np.asarray(video_t, dtype=float)

    def window(self) -> tuple[float, float]:
        start = float(self.track_time(0.0))
        return start, start + self.duration

    def frame_track_times(self) -> np.ndarray:
        """Track-Zeit für jedes Frame des Videos."""
        return frame_times(float(self.track_time(0.0)), self.n_frames, self.fps)

    def offset_for_sync_point(self, video_t: float, track_t: float, snap_to_frame: bool = True) -> float:
        """Offset, bei dem Video-Zeit ``video_t`` genau auf Track-Zeit ``track_t`` liegt."""
        offset = track_t - self.base_offset_s - video_t
        if snap_to_frame:
            frame = self.fps.denominator / self.fps.numerator
            offset = round(offset / frame) * frame
        return offset

    def check(self, track_duration: float) -> SyncCheck:
        return check_window(*self.window(), track_duration)


TIME_SOURCES = {
    "auto": "Automatisch",
    "metadata": "Aufnahmezeit des Videos",
    "track_start": "Video beginnt mit dem Track",
}


def metadata_plausible(base_offset_s: float, video_duration: float, track_duration: float) -> bool:
    """Passt die Aufnahmezeit zur Fahrt? Ja, wenn sich Video und Track überlappen – oder es mit einem
    ganzzahligen Stunden-Versatz (falsche Zeitzone) täten."""
    chk = check_window(base_offset_s, base_offset_s + video_duration, track_duration)
    return chk.status != "outside" or chk.suggested_offset_s is not None


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def check_window(start: float, end: float, track_duration: float) -> SyncCheck:
    """Prüft, ob das Video-Fenster (in Track-Zeit) im Track liegt, und schlägt ggf. einen Stunden-Versatz vor."""
    length = end - start
    overlap = _overlap(start, end, 0.0, track_duration)
    if overlap >= length - 1e-6:
        return SyncCheck("ok", overlap, "Das Video liegt vollständig innerhalb des Tracks.")

    suggestion = None
    for h in sorted(range(-14, 15), key=abs):
        if h == 0:
            continue
        s = h * 3600.0
        if start + s >= 0 and end + s <= track_duration:
            suggestion = s
            break

    if overlap > 0:
        status = "partial"
        msg = f"Das Video liegt nur teilweise im Track ({format_duration(overlap)} von {format_duration(length)})."
    else:
        status = "outside"
        if end <= 0:
            msg = f"Das Video endet {format_duration(-end)} vor Trackbeginn."
        else:
            msg = f"Das Video beginnt {format_duration(start - track_duration)} nach Trackende."
    if suggestion is not None:
        sign = "+" if suggestion > 0 else "-"
        hours = abs(int(round(suggestion / 3600)))
        msg += f" Vermutlich falsche Zeitzone: Ein Versatz von {sign}{hours} h würde passen."
    elif status == "outside":
        msg += " Passt die GPX-Datei zu diesem Video?"
    return SyncCheck(status, overlap, msg, suggestion)
