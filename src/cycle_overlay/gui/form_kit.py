"""Bausteine für die Einstellungs-Panels (Qt).

- :class:`CollapsibleSection` – aufklappbarer Abschnitt mit Kurzinfo im Kopf
- :class:`SegmentedControl` – wenige Möglichkeiten nebeneinander, ein Klick wählt
- :class:`AnchorGrid` – 3×3-Raster für den Ankerpunkt
- :class:`SliderSpin` – Schieberegler plus Zahlenfeld
- :class:`ColorButton` – Farbfeld mit Farbwähler
- :func:`make_form` – Formular, das lange Beschriftungen über das Feld setzt
"""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..rendering.layout import ANCHORS, parse_color


def make_form() -> QFormLayout:
    form = QFormLayout()
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    form.setContentsMargins(4, 2, 4, 6)
    form.setVerticalSpacing(6)
    return form


class CollapsibleSection(QWidget):
    """Abschnitt mit Kopfzeile zum Auf-/Zuklappen; zugeklappt zeigt der Kopf eine Kurzinfo."""

    toggled = Signal(bool)  # True = offen

    def __init__(self, title: str, expanded: bool = True, summary: str = "", parent=None):
        super().__init__(parent)
        self.title = title
        self.header = QToolButton()
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.header.setStyleSheet("QToolButton { border: none; font-weight: 600; text-align: left; padding: 4px 2px; }")
        self.header.toggled.connect(self._on_toggle)
        self.summary = QLabel(summary)
        self.summary.setStyleSheet("color: gray")
        self.summary.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(self.header, 1)
        head.addWidget(self.summary)
        self.body = QWidget()
        self.form = make_form()
        self.body.setLayout(self.form)
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet("background: rgba(128,128,128,60)")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(line)
        lay.addLayout(head)
        lay.addWidget(self.body)
        self._apply(expanded)

    @property
    def expanded(self) -> bool:
        return self.header.isChecked()

    def set_expanded(self, on: bool) -> None:
        self.header.setChecked(on)

    def _apply(self, on: bool) -> None:
        self.header.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)
        self.header.setText(escape_mnemonic(self.title))
        self.body.setVisible(on)
        self.summary.setVisible(not on and bool(self.summary.text()))

    def _on_toggle(self, on: bool) -> None:
        self._apply(on)
        self.toggled.emit(on)


class ChoiceCombo(QComboBox):
    """Auswahlliste mit derselben Schnittstelle wie ``SegmentedControl`` (für mehr als 4 Möglichkeiten)."""

    changed = Signal(object)

    def __init__(self, options: list[tuple[Any, str]], value: Any = None, parent=None):
        super().__init__(parent)
        self._values = [v for v, _ in options]
        for v, text in options:
            self.addItem(text, v)
        self.set_value(value)
        self.currentIndexChanged.connect(self._on_index)
        self._quiet = False

    def value(self) -> Any:
        i = self.currentIndex()
        return self._values[i] if 0 <= i < len(self._values) else None

    def set_value(self, value: Any) -> None:
        if value in self._values:
            self._quiet = True
            try:
                self.setCurrentIndex(self._values.index(value))
            finally:
                self._quiet = False

    def select(self, value: Any) -> None:
        """Wie eine Auswahl durch den Nutzer (für Tests)."""
        if value in self._values:
            if self.currentIndex() == self._values.index(value):
                self.changed.emit(value)
            else:
                self.setCurrentIndex(self._values.index(value))

    def _on_index(self, i: int) -> None:
        if not getattr(self, "_quiet", False) and 0 <= i < len(self._values):
            self.changed.emit(self._values[i])


def choice_control(options: list[tuple[Any, str]], value: Any = None, parent=None) -> "SegmentedControl | ChoiceCombo":
    """Bis 4 Möglichkeiten als Knopfreihe, sonst als Auswahlliste."""
    return SegmentedControl(options, value, parent) if len(options) <= 4 else ChoiceCombo(options, value, parent)


class SegmentedControl(QWidget):
    """Wenige Möglichkeiten als Knopfreihe (bei langen Texten zweizeilig)."""

    changed = Signal(object)

    def __init__(self, options: list[tuple[Any, str]], value: Any = None, parent=None):
        super().__init__(parent)
        self._values = [v for v, _ in options]
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        longest = max((len(t) for _, t in options), default=0)
        cols = len(options) if longest * len(options) <= 34 else max(1, math.ceil(len(options) / 2))
        self.buttons: list[QToolButton] = []
        for i, (_v, text) in enumerate(options):
            b = QToolButton()
            b.setText(escape_mnemonic(text))
            b.setCheckable(True)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.setMinimumHeight(24)
            self.group.addButton(b, i)
            grid.addWidget(b, i // cols, i % cols)
            self.buttons.append(b)
        self.set_value(value)
        self.group.idClicked.connect(self._on_click)

    def value(self) -> Any:
        i = self.group.checkedId()
        return self._values[i] if 0 <= i < len(self._values) else None

    def set_value(self, value: Any) -> None:
        for i, v in enumerate(self._values):
            if v == value:
                self.buttons[i].setChecked(True)
                return

    def select(self, value: Any) -> None:
        """Wie ein Klick (für Tests und Tastatur)."""
        for i, v in enumerate(self._values):
            if v == value:
                self.buttons[i].setChecked(True)
                self._on_click(i)
                return

    def _on_click(self, i: int) -> None:
        self.changed.emit(self._values[i])


ANCHOR_ARROWS = {"top_left": "↖", "top_center": "↑", "top_right": "↗", "center_left": "←", "center": "•",
                 "center_right": "→", "bottom_left": "↙", "bottom_center": "↓", "bottom_right": "↘"}


class AnchorGrid(QWidget):
    """3×3-Raster: an welcher Ecke/Kante sich das Widget ausrichtet."""

    changed = Signal(str)

    def __init__(self, value: str, titles: dict[str, str] | None = None, parent=None):
        super().__init__(parent)
        self.group = QButtonGroup(self)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        self.buttons: dict[str, QToolButton] = {}
        for i, key in enumerate(ANCHORS):
            b = QToolButton()
            b.setText(ANCHOR_ARROWS.get(key, "·"))
            b.setCheckable(True)
            b.setFixedSize(28, 24)
            if titles:
                b.setToolTip(titles.get(key, key))
            self.group.addButton(b, i)
            grid.addWidget(b, i // 3, i % 3)
            self.buttons[key] = b
        grid.setColumnStretch(3, 1)
        if value in self.buttons:
            self.buttons[value].setChecked(True)
        self.group.idClicked.connect(lambda i: self.changed.emit(list(ANCHORS)[i]))

    def value(self) -> str:
        for k, b in self.buttons.items():
            if b.isChecked():
                return k
        return ""

    def select(self, key: str) -> None:
        if key in self.buttons:
            self.buttons[key].setChecked(True)
            self.changed.emit(key)


class SliderSpin(QWidget):
    """Schieberegler und Zahlenfeld, die sich gegenseitig nachführen."""

    valueChanged = Signal(float)
    STEPS = 1000

    def __init__(self, lo: float, hi: float, value: float, step: float = 1.0, decimals: int = 1,
                 suffix: str = "", prefix: str = "", parent=None):
        super().__init__(parent)
        self.lo, self.hi = float(lo), float(hi)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, self.STEPS)
        self.spin = QDoubleSpinBox()
        self.spin.setRange(lo, hi)
        self.spin.setDecimals(decimals)
        self.spin.setSingleStep(step)
        self.spin.setSuffix(suffix)
        self.spin.setPrefix(prefix)
        self.spin.setKeyboardTracking(False)
        self.spin.setMinimumWidth(84)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.spin)
        self._sync = False
        self.setValue(value)
        self.slider.valueChanged.connect(self._from_slider)
        self.slider.sliderReleased.connect(lambda: self.valueChanged.emit(self.spin.value()))
        self.spin.valueChanged.connect(self._from_spin)

    def value(self) -> float:
        return self.spin.value()

    def setValue(self, v: float) -> None:  # noqa: N802 (Qt-Stil)
        self._sync = True
        self.spin.setValue(v)
        span = max(self.hi - self.lo, 1e-9)
        self.slider.setValue(int(round((min(max(v, self.lo), self.hi) - self.lo) / span * self.STEPS)))
        self._sync = False

    def _from_slider(self, pos: int) -> None:
        if self._sync:
            return
        v = self.lo + pos / self.STEPS * (self.hi - self.lo)
        step = self.spin.singleStep()
        if step > 0:
            v = round(v / step) * step
        self._sync = True
        self.spin.setValue(v)
        self._sync = False
        if not self.slider.isSliderDown():  # Klick/Tastatur: sofort; Ziehen: beim Loslassen
            self.valueChanged.emit(self.spin.value())

    def _from_spin(self, v: float) -> None:
        if self._sync:
            return
        self._sync = True
        span = max(self.hi - self.lo, 1e-9)
        self.slider.setValue(int(round((v - self.lo) / span * self.STEPS)))
        self._sync = False
        self.valueChanged.emit(v)


class ColorButton(QPushButton):
    def __init__(self, value: str, on_change, parent=None):
        super().__init__(parent)
        self._value = value
        self._on_change = on_change
        self.setFixedWidth(96)
        self.clicked.connect(self._choose)
        self._paint()

    def value(self) -> str:
        return self._value

    def set_value(self, value: str, notify: bool = True) -> None:
        self._value = value
        self._paint()
        if notify:
            self._on_change(value)

    def _paint(self) -> None:
        r, g, b, a = parse_color(self._value)
        fg = "#000" if (r * 0.3 + g * 0.59 + b * 0.11) > 140 or a < 100 else "#fff"
        self.setText(self._value)
        self.setStyleSheet(f"background-color: rgba({r},{g},{b},{a}); color: {fg}; border: 1px solid #888; "
                           f"padding: 2px;")

    def _choose(self) -> None:
        r, g, b, a = parse_color(self._value)
        col = QColorDialog.getColor(QColor(r, g, b, a), self, "Farbe wählen",
                                    QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if col.isValid():
            self.set_value(f"#{col.red():02X}{col.green():02X}{col.blue():02X}{col.alpha():02X}")


class ElidedLabel(QLabel):
    """Einzeiliger Text, der bei Platzmangel mit „…“ gekürzt wird statt andere Elemente zu verdrängen.

    Der vollständige Text steht im Tooltip.
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt)
        self._full = text or ""
        self.setToolTip(self._full)
        self._elide()

    def full_text(self) -> str:
        return self._full

    def _elide(self) -> None:
        width = max(self.width(), 10)
        super().setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, width))

    def resizeEvent(self, e) -> None:
        self._elide()
        super().resizeEvent(e)


def escape_mnemonic(text: str) -> str:
    """„&“ in Knopf-Texten wörtlich anzeigen (Qt macht daraus sonst ein Tastenkürzel)."""
    return text.replace("&", "&&")


def hint_label(text: str, color: str = "#d08a00") -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet(f"color:{color}")
    return lab
