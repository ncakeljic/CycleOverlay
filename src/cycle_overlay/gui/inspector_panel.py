"""Rechtes Panel, Tab „Widget“: Eigenschaften des ausgewählten Widgets."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFontComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.units import CHANNELS
from ..rendering.grade import climbs_for
from ..rendering.layout import (
    GRADE_SCHEMES,
    MAP_CHANNEL,
    POSITION_TYPES,
    WidgetConfig,
    Zone,
    grade_scheme_of,
    parse_color,
)
from ..rendering.values import CLOCK_CHANNEL
from .controller import Controller
from .form_kit import AnchorGrid, CollapsibleSection, ColorButton, SegmentedControl, SliderSpin, hint_label
from .inspector_spec import ANCHOR_TITLES, SEGMENT_MAX, Field, visible_sections
from .signal_panel import TYPE_TITLES, channel_title


def zones_to_text(zones: list[Zone]) -> str:
    return ", ".join(f"{z.from_value:g}:{z.color}" for z in zones)


def text_to_zones(text: str) -> list[Zone]:
    zones = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        val, color = part.split(":", 1)
        color = color.strip()
        parse_color(color)
        zones.append(Zone(float(val.strip().replace(",", ".")), color))
    return zones


class InspectorPanel(QScrollArea):
    """Eigenschaften des ausgewählten Widgets – in aufklappbaren Abschnitten (siehe ``inspector_spec``)."""

    def __init__(self, controller: Controller, parent=None):
        super().__init__(parent)
        self.c = controller
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._cfg: WidgetConfig | None = None
        self._pos_fields: dict[str, QDoubleSpinBox] = {}
        self._building = False
        #: aufgeklappte Abschnitte je (Widget-Typ, Abschnitt) – bleibt über Neuaufbauten erhalten
        self._expanded: dict[tuple[str, str], bool] = {}
        self.sections: dict[str, CollapsibleSection] = {}
        c = controller
        c.selectionChanged.connect(lambda _w: self.rebuild())
        c.layoutChanged.connect(self._on_layout_changed)
        c.projectChanged.connect(self.rebuild)
        self.rebuild()

    # ------------------------------------------------------------------ Aufbau

    def _on_layout_changed(self, sender) -> None:
        if sender is self:
            return
        if sender is not None and sender.__class__.__name__ == "SignalPanel":
            self.schedule_rebuild()  # z. B. Sichtbarkeit in der Widget-Liste umgeschaltet
            return
        cfg = self.c.model.widget(self.c.selected) if self.c.selected else None
        if cfg is not self._cfg:
            self.rebuild()
            return
        # nur Positionsfelder nachziehen (z. B. nach Drag-and-Drop), ohne Neuaufbau
        if cfg is not None:
            self._building = True
            for key, box in self._pos_fields.items():
                box.setValue(getattr(cfg, key) * 100)
            self._building = False

    def rebuild(self) -> None:
        scroll = self.verticalScrollBar().value()
        same = self._cfg is not None and self.c.selected and self._cfg is self.c.model.widget(self.c.selected)
        self._building = True
        self._cfg = cfg = self.c.model.widget(self.c.selected) if self.c.selected else None
        self._pos_fields = {}
        self.sections = {}
        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(2)
        if cfg is None:
            hint = QLabel("Kein Widget ausgewählt.\n\nKlicke ein Widget in der Vorschau an "
                          "oder wähle es links in der Liste.\nMit „+ Widget“ fügst du neue hinzu.")
            hint.setWordWrap(True)
            lay.addWidget(hint)
            lay.addStretch(1)
            self.setWidget(root)
            self._building = False
            return

        lay.addLayout(self._header(cfg))
        for sec, fields in visible_sections(cfg, self):
            key = (cfg.type, sec.key)
            box = CollapsibleSection(sec.title, self._expanded.get(key, not sec.collapsed),
                                     sec.summary(cfg) if sec.summary else "")
            box.toggled.connect(lambda on, k=key: self._expanded.__setitem__(k, on))
            for f in fields:
                self._add_field(box.form, cfg, f)
            self.sections[sec.key] = box
            lay.addWidget(box)
        lay.addStretch(1)
        self.setWidget(root)
        self._building = False
        if same:  # nach einem Neuaufbau an derselben Stelle weiterarbeiten
            QTimer.singleShot(0, lambda v=scroll: self.verticalScrollBar().setValue(v))

    def _header(self, cfg: WidgetConfig) -> QHBoxLayout:
        title = QLabel(f"<b>{channel_title(cfg.channel)}</b> · {TYPE_TITLES.get(cfg.type, cfg.type)}")
        title.setWordWrap(True)
        dup = QToolButton()
        dup.setText("⧉")
        dup.setToolTip("Duplizieren (Strg/Cmd+D)")
        dup.clicked.connect(self.c.duplicate_selected)
        rem = QToolButton()
        rem.setText("🗑")
        rem.setToolTip("Löschen")
        rem.clicked.connect(self.c.remove_selected)
        row = QHBoxLayout()
        row.addWidget(title, 1)
        for b in (dup, rem):
            row.addWidget(b)
        return row

    # ------------------------------------------------------------------ Felder

    def _target(self, cfg: WidgetConfig, target: str):
        return {"cfg": cfg, "style": cfg.style, "minimap": cfg.minimap, "climb": cfg.climb}[target]

    def _setter(self, f: Field):
        if f.target == "minimap":
            return lambda v: self._set_minimap(f.key, v, rebuild=f.rebuild)
        if f.target == "climb":
            return lambda v: self._set_climb(f.key, v, rebuild=f.rebuild)
        if f.target == "style":
            return lambda v: self._set_style(f.key, v)
        return lambda v: self._set(f.key, v, rebuild=f.rebuild)

    def _add_field(self, form: QFormLayout, cfg: WidgetConfig, f: Field) -> None:
        if f.kind == "custom":
            self._add_custom(form, cfg, f)
            return
        obj = self._target(cfg, f.target)
        value = getattr(obj, f.key)
        set_value = self._setter(f)
        w: QWidget
        if f.kind == "choice":
            options = list(f.options)
            if value not in [v for v, _ in options]:
                options.append((value, str(value)))
            if len(options) <= SEGMENT_MAX:
                seg = SegmentedControl(options, value)
                seg.changed.connect(set_value)
                w = seg
            else:
                box = QComboBox()
                for v, t in options:
                    box.addItem(t, v)
                box.setCurrentIndex(max([v for v, _ in options].index(value), 0))
                box.currentIndexChanged.connect(lambda i, b=box: set_value(b.itemData(i)))
                w = box
        elif f.kind == "bool":
            cb = QCheckBox(f.tooltip or f.label)
            cb.setChecked(bool(value))
            cb.toggled.connect(set_value)
            form.addRow(f.label if (f.label and f.tooltip) else "", cb)
            return
        elif f.kind == "number":
            shown = (value if value is not None else f.lo) / f.scale
            if f.slider:
                ss = SliderSpin(f.lo, f.hi, shown, f.step, f.decimals, f.suffix, f.prefix)
                ss.valueChanged.connect(lambda v: set_value(v * f.scale))
                spin = ss.spin
                w = ss
            else:
                spin = QDoubleSpinBox()
                spin.setRange(f.lo, f.hi)
                spin.setDecimals(f.decimals)
                spin.setSingleStep(f.step)
                spin.setSuffix(f.suffix)
                spin.setPrefix(f.prefix)
                if f.special:
                    spin.setSpecialValueText(f.special)
                spin.setValue(shown)
                spin.setKeyboardTracking(False)
                spin.valueChanged.connect(lambda v: set_value(v * f.scale))
                w = spin
            if f.target == "cfg" and f.key in ("x", "y", "size"):
                self._pos_fields[f.key] = spin
        elif f.kind == "color":
            w = ColorButton(value, set_value)
        elif f.kind == "optional_color":
            default = f.default(cfg) if f.default else "#FFFFFFFF"
            own = QCheckBox("eigene")
            own.setChecked(value is not None)
            btn = ColorButton(value or default, set_value)
            btn.setEnabled(own.isChecked())
            own.toggled.connect(lambda on, b=btn: (b.setEnabled(on), set_value(b.value() if on else None)))
            row = QHBoxLayout()
            row.addWidget(own)
            row.addWidget(btn)
            row.addStretch(1)
            form.addRow(f.label, row)
            return
        elif f.kind == "text":
            ed = QLineEdit(value or "")
            ed.setPlaceholderText("Standard")
            ed.editingFinished.connect(lambda e=ed: set_value(e.text() or None))
            w = ed
        elif f.kind == "font":
            fc = QFontComboBox()
            fc.setCurrentText(value)
            fc.currentTextChanged.connect(set_value)
            w = fc
        elif f.kind == "anchor":
            grid = AnchorGrid(cfg.anchor, ANCHOR_TITLES)
            grid.changed.connect(self._change_anchor)
            w = grid
        else:  # pragma: no cover - Schutz gegen Tippfehler in der Beschreibung
            raise ValueError(f"Unbekannte Feldart: {f.kind}")
        if f.tooltip and f.kind != "bool":
            w.setToolTip(f.tooltip)
        form.addRow(f.label, w)

    def _add_custom(self, form: QFormLayout, cfg: WidgetConfig, f: Field) -> None:
        key = f.key
        if key == "channel":
            chan = QComboBox()
            for ch in list(CHANNELS) + [MAP_CHANNEL, CLOCK_CHANNEL]:
                chan.addItem(channel_title(ch), ch)
            chan.setCurrentIndex(max(chan.findData(cfg.channel), 0))
            chan.currentIndexChanged.connect(lambda: self._change_channel(chan.currentData()))
            form.addRow(f.label, chan)
        elif key == "type":
            wtype = QComboBox()
            for k, title in TYPE_TITLES.items():
                wtype.addItem(title, k)
            wtype.setCurrentIndex(max(wtype.findData(cfg.type), 0))
            wtype.currentIndexChanged.connect(lambda: self._change_type(wtype.currentData()))
            form.addRow(f.label, wtype)
        elif key == "range":
            self._range_row(form, cfg)
        elif key == "zones":
            self._zones_row(form, cfg)
        elif key == "grade_scheme":
            self._grade_steps_row(form, cfg)
        elif key in ("shadow", "outline", "badge"):
            cb = QCheckBox(f.label)
            cb.setChecked(getattr(cfg.style, key))
            cb.toggled.connect(lambda v, k=key: self._set_style(k, v))
            ckey = f.keys[1]
            row = QHBoxLayout()
            row.addWidget(cb)
            row.addWidget(ColorButton(getattr(cfg.style, ckey), lambda v, k=ckey: self._set_style(k, v)))
            row.addStretch(1)
            form.addRow("", row)
        elif key == "map_hint":
            if cfg.minimap.background == "osm" and self.c.model.map_data is None:
                form.addRow("", hint_label("Noch keine Kartendaten – links „Kartendaten laden“."))
        elif key == "ribbon_hint":
            form.addRow("", hint_label("Das Höhenband ist nur in der Ansicht „3D“ sichtbar."))
        elif key == "attribution_hint":
            form.addRow("", hint_label("Ohne Hinweis im Video muss die OSM-Quelle anderswo genannt werden "
                                       "(z. B. in der Videobeschreibung).", "gray"))
        elif key == "climb_count":
            cs = cfg.climb
            tel = self.c.model.telemetry
            found = climbs_for(tel, cs.min_length_m, cs.min_gain_m, cs.min_grade, cs.mode) if tel is not None else []
            n_up = sum(not c.is_descent for c in found)
            n_down = sum(c.is_descent for c in found)
            parts = ([f"{n_up} Anstieg(e)"] if cs.mode != "descents" else []) + \
                    ([f"{n_down} Abfahrt(en)"] if cs.mode != "climbs" else [])
            form.addRow("", QLabel("In dieser Fahrt: " + ", ".join(parts)))
        else:  # pragma: no cover
            raise ValueError(f"Unbekanntes Sonderfeld: {key}")

    def _range_row(self, form: QFormLayout, cfg: WidgetConfig) -> None:
        auto = QCheckBox("automatisch")
        auto.setChecked(cfg.min_value is None and cfg.max_value is None)
        lo, hi = QDoubleSpinBox(), QDoubleSpinBox()
        values_range = self.c.model.frame_values([0.0]).range(cfg) if self.c.model.ready else (0.0, 100.0)
        for box, val in ((lo, cfg.min_value if cfg.min_value is not None else values_range[0]),
                         (hi, cfg.max_value if cfg.max_value is not None else values_range[1])):
            box.setRange(-100000, 100000)
            box.setDecimals(1)
            box.setValue(val)
            box.setKeyboardTracking(False)
            box.setEnabled(not auto.isChecked())

        def apply_range():
            if auto.isChecked():
                self._set_many(min_value=None, max_value=None)
            elif hi.value() > lo.value():
                self._set_many(min_value=lo.value(), max_value=hi.value())
            lo.setEnabled(not auto.isChecked())
            hi.setEnabled(not auto.isChecked())

        auto.toggled.connect(lambda _v: apply_range())
        lo.valueChanged.connect(lambda _v: apply_range())
        hi.valueChanged.connect(lambda _v: apply_range())
        row = QHBoxLayout()
        row.addWidget(lo)
        row.addWidget(QLabel("bis"))
        row.addWidget(hi)
        form.addRow("Bereich", row)
        form.addRow("", auto)

    def _zones_row(self, form: QFormLayout, cfg: WidgetConfig) -> None:
        zones = QLineEdit(zones_to_text(cfg.zones))
        zones.setPlaceholderText("z. B. 400:#FFC233, 600:#FF4D4D")
        zones.setToolTip("Ab Wert:Farbe – Farbzonen für Bogen/Balken (und optional die Zahl)")
        zones_err = QLabel("")
        zones_err.setStyleSheet("color:#d03030")

        def apply_zones():
            try:
                self._set("zones", text_to_zones(zones.text()))
                zones_err.setText("")
            except (ValueError, IndexError):
                zones_err.setText("Format: Wert:#RRGGBB, …")

        zones.editingFinished.connect(apply_zones)
        form.addRow("Farbzonen", zones)
        form.addRow("", zones_err)

    # ------------------------------------------------------------------ Änderungen

    def _set(self, key: str, value, rebuild: bool = False) -> None:
        self._set_many(rebuild=rebuild, **{key: value})

    def _set_many(self, rebuild: bool = False, **values) -> None:
        cfg = self._cfg
        if self._building or cfg is None:
            return
        old = {k: getattr(cfg, k) for k in values}
        for k, v in values.items():
            setattr(cfg, k, v)
        try:
            cfg.validate()
        except ValueError as exc:
            for k, v in old.items():
                setattr(cfg, k, v)
            self.c.message.emit(str(exc))
            return
        self.c.layout_edited(None if rebuild else self)
        if rebuild:
            self.schedule_rebuild()

    def schedule_rebuild(self) -> None:
        """Neuaufbau erst nach dem aktuellen Ereignis – sonst würde das auslösende
        Eingabefeld gelöscht, während es noch sein Signal verarbeitet (Absturzgefahr)."""
        if not getattr(self, "_rebuild_pending", False):
            self._rebuild_pending = True
            QTimer.singleShot(0, self._do_rebuild)

    def _do_rebuild(self) -> None:
        self._rebuild_pending = False
        self.rebuild()

    def _change_channel(self, channel: str) -> None:
        cfg = self._cfg
        if self._building or cfg is None:
            return
        if channel == MAP_CHANNEL:
            wtype = cfg.type if cfg.type in POSITION_TYPES else "minimap"
            self._set_many(rebuild=True, channel=channel, type=wtype)
        elif cfg.type in POSITION_TYPES or (cfg.type == "climb" and channel != "elevation"):
            self._set_many(rebuild=True, channel=channel, type="text")
        else:
            self._set_many(rebuild=True, channel=channel)

    def _change_type(self, wtype: str) -> None:
        cfg = self._cfg
        if self._building or cfg is None:
            return
        if wtype in POSITION_TYPES:
            self._set_many(rebuild=True, type=wtype, channel=MAP_CHANNEL)
        elif wtype == "climb":
            self._set_many(rebuild=True, type=wtype, channel="elevation")
        elif cfg.channel == MAP_CHANNEL:
            self._set_many(rebuild=True, type=wtype, channel="elevation" if wtype == "profile" else "speed")
        else:
            self._set_many(rebuild=True, type=wtype)

    def _grade_steps_row(self, form: QFormLayout, cfg: WidgetConfig) -> None:
        scheme = QComboBox()
        for key, (title, _, _) in GRADE_SCHEMES.items():
            scheme.addItem(title, key)
        current = grade_scheme_of(cfg.grade_steps, cfg.grade_colors)
        if current is None:
            scheme.addItem("Eigene Stufen", "custom")
            current = "custom"
        scheme.setCurrentIndex(max(scheme.findData(current), 0))

        def apply_scheme():
            key = scheme.currentData()
            if key in GRADE_SCHEMES:
                _, st, co = GRADE_SCHEMES[key]
                self._set_many(rebuild=True, grade_steps=list(st), grade_colors=list(co))

        scheme.currentIndexChanged.connect(lambda: apply_scheme())
        form.addRow("Farbschema Steigung", scheme)
        steps = QLineEdit("; ".join(f"{v:g}" for v in cfg.grade_steps))
        steps.setToolTip("Grenzen der Farbklassen in % (aufsteigend, z. B. -2, 2, 5, 8, 12):\n"
                         "blau = Abfahrt · grün = flach · gelb · orange · rot · dunkelrot = sehr steil")
        err = QLabel("")
        err.setStyleSheet("color:#d03030")

        def apply():
            try:
                # Trennzeichen: Semikolon, Leerzeichen oder „Komma + Leerzeichen“; „2,5“ ist eine Dezimalzahl
                tokens = steps.text().replace(";", " ").replace(", ", " ").split()
                vals = [float(t.replace(",", ".")) for t in tokens]
                if len(vals) != len(cfg.grade_colors) - 1:
                    raise ValueError(f"genau {len(cfg.grade_colors) - 1} Werte")
                if vals != sorted(vals):
                    raise ValueError("aufsteigend")
                self._set("grade_steps", vals)
                err.setText("")
            except ValueError as exc:
                err.setText(f"Ungültig: {exc}")

        steps.editingFinished.connect(apply)
        form.addRow("Steigungsstufen (%)", steps)
        form.addRow("", err)

    def _set_climb(self, key: str, value, rebuild: bool = False) -> None:
        cfg = self._cfg
        if self._building or cfg is None:
            return
        old = getattr(cfg.climb, key)
        setattr(cfg.climb, key, value)
        try:
            cfg.climb.validate()
        except ValueError as exc:
            setattr(cfg.climb, key, old)
            self.c.message.emit(str(exc))
            return
        self.c.layout_edited(self)
        if key.startswith("min_") or rebuild:
            self.schedule_rebuild()  # Anzahl der erkannten Anstiege aktualisieren

    def _set_minimap(self, key: str, value, rebuild: bool = False) -> None:
        cfg = self._cfg
        if self._building or cfg is None:
            return
        old = getattr(cfg.minimap, key)
        setattr(cfg.minimap, key, value)
        try:
            cfg.minimap.validate()
        except ValueError as exc:
            setattr(cfg.minimap, key, old)
            self.c.message.emit(str(exc))
            return
        self.c.layout_edited(None if rebuild else self)
        if rebuild or key in ("background", "theme", "show_attribution"):
            self.schedule_rebuild()

    def _set_style(self, key: str, value) -> None:
        cfg = self._cfg
        if self._building or cfg is None:
            return
        setattr(cfg.style, key, value)
        self.c.layout_edited(self)

    def _change_anchor(self, anchor: str) -> None:
        """Ankerpunkt wechseln, ohne dass das Widget springt."""
        cfg = self._cfg
        if self._building or cfg is None:
            return
        fw, fh = self.c.model.frame_size()
        x, y, _, _ = cfg.rect(fw, fh)
        cfg.anchor = anchor
        cfg.move_to(x, y, fw, fh)
        self.c.layout_edited(self)
        self.schedule_rebuild()
