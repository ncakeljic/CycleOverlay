"""Rechtes Panel, Tab „Filter“: Glättung je Datenkanal (Aus / niedrig / mittel / stark), Pausen,
Zahlenanzeige (Durchzählen / Springen)."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ..core.processor import FILTER_DEFAULTS, FILTER_LEVEL_TITLES, FILTER_LEVELS, filter_value
from .controller import Controller
from .form_kit import CollapsibleSection, SegmentedControl

#: Reihenfolge und Beschriftung im Panel
FILTER_CHANNELS = [
    ("speed", "Geschwindigkeit", "s"),
    ("power", "Leistung", "s"),
    ("heart_rate", "Herzfrequenz", "s"),
    ("cadence", "Trittfrequenz", "s"),
    ("elevation", "Höhe", "s"),
    ("gradient", "Steigung", "m"),
    ("temperature", "Temperatur", "s"),
]


def level_tooltip(channel: str, unit: str) -> str:
    parts = []
    for lv in FILTER_LEVELS:
        v = filter_value(channel, lv)
        if channel == "gradient":
            txt = f"über {v:g} m bestimmt"
        else:
            txt = "ungefiltert" if v <= 1 else f"Mittel über {v:g} s"
        default = " (Standard)" if FILTER_DEFAULTS[channel] == lv else ""
        parts.append(f"{FILTER_LEVEL_TITLES[lv]}: {txt}{default}")
    return "\n".join(parts)


class FilterPanel(QScrollArea):
    def __init__(self, controller: Controller, parent=None):
        super().__init__(parent)
        self.c = controller
        self.setWidgetResizable(True)
        self._updating = False
        self.controls: dict[str, SegmentedControl] = {}

        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(6, 6, 6, 6)
        intro = QLabel("Wie stark die Messwerte geglättet werden. Stärker = ruhigere Anzeige, aber Änderungen "
                       "kommen etwas verzögert bzw. verwischt an. „Aus“ zeigt die Rohwerte (Sekundenraster).")
        intro.setWordWrap(True)
        intro.setStyleSheet("color: gray")
        lay.addWidget(intro)

        sec = CollapsibleSection("Glättung je Signal")
        self.labels: dict[str, QLabel] = {}
        for ch, title, unit in FILTER_CHANNELS:
            seg = SegmentedControl([(lv, FILTER_LEVEL_TITLES[lv]) for lv in FILTER_LEVELS])
            seg.setToolTip(level_tooltip(ch, unit))
            seg.changed.connect(lambda lv, c=ch: self._set(c, lv))
            self.controls[ch] = seg
            lab = QLabel(title)
            lab.setToolTip(level_tooltip(ch, unit))
            self.labels[ch] = lab
            sec.form.addRow(lab, seg)
        lay.addWidget(sec)

        pause = CollapsibleSection("Pausen")
        self.pause_mode = SegmentedControl([("zero", "auf 0 setzen"), ("hold", "letzten Wert halten")])
        self.pause_mode.setToolTip("Was Geschwindigkeit, Leistung und Trittfrequenz zeigen, während die "
                                   "Aufzeichnung pausiert (Auto-Pause des Radcomputers).")
        self.pause_mode.changed.connect(self._set_pause)
        pause.form.addRow("Werte in Pausen", self.pause_mode)
        lay.addWidget(pause)

        numbers = CollapsibleSection("Zahlenanzeige")
        self.count_numbers = SegmentedControl([(True, "Durchzählen"), (False, "Springen")])
        self.count_numbers.setToolTip(
            "Durchzählen: Zwischen zwei Aktualisierungen läuft die Zahl durch alle Zwischenwerte –\n"
            "wirst du in 1 s um 10 km/h schneller, erscheint jede Zahl dazwischen.\n"
            "Springen: Die Zahl wechselt nur im eingestellten Takt (Widget → „Zahl aktualisieren“).")
        self.count_numbers.changed.connect(self._set_count)
        numbers.form.addRow("Übergang", self.count_numbers)
        hint = QLabel("Wie oft eine Zahl wechselt, stellst du je Widget unter „Zahl aktualisieren“ ein.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray")
        numbers.form.addRow(hint)
        lay.addWidget(numbers)

        self.reset_btn = QPushButton("Alle auf Standard")
        self.reset_btn.clicked.connect(self.reset)
        lay.addWidget(self.reset_btn)
        lay.addStretch(1)
        self.setWidget(root)

        controller.projectChanged.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        m = self.c.model
        self._updating = True
        tel = m.telemetry
        for ch, seg in self.controls.items():
            seg.set_value(m.filter_level(ch))
            avail = tel is None or bool(tel.available.get(ch, False))
            seg.setEnabled(tel is not None and avail)
            self.labels[ch].setEnabled(tel is not None and avail)
        self.pause_mode.set_value(m.project.pause_mode)
        self.pause_mode.setEnabled(tel is not None)
        self.count_numbers.set_value(bool(m.project.count_numbers))
        self.reset_btn.setEnabled(tel is not None)
        self._updating = False

    def _set(self, channel: str, level: str) -> None:
        if self._updating or self.c.model.filter_level(channel) == level:
            return
        try:
            self.c.model.set_filter(channel, level)
        except ValueError as exc:
            self.c.error.emit("Filter", str(exc))
            return
        self.c.data_reprocessed()

    def _set_pause(self, mode: str) -> None:
        if self._updating or self.c.model.project.pause_mode == mode:
            return
        self.c.model.set_pause_mode(mode)
        self.c.data_reprocessed()

    def _set_count(self, on: bool) -> None:
        if self._updating or self.c.model.project.count_numbers == on:
            return
        self.c.model.project.count_numbers = bool(on)
        self.c.project_edited()
        self.c.layoutChanged.emit(None)

    def reset(self) -> None:
        m = self.c.model
        m.project.filters.clear()
        m.project.pause_mode = "zero"
        m.project.count_numbers = True
        m.reprocess()
        self.c.data_reprocessed()
