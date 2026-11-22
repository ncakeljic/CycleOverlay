"""Linkes Panel: GPX/Video laden, Signale, Widget-Liste."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.units import CHANNELS, format_duration, format_value
from ..rendering.layout import MAP_CHANNEL
from ..rendering.values import CLOCK_CHANNEL
from .controller import Controller

GPX_FILTER = "Fahrten (*.gpx *.GPX *.fit *.FIT);;Alle Dateien (*)"
VIDEO_FILTER = "Videos (*.mp4 *.MP4 *.mov *.MOV *.m4v *.mkv *.insv *.LRV);;Alle Dateien (*)"
TYPE_TITLES = {"text": "Wert + Beschriftung", "arc": "Rundinstrument", "bar": "Balken",
               "minimap": "Minimap (Navi)", "map": "Streckenübersicht", "profile": "Profil (über Distanz)",
               "climb": "Anstiegs-Vorschau"}
STATUS_COLORS = {"ok": "#2e9e4f", "partial": "#d08a00", "outside": "#d03030"}


def channel_title(ch: str) -> str:
    if ch == CLOCK_CHANNEL:
        return "Uhrzeit"
    if ch == MAP_CHANNEL:
        return "Strecke (Position)"
    return CHANNELS[ch].title if ch in CHANNELS else ch


class AddWidgetDialog(QDialog):
    def __init__(self, channels: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Widget hinzufügen")
        self.channel = QComboBox()
        for ch in channels:
            self.channel.addItem(channel_title(ch), ch)
        self.wtype = QComboBox()
        for key, title in TYPE_TITLES.items():
            self.wtype.addItem(title, key)
        form = QFormLayout(self)
        form.addRow("Signal", self.channel)
        form.addRow("Darstellung", self.wtype)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def result_values(self) -> tuple[str, str]:
        return self.channel.currentData(), self.wtype.currentData()


class SignalPanel(QWidget):
    def __init__(self, controller: Controller, parent=None):
        super().__init__(parent)
        self.c = controller
        self._updating = False

        # --- Daten
        self.gpx_btn = QPushButton("GPX/FIT öffnen …")
        self.gpx_btn.clicked.connect(self.choose_gpx)
        self.gpx_label = QLabel("Keine GPX-Datei")
        self.gpx_label.setWordWrap(True)
        self.video_btn = QPushButton("Video öffnen …")
        self.video_btn.clicked.connect(self.choose_video)
        self.video_remove = QPushButton("Entfernen")
        self.video_remove.clicked.connect(self.c.remove_video)
        self.video_label = QLabel("Kein Video (Export-Bereich manuell wählen)")
        self.video_label.setWordWrap(True)
        self.relink_btn = QPushButton("Video neu verknüpfen …")
        self.relink_btn.clicked.connect(self.choose_relink)
        self.map_label = QLabel("")
        self.map_label.setWordWrap(True)
        self.map_btn = QPushButton("Kartendaten laden")
        self.map_btn.setToolTip("Straßen, Wege, Wasser und Wald rund um die Strecke aus OpenStreetMap laden\n"
                                "(Internet nötig, wird im Projekt gespeichert). Für die Minimap.")
        self.map_btn.clicked.connect(self.c.download_map_data)
        self.map_clear = QPushButton("Entfernen")
        self.map_clear.clicked.connect(self.c.clear_map_data)
        self.map_cancel = QPushButton("Abbrechen")
        self.map_cancel.setVisible(False)
        self.map_cancel.clicked.connect(self.c.cancel_map_download)
        self.c.mapDownloadRunning.connect(self._on_map_running)
        self.sync_label = QLabel("")
        self.sync_label.setWordWrap(True)
        self.units = QComboBox()
        self.units.addItem("Metrisch (km/h, m)", "metric")
        self.units.addItem("Imperial (mph, ft)", "imperial")
        self.units.currentIndexChanged.connect(self._on_units)

        data_box = QGroupBox("Daten")
        dl = QVBoxLayout(data_box)
        dl.addWidget(self.gpx_btn)
        dl.addWidget(self.gpx_label)
        vrow = QHBoxLayout()
        vrow.addWidget(self.video_btn, 1)
        vrow.addWidget(self.video_remove)
        dl.addLayout(vrow)
        dl.addWidget(self.video_label)
        dl.addWidget(self.relink_btn)
        dl.addWidget(self.sync_label)
        mrow = QHBoxLayout()
        mrow.addWidget(self.map_btn, 1)
        mrow.addWidget(self.map_clear)
        mrow.addWidget(self.map_cancel)
        dl.addLayout(mrow)
        dl.addWidget(self.map_label)
        urow = QHBoxLayout()
        urow.addWidget(QLabel("Einheiten"))
        urow.addWidget(self.units, 1)
        dl.addLayout(urow)

        # --- Signale
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Signal", "Werte"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        sig_box = QGroupBox("Signale in der Datei")
        QVBoxLayout(sig_box).addWidget(self.table)

        # --- Widgets
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_current)
        self.list.itemChanged.connect(self._on_item_changed)
        self.add_btn = QPushButton("+ Widget")
        self.add_btn.clicked.connect(self.add_widget)
        self.dup_btn = QPushButton("Duplizieren")
        self.dup_btn.clicked.connect(self.c.duplicate_selected)
        self.del_btn = QPushButton("Löschen")
        self.del_btn.clicked.connect(self.c.remove_selected)
        w_box = QGroupBox("Widgets")
        wl = QVBoxLayout(w_box)
        wl.addWidget(self.list)
        brow = QHBoxLayout()
        brow.addWidget(self.add_btn)
        brow.addWidget(self.dup_btn)
        brow.addWidget(self.del_btn)
        wl.addLayout(brow)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(data_box)
        lay.addWidget(sig_box, 1)
        lay.addWidget(w_box, 1)

        c = controller
        c.projectChanged.connect(self.refresh)
        c.layoutChanged.connect(self._on_layout_changed)
        c.selectionChanged.connect(self._select_in_list)
        c.busyChanged.connect(lambda busy, _t: self.setEnabled(not busy))
        self.refresh()

    # ------------------------------------------------------------------ Aktionen

    def choose_gpx(self) -> None:
        start = str(Path(self.c.model.project.gpx_path).parent) if self.c.model.project.gpx_path else ""
        path, _ = QFileDialog.getOpenFileName(self, "Fahrt öffnen (GPX/FIT)", start, GPX_FILTER)
        if path:
            self.c.open_gpx(path)

    def choose_video(self) -> None:
        p = self.c.model.project
        start = str(Path(p.video_path or p.gpx_path or "").parent) if (p.video_path or p.gpx_path) else ""
        path, _ = QFileDialog.getOpenFileName(self, "Video öffnen", start, VIDEO_FILTER)
        if path:
            self.c.open_video(path)

    def _on_map_running(self, running: bool) -> None:
        self.map_cancel.setVisible(running)
        self.map_btn.setEnabled(not running)
        if running:
            self.map_label.setText("Kartendaten werden geladen … (Fortschritt in der Statuszeile)")
        else:
            self.refresh()

    def choose_relink(self) -> None:
        missing = self.c.model.project.video_path or ""
        start = str(Path(missing).parent) if missing else ""
        path, _ = QFileDialog.getOpenFileName(self, f"Video neu verknüpfen ({Path(missing).name})", start, VIDEO_FILTER)
        if path:
            self.c.relink_video(path)

    def add_widget(self) -> None:
        chans = self.c.model.available_channels()
        if not chans:
            return
        dlg = AddWidgetDialog(chans, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            ch, wt = dlg.result_values()
            self.c.add_widget(ch, wt)

    def _on_units(self) -> None:
        if self._updating:
            return
        self.c.model.project.units = self.units.currentData()
        self.c.project_edited()
        self.c.layout_edited()

    # ------------------------------------------------------------------ Anzeige

    def refresh(self) -> None:
        m = self.c.model
        self._updating = True
        self.units.setCurrentIndex(max(self.units.findData(m.project.units), 0))
        self._updating = False
        tel = m.telemetry
        if tel is not None:
            name = Path(m.project.gpx_path).name if m.project.gpx_path else (m.track_name or "")
            try:
                from zoneinfo import ZoneInfo

                local = tel.start_time.astimezone(ZoneInfo(m.project.timezone)) if m.project.timezone \
                    else tel.start_time.astimezone()
            except Exception:  # noqa: BLE001 – unbekannte Zeitzone: Rechnerzeit
                local = tel.start_time.astimezone()
            self.gpx_label.setText(
                f"<b>{name}</b><br>{local:%d.%m.%Y %H:%M} · {format_duration(tel.duration)} · "
                f"{format_value('distance', tel.channels['distance'][-1], m.project.units)}"
                + (f" · {format_value('elevation_gain', tel.channels['elevation_gain'][-1], m.project.units)} hm"
                   if tel.available.get('elevation_gain') else "")
            )
        else:
            self.gpx_label.setText("Keine GPX-Datei")
        self.video_btn.setEnabled(tel is not None)
        self.video_remove.setVisible(m.video is not None)
        if m.video is not None:
            v = m.video
            self.video_label.setText(f"<b>{v.path.name}</b><br>{v.width}x{v.height} · {float(v.fps):.3f} FPS · "
                                     f"{format_duration(v.duration)}")
        elif m.project.video_path:
            self.video_label.setText(f"<span style='color:#d03030'><b>Video offline:</b> {Path(m.project.video_path).name}</span>")
        else:
            self.video_label.setText("Kein Video – Export-Bereich unten in der Timeline wählen")
        self.relink_btn.setVisible(m.video is None and bool(m.project.video_path) and tel is not None)
        has_gps = tel is not None and bool(tel.available.get(MAP_CHANNEL))
        running = self.c.map_download_running
        self.map_btn.setEnabled(has_gps and not running)
        self.map_cancel.setVisible(running)
        self.map_btn.setText("Kartendaten neu laden" if m.map_data is not None else "Kartendaten laden")
        self.map_clear.setVisible(m.map_data is not None and not running)
        if running:
            self.map_label.setText("Kartendaten werden geladen … (Fortschritt in der Statuszeile)")
        else:
            self.map_label.setText(("Karte: " + m.map_status() + (
                "<br><small>© OpenStreetMap-Mitwirkende (ODbL)</small>" if m.map_data is not None else ""))
                if tel is not None else "")
        chk = m.sync_check()
        if m.sync_error:
            self.sync_label.setText(f"<span style='color:#d03030'>Sync: {m.sync_error}</span>")
        elif chk is not None:
            color = STATUS_COLORS[chk.status]
            self.sync_label.setText(f"<span style='color:{color}'>● Sync: {chk.message}</span>")
        else:
            self.sync_label.setText("")

        rows = m.signal_rows()
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            a = QTableWidgetItem(r.title)
            b = QTableWidgetItem(r.summary)
            if not r.available:
                for it in (a, b):
                    it.setForeground(QColor(140, 140, 140))
            self.table.setItem(i, 0, a)
            self.table.setItem(i, 1, b)
        self.add_btn.setEnabled(tel is not None)
        self._on_layout_changed(None)

    def _on_layout_changed(self, sender) -> None:
        if sender is self:
            return
        m = self.c.model
        self._updating = True
        self.list.clear()
        for cfg in m.project.layout.widgets:
            avail = m.telemetry is None or cfg.channel == CLOCK_CHANNEL or bool(m.telemetry.available.get(cfg.channel, False))
            text = f"{channel_title(cfg.channel)}  ·  {TYPE_TITLES.get(cfg.type, cfg.type)}"
            if not avail:
                text += "  (nicht in Datei)"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, cfg.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if cfg.visible else Qt.CheckState.Unchecked)
            if not avail:
                item.setForeground(QColor(140, 140, 140))
            self.list.addItem(item)
        self._updating = False
        self._select_in_list(self.c.selected)
        has_sel = bool(self.c.selected)
        self.dup_btn.setEnabled(has_sel)
        self.del_btn.setEnabled(has_sel)

    def _select_in_list(self, wid: str) -> None:
        self._updating = True
        self.list.setCurrentRow(-1)
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == wid:
                self.list.setCurrentRow(i)
                break
        self._updating = False
        self.dup_btn.setEnabled(bool(wid))
        self.del_btn.setEnabled(bool(wid))

    def _on_current(self, item, _prev) -> None:
        if self._updating:
            return
        self.c.select(item.data(Qt.ItemDataRole.UserRole) if item else "")

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if self._updating:
            return
        cfg = self.c.model.widget(item.data(Qt.ItemDataRole.UserRole))
        if cfg is not None:
            cfg.visible = item.checkState() == Qt.CheckState.Checked
            self.c.layout_edited(self)
