"""Timeline: Wiedergabe, Scrubbing, In-/Out-Punkte (ohne Video)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QStyle,
    QStyleOptionSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..cli import parse_clock
from ..core.units import format_duration
from .controller import Controller

STEPS_PER_SECOND = 10  # Auflösung des Schiebereglers; Feinschritte per Pfeiltaste (Frame)


def fmt_time(t: float, decimals: bool = True) -> str:
    """H:MM:SS(.z) – auch für negative Zeiten (Video vor Trackbeginn) korrekt."""
    if t != t:  # NaN
        return "--:--:--"
    if not decimals:
        return format_duration(t)
    tenths = int(round(abs(t) * 10))
    sign = "-" if t < 0 and tenths else ""
    return f"{sign}{format_duration(tenths // 10)}.{tenths % 10}"


class RangeSlider(QSlider):
    """Schieberegler, der den Export-Bereich (In/Out) farbig hinterlegt."""

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.range_marks: tuple[float, float] | None = None  # Anteile 0..1

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        if self.range_marks is None:
            return
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        groove = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, opt,
                                             QStyle.SubControl.SC_SliderGroove, self)
        a, b = self.range_marks
        x0 = groove.x() + groove.width() * a
        x1 = groove.x() + groove.width() * b
        p = QPainter(self)
        p.fillRect(QRectF(x0, groove.center().y() - 3, max(x1 - x0, 2), 6), QColor(255, 106, 31, 170))
        p.end()


class TimelineBar(QWidget):
    def __init__(self, controller: Controller, parent=None):
        super().__init__(parent)
        self.c = controller
        self._updating = False

        self.play_btn = QToolButton()
        self.play_btn.setText("▶")
        self.play_btn.setToolTip("Wiedergabe (Leertaste)")
        self.play_btn.clicked.connect(self.c.toggle_play)
        self.back_btn = QToolButton()
        self.back_btn.setText("◀|")
        self.back_btn.setToolTip("1 Frame zurück")
        self.back_btn.clicked.connect(lambda: self.c.step_frames(-1))
        self.fwd_btn = QToolButton()
        self.fwd_btn.setText("|▶")
        self.fwd_btn.setToolTip("1 Frame vor")
        self.fwd_btn.clicked.connect(lambda: self.c.step_frames(1))

        self.slider = RangeSlider()
        self.slider.valueChanged.connect(self._on_slider)
        self.time_label = QLabel("–")
        self.time_label.setMinimumWidth(330)

        top = QHBoxLayout()
        top.addWidget(self.back_btn)
        top.addWidget(self.play_btn)
        top.addWidget(self.fwd_btn)
        top.addWidget(self.slider, 1)
        top.addWidget(self.time_label)

        # In/Out (nur ohne Video)
        self.in_edit = QLineEdit()
        self.out_edit = QLineEdit()
        for ed in (self.in_edit, self.out_edit):
            ed.setPlaceholderText("H:MM:SS")
            # breit genug für „10:41:04.000“ in der Systemschrift (macOS-Schrift ist breiter)
            ed.setMinimumWidth(ed.fontMetrics().horizontalAdvance("00:00:00.000") + 24)
            ed.setMaximumWidth(ed.fontMetrics().horizontalAdvance("00:00:00.000") + 40)
            ed.editingFinished.connect(self._on_in_out_edited)
        self.set_in_btn = QPushButton("In = Position")
        self.set_in_btn.clicked.connect(lambda: self._set_mark("in"))
        self.set_out_btn = QPushButton("Out = Position")
        self.set_out_btn.clicked.connect(lambda: self._set_mark("out"))
        self.range_label = QLabel("")
        self.inout_row = QWidget()
        row = QHBoxLayout(self.inout_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel("Export-Bereich:  In"))
        row.addWidget(self.in_edit)
        row.addWidget(self.set_in_btn)
        row.addSpacing(12)
        row.addWidget(QLabel("Out"))
        row.addWidget(self.out_edit)
        row.addWidget(self.set_out_btn)
        row.addSpacing(12)
        row.addWidget(self.range_label, 1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 6)
        lay.addLayout(top)
        lay.addWidget(self.inout_row)

        c = controller
        c.projectChanged.connect(self.refresh)
        c.positionChanged.connect(self._on_position)
        c.playingChanged.connect(lambda on: self.play_btn.setText("⏸" if on else "▶"))
        self.refresh()

    # ------------------------------------------------------------------

    def set_compact(self, on: bool) -> None:
        """Im Sync-Modus übernimmt die Sync-Zeitleiste das Scrubben – nur Knöpfe und Zeit zeigen."""
        self.slider.setVisible(not on)

    def refresh(self) -> None:
        m = self.c.model
        ready = m.ready
        for w in (self.play_btn, self.back_btn, self.fwd_btn, self.slider):
            w.setEnabled(ready)
        dur = m.timeline_duration() if ready else 0.0
        self._updating = True
        self.slider.setRange(0, max(int(round(dur * STEPS_PER_SECOND)), 1))
        self._updating = False
        manual = ready and not m.has_video
        self.inout_row.setVisible(manual)
        if manual:
            a, b = m.export_range()
            self.in_edit.setText(format_duration(a))
            self.out_edit.setText(format_duration(b))
            self.range_label.setText(f"Clip {format_duration(b - a)} · {m.n_frames()} Frames @ {float(m.fps()):.3f} FPS")
            self.slider.range_marks = (a / dur, b / dur) if dur > 0 else None
        else:
            self.slider.range_marks = None
        self.slider.update()
        self._on_position(self.c.position)

    def _on_position(self, t: float) -> None:
        m = self.c.model
        if not m.ready:
            self.time_label.setText("Keine GPX-Datei geladen")
            return
        self._updating = True
        self.slider.setValue(int(round(t * STEPS_PER_SECOND)))
        self._updating = False
        track_t = m.track_time(t)
        tod = ""
        if m.telemetry is not None and 0 <= track_t <= m.telemetry.duration:
            dt = m.telemetry.absolute_time(track_t)
            from zoneinfo import ZoneInfo

            local = dt.astimezone(ZoneInfo(m.project.timezone)) if m.project.timezone else dt.astimezone()
            tod = f"{local:%H:%M:%S}"
        if m.has_video:
            self.time_label.setText(f"Video {fmt_time(t)} / {fmt_time(m.timeline_duration(), False)}   "
                                    f"Track {fmt_time(track_t)}   {tod}")
        else:
            self.time_label.setText(f"Track {fmt_time(t)} / {fmt_time(m.timeline_duration(), False)}   {tod}")

    def _on_slider(self, v: int) -> None:
        if not self._updating:
            self.c.set_position(v / STEPS_PER_SECOND)

    def _set_mark(self, which: str) -> None:
        t = self.c.position
        if which == "in":
            self.c.model.set_in_out(in_s=t)
        else:
            self.c.model.set_in_out(out_s=t)
        self.c.project_edited()

    def _on_in_out_edited(self) -> None:
        try:
            a = parse_clock(self.in_edit.text())
            b = parse_clock(self.out_edit.text())
        except Exception:  # noqa: BLE001
            self.refresh()
            return
        cur = self.c.model.export_range()
        if (a, b) != cur:
            self.c.model.set_in_out(a, b)
            self.c.project_edited()
