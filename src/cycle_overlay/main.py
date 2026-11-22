"""App-Startpunkt.

``cycle-overlay`` ohne Argumente (oder nur mit Dateien) startet die grafische Oberfläche,
mit einem Befehl (``info``, ``render`` …) die Kommandozeile.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .cli import build_parser
from .cli import main as cli_main


def _commands() -> set[str]:
    parser = build_parser()
    for action in parser._subparsers._group_actions:  # type: ignore[union-attr]
        return set(action.choices)
    return set()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == "gui":
        args = args[1:]
    elif args and (args[0] in _commands() or args[0].startswith("-")):
        return cli_main(args)
    if any(not Path(a).exists() for a in args):
        return cli_main(args)  # zeigt Fehlermeldung/Hilfe
    from .gui.app import run_gui

    return run_gui(args)


if __name__ == "__main__":
    sys.exit(main())
