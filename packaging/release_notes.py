"""Abschnitt einer Version aus CHANGELOG.md ausgeben (für den Release-Text auf GitHub).

    python packaging/release_notes.py 1.8.0 > notes.md
    python packaging/release_notes.py --check v1.8.0   # Tag passt zu __version__?
    python packaging/release_notes.py --version         # Version aus dem Quellcode ausgeben
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source_version() -> str:
    text = (ROOT / "src" / "cycle_overlay" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not m:
        raise SystemExit("Version in __init__.py nicht gefunden")
    return m.group(1)


def section(version: str, changelog: str | None = None) -> str:
    text = changelog if changelog is not None else (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    version = version.lstrip("v")
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line[3:].strip().split()[0] == version
            continue
        if inside:
            out.append(line)
    if not inside and not out:
        raise SystemExit(f"Version {version} steht nicht in CHANGELOG.md")
    return "\n".join(out).strip() + "\n"


def main(argv: list[str]) -> int:
    if argv == ["--version"]:
        print(source_version())
        return 0
    if len(argv) == 2 and argv[0] == "--check":
        tag, ver = argv[1].lstrip("v"), source_version()
        if tag != ver:
            print(f"Tag v{tag} passt nicht zur Version {ver} im Quellcode.", file=sys.stderr)
            return 1
        print(f"Version {ver} ok")
        return 0
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    sys.stdout.write(section(argv[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
