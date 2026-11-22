"""App-Einstellungen als JSON im Benutzer-Konfigurationsordner."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from ..app_paths import user_config_dir

log = logging.getLogger(__name__)

SETTINGS_FILE_NAME = "settings.json"


def settings_file() -> Path:
    return user_config_dir() / SETTINGS_FILE_NAME


@dataclass
class AppSettings:
    ffmpeg_path: str | None = None
    ffprobe_path: str | None = None
    units: str = "metric"  # "metric" | "imperial"
    #: IANA-Zeitzone für Kamerazeiten, None = Zeitzone des Rechners
    default_timezone: str | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> "AppSettings":
        path = path or settings_file()
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Einstellungen konnten nicht gelesen werden (%s), verwende Standardwerte", exc)
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path | None = None) -> Path:
        path = path or settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return path
