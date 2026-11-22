"""Layout-Presets: mitgelieferte (resources/presets) und eigene (Benutzerordner)."""

from __future__ import annotations

from os import PathLike
from pathlib import Path

from ..app_paths import resource_path, user_presets_dir
from ..rendering.layout import Layout

DEFAULT_PRESET = "rennrad_minimal"


def builtin_presets_dir() -> Path:
    return resource_path("presets")


def list_presets() -> dict[str, Path]:
    """Name -> Pfad. Eigene Presets überschreiben gleichnamige mitgelieferte."""
    found: dict[str, Path] = {}
    for d in (builtin_presets_dir(), user_presets_dir()):
        if d.is_dir():
            for f in sorted(d.glob("*.json")):
                found[f.stem] = f
    return found


def load_preset(name_or_path: str | PathLike | None = None) -> Layout:
    """Preset über Namen (z. B. 'rennrad_minimal') oder Dateipfad laden."""
    if name_or_path is None:
        name_or_path = DEFAULT_PRESET
    p = Path(name_or_path)
    if p.suffix.lower() == ".json" or p.exists():
        if not p.is_file():
            raise ValueError(f"Preset-Datei nicht gefunden: {p}")
        return Layout.load(p)
    presets = list_presets()
    if str(name_or_path) not in presets:
        raise ValueError(f"Unbekanntes Preset '{name_or_path}'. Verfügbar: {', '.join(presets) or '-'}")
    return Layout.load(presets[str(name_or_path)])


def save_user_preset(layout: Layout, name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_") or "preset"
    return layout.save(user_presets_dir() / f"{safe}.json")
