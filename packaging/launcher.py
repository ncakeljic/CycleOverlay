"""Startpunkt der gebündelten App (PyInstaller).

Im Fenster-Modus gibt es keine Konsole: ``sys.stdout``/``sys.stderr`` sind dann ``None`` – jede
Ausgabe würde abstürzen. Deshalb werden sie auf „nirgendwo“ umgeleitet.
"""

import os
import sys

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115

from cycle_overlay.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
