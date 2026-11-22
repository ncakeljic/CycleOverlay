"""Versionsnummer und Änderungsprotokoll passen zusammen."""

import re
from pathlib import Path

from cycle_overlay import __version__


def test_version_format():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)


def test_changelog_lists_current_version_first():
    text = (Path(__file__).parents[1] / "CHANGELOG.md").read_text(encoding="utf-8")
    first = re.search(r"^## (\d+\.\d+\.\d+)", text, re.M)
    assert first and first.group(1) == __version__


def test_release_notes_for_current_version():
    import importlib.util
    from pathlib import Path

    from cycle_overlay import __version__

    path = Path(__file__).resolve().parents[1] / "packaging" / "release_notes.py"
    spec = importlib.util.spec_from_file_location("release_notes", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.source_version() == __version__
    assert mod.main(["--check", f"v{__version__}"]) == 0
    assert mod.main(["--check", "v0.0.1"]) == 1
    notes = mod.section(__version__)
    assert notes.strip() and not any(line.startswith("## ") for line in notes.splitlines())
    assert mod.section("v1.0.0", "## 1.0.0\n- a\n## 0.9.0\n- b\n") == "- a\n"
