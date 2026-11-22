"""Rechtes Panel, Tab „Sync“: Kamera-Profil, Zeitzone, Zeitbezug, Versatz.

Das eigentliche Verschieben passiert in der Sync-Zeitleiste unter der Vorschau (``sync_timeline``).
"""

from __future__ import annotations

from zoneinfo import available_timezones

from PySide6.QtGui import QValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.sync import CAMERA_PROFILES
from ..core.units import format_duration, format_offset, parse_offset
from .controller import Controller
from .form_kit import CollapsibleSection, SegmentedControl
from .signal_panel import STATUS_COLORS


class OffsetSpinBox(QDoubleSpinBox):
    """Versatz-Feld: zeigt ±H:MM:SS.mmm, nimmt Sekunden („-12,5“) oder H:MM:SS / M:SS entgegen."""

    LIMIT = 10 * 365 * 86400.0  # ±10 Jahre – auch grob falsche Kamera-Uhren lassen sich ausgleichen

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(-self.LIMIT, self.LIMIT)
        self.setDecimals(3)
        self.setKeyboardTracking(False)
        self.setToolTip("Versatz in Sekunden (z. B. -12,5) oder als H:MM:SS bzw. M:SS (z. B. -1:02:03,5).\n"
                        "Positiv = Overlay-Daten kommen später im Video.")
        self.setMinimumWidth(150)

    def textFromValue(self, value: float) -> str:  # noqa: N802 (Qt)
        return format_offset(value)

    def valueFromText(self, text: str) -> float:  # noqa: N802
        try:
            return max(-self.LIMIT, min(self.LIMIT, parse_offset(text)))
        except ValueError:
            return self.value()

    def validate(self, text: str, pos: int):  # noqa: D401
        try:
            v = parse_offset(text)
        except ValueError:
            return (QValidator.State.Intermediate, text, pos)
        if abs(v) > self.LIMIT:
            return (QValidator.State.Invalid, text, pos)
        return (QValidator.State.Acceptable, text, pos)

    def fixup(self, text: str) -> str:
        return self.textFromValue(self.value())


class SyncPanel(QWidget):
    def __init__(self, controller: Controller, parent=None):
        super().__init__(parent)
        self.c = controller
        self._updating = False

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.suggest_btn = QPushButton("Vorschlag übernehmen")
        self.suggest_btn.clicked.connect(self._apply_suggestion)

        self.camera = QComboBox()
        self.camera.addItem("Automatisch erkennen", "auto")
        for key, prof in CAMERA_PROFILES.items():
            self.camera.addItem(prof.title, key)
        self.camera.currentIndexChanged.connect(self._on_options)
        self.tz = QComboBox()
        self.tz.setEditable(True)
        self.tz.addItem("Zeitzone des Rechners", "")
        for name in sorted(available_timezones()):
            if "/" in name and not name.startswith(("Etc/", "SystemV/", "posix/", "right/")):
                self.tz.addItem(name, name)
        self.tz.currentIndexChanged.connect(self._on_options)
        self.is_end = QCheckBox("Aufnahmezeit markiert das Ende des Clips")
        self.is_end.toggled.connect(self._on_options)

        self.time_source = SegmentedControl([("auto", "Automatisch"), ("metadata", "Aufnahmezeit"),
                                             ("track_start", "Mit Track")])
        self.time_source.setToolTip(
            "Automatisch: Aufnahmezeit des Videos verwenden, wenn sie zur Fahrt passt –\n"
            "sonst (z. B. falsch gestellte Kamera-Uhr) beginnt das Video mit dem Track.\n"
            "Den genauen Abgleich machst du dann mit Versatz oder Sync-Punkt.\n"
            "Aufnahmezeit: immer die Zeit aus dem Video · Mit Track: Video beginnt immer mit dem Track.")
        self.time_source.changed.connect(self._on_options)

        self.offset = OffsetSpinBox()
        self.offset.valueChanged.connect(self._on_options)
        self.hint = QLabel(
            "<b>So synchronisierst du:</b><br>"
            "Unten in der Sync-Zeitleiste den Videoclip ziehen, bis Bild und Werte zusammenpassen "
            "(Shift = fein). Mit ←/→ verschiebst du das Video um 1 Frame, mit ↑/↓ um 1 Sekunde "
            "(Shift ×10).<br><br>"
            "<b>Hilfen:</b> Die orange Kurve ist der Fahrtwind im Ton – je lauter, desto schneller. "
            "Leg ihre Berge auf die Berge der Geschwindigkeit (Abfahrten!). Die grüne Linie zeigt die "
            "Bewegung im Bild. <i>Auto-Sync</i> schlägt bis zu fünf Stellen vor; "
            "mit <i>Sync-Punkt</i> legst du das aktuelle Videobild auf einen Moment in der Kurve.")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: gray")

        time_sec = CollapsibleSection("Kamera & Zeit")
        time_sec.form.addRow("Kamera", self.camera)
        time_sec.form.addRow("Zeitzone", self.tz)
        time_sec.form.addRow("", self.is_end)
        time_sec.form.addRow("Zeitbezug", self.time_source)
        off_sec = CollapsibleSection("Versatz")
        off_sec.form.addRow("Versatz", self.offset)
        help_sec = CollapsibleSection("So synchronisierst du", expanded=False)
        help_sec.form.addRow(self.hint)
        self.sections = {"time": time_sec, "offset": off_sec, "help": help_sec}

        lay = QVBoxLayout(self)
        lay.addWidget(self.status)
        lay.addWidget(self.suggest_btn)
        for sec in (time_sec, off_sec, help_sec):
            lay.addWidget(sec)
        lay.addStretch(1)

        c = controller
        c.projectChanged.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------------

    def refresh(self) -> None:
        m = self.c.model
        self._updating = True
        p = m.project
        self.camera.setCurrentIndex(max(self.camera.findData(p.camera), 0))
        idx = self.tz.findData(p.timezone or "")
        if idx >= 0:
            self.tz.setCurrentIndex(idx)
        else:
            self.tz.setEditText(p.timezone or "")
        self.is_end.setChecked(p.camera_time_is_end)
        self.time_source.set_value(p.time_source)
        self.offset.setValue(p.offset_s)
        if m.has_video:
            self.offset.setSingleStep(1 / float(m.fps()))
        self._updating = False

        has_video = m.video is not None
        for w in (self.camera, self.tz, self.is_end, self.time_source, self.offset):
            w.setEnabled(has_video and m.ready)
        chk = m.sync_check()
        if m.sync_error:
            self.status.setText(f"<span style='color:#d03030'><b>Sync nicht möglich:</b> {m.sync_error}</span>")
        elif chk is not None:
            st = m.sync
            meta = (f"{st.metadata_start.astimezone():%d.%m.%Y %H:%M:%S}" if st.metadata_start else "unbekannt")
            if st.anchored:
                anchor = (f"<br><span style='color:#d08a20'>Video beginnt mit dem Track "
                          f"(+ Versatz). {st.anchor_reason}</span><br>Aufnahmezeit laut Video: {meta}")
            else:
                anchor = f"<br>Videobeginn: {meta}"
            self.status.setText(
                f"<span style='color:{STATUS_COLORS[chk.status]}'><b>● {chk.message}</b></span><br>"
                f"Kamera: {CAMERA_PROFILES[m.camera_key].title}{anchor}<br>"
                f"Im Track ab {format_duration(st.window()[0])}"
            )
        elif m.ready:
            self.status.setText("Kein Video geladen. Ohne Video wählst du den Export-Bereich in der Timeline.")
        else:
            self.status.setText("Zuerst eine GPX-Datei öffnen.")
        self.suggest_btn.setVisible(chk is not None and chk.suggested_offset_s is not None)

    def _on_options(self, *_args) -> None:
        if self._updating:
            return
        tz = self.tz.currentData()
        if tz is None:  # frei eingegebener Text
            tz = self.tz.currentText().strip()
        before = self.c.model.project.offset_s
        try:
            self.c.model.set_sync_options(camera=self.camera.currentData(), timezone=tz or None,
                                          offset_s=self.offset.value(), camera_time_is_end=self.is_end.isChecked(),
                                          time_source=self.time_source.value())
        except ValueError as exc:
            self.c.error.emit("Synchronisation", str(exc))
        self.c.offset_history.record(before, self.c.model.project.offset_s)
        self.c.project_edited()

    def _apply_suggestion(self) -> None:
        if self.c.model.apply_suggestion():
            self.c.project_edited()
