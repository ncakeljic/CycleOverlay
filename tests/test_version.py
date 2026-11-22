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
