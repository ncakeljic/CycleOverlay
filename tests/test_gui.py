"""Smoke-Tests der Oberfläche (Phase 3). Fenster werden nicht angezeigt."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox  # noqa: E402

from cycle_overlay.rendering.qt_support import ensure_app  # noqa: E402

from .conftest import SAMPLE_GPX, make_clip, needs_ffmpeg  # noqa: E402


def mouse(widget, kind: str, pos: QPointF) -> None:
    types = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove, "release": QEvent.Type.MouseButtonRelease}
    button = Qt.MouseButton.NoButton if kind == "move" else Qt.MouseButton.LeftButton
    ev = QMouseEvent(types[kind], pos, pos, button, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    {"press": widget.mousePressEvent, "move": widget.mouseMoveEvent, "release": widget.mouseReleaseEvent}[kind](ev)


def key(widget, k) -> None:
    widget.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, k, Qt.KeyboardModifier.NoModifier))


def wait_for(cond, timeout: float = 20.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    QApplication.processEvents()
    return cond()


@pytest.fixture()
def win(monkeypatch):
    ensure_app()
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: errors.append(a[1:3]) or QMessageBox.StandardButton.No)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    from cycle_overlay.gui.main_window import MainWindow

    w = MainWindow()
    w.resize(1400, 850)
    w.errors = errors
    yield w
    w.c.set_dirty(False)
    w.c.shutdown()
    w.deleteLater()
    QApplication.processEvents()


@pytest.fixture()
def loaded(win):
    win.c.open_gpx(SAMPLE_GPX)
    assert wait_for(lambda: win.c.model.ready)
    win.c.wait_idle()
    return win


@pytest.fixture()
def clip(tmp_path):
    return make_clip(tmp_path / "GX010001.MP4", creation_time="2026-06-14T10:05:00.000000Z",
                     handler="GoPro AVC", seconds=3.0, size="640x360")


def test_window_starts_empty(win):
    assert "Neues Projekt" in win.windowTitle()
    assert not win.timeline.slider.isEnabled()
    assert not win.export.export_btn.isEnabled()


def test_load_gpx_fills_panels(loaded):
    w = loaded
    assert w.signals.table.rowCount() >= 10
    assert w.signals.list.count() == len(w.c.model.project.layout.widgets)
    assert not w.timeline.inout_row.isHidden()  # ohne Video: In/Out sichtbar
    assert w.timeline.slider.maximum() == 1200 * 10
    assert w.export.export_btn.isEnabled()
    assert "sample_ride" in w.windowTitle()
    assert not w.errors


def test_preview_paints_overlay(loaded):
    w = loaded
    from PySide6.QtGui import QImage

    w.canvas.resize(800, 470)
    w.c.set_position(450)
    w.canvas.render(QImage(w.canvas.size(), QImage.Format.Format_ARGB32))  # Layout stabilisieren
    img = QImage(w.canvas.size(), QImage.Format.Format_ARGB32)  # logische Pixel (auch bei Retina)
    w.canvas.render(img)
    assert img.width() == w.canvas.width()
    # unten links (Tacho) ist nicht nur Schachbrett: es gibt sehr helle Pixel (weiße Schrift)
    r = w.canvas.frame_rect()
    bright = 0
    for x in range(int(r.left()) + 20, int(r.left() + r.width() * 0.3), 3):
        for y in range(int(r.top() + r.height() * 0.7), int(r.bottom()) - 5, 3):
            c = img.pixelColor(x, y)
            if c.red() > 245 and c.green() > 245 and c.blue() > 245:
                bright += 1
    assert bright > 20


def test_select_and_drag_widget(loaded):
    w = loaded
    cv = w.canvas
    cv.resize(960, 560)
    model = w.c.model
    cfg = model.widget("speed")
    fw, fh = model.frame_size()
    x, y, ww, hh = cfg.rect(fw, fh)
    r, s = cv.frame_rect(), cv.scale()
    start = QPointF(r.x() + (x + ww / 2) * s, r.y() + (y + hh / 2) * s)
    mouse(cv, "press", start)
    assert w.c.selected == "speed"
    old = (cfg.x, cfg.y)
    mouse(cv, "move", start + QPointF(120, -80))
    mouse(cv, "release", start + QPointF(120, -80))
    assert (cfg.x, cfg.y) != old
    assert cfg.x > old[0] and cfg.y > old[1]  # Anker unten links: y wächst nach oben
    assert w.c.dirty
    assert w.inspector._pos_fields["x"].value() == pytest.approx(cfg.x * 100, abs=0.06)
    # Klick ins Leere hebt die Auswahl auf
    mouse(cv, "press", r.center())
    assert w.c.selected == ""


def test_keyboard_nudge_and_delete(loaded):
    w = loaded
    w.c.select("power")
    cfg = w.c.model.widget("power")
    old_x = cfg.x
    key(w.canvas, Qt.Key.Key_Right)
    assert cfg.x > old_x
    key(w.canvas, Qt.Key.Key_Delete)
    assert w.c.model.widget("power") is None


def test_inspector_edits(loaded):
    w = loaded
    w.c.select("heart_rate")
    cfg = w.c.model.widget("heart_rate")
    w.inspector._pos_fields["size"].setValue(12.0)
    assert cfg.size == pytest.approx(0.12)
    w.inspector._set("label", "PULS")
    assert cfg.label == "PULS"
    w.inspector._change_type("arc")
    assert cfg.type == "arc"
    w.inspector._change_type("map")
    assert cfg.type == "map" and cfg.channel == "position"
    w.inspector._change_type("profile")
    assert cfg.type == "profile" and cfg.channel == "elevation"
    w.inspector._change_channel("heart_rate")
    assert cfg.channel == "heart_rate"
    w.inspector._change_type("arc")
    fw, fh = w.c.model.frame_size()
    before = cfg.rect(fw, fh)[:2]
    w.inspector._change_anchor("top_right")
    assert cfg.anchor == "top_right"
    assert cfg.rect(fw, fh)[:2] == pytest.approx(before)  # Widget springt nicht


def test_add_widget_and_list(loaded):
    w = loaded
    n = w.signals.list.count()
    w.c.add_widget("vam", "bar")
    assert w.signals.list.count() == n + 1
    assert w.c.selected == "vam"
    item = w.signals.list.item(n)
    item.setCheckState(Qt.CheckState.Unchecked)
    assert not w.c.model.widget("vam").visible


def test_in_out_and_play(loaded):
    w = loaded
    w.c.set_position(400)
    w.timeline._set_mark("in")
    w.c.set_position(402)
    w.timeline._set_mark("out")
    assert w.c.model.export_range() == (400, 402)
    assert "Clip 0:00:02" in w.timeline.range_label.text()
    w.c.set_position(400)
    w.c.play()
    assert wait_for(lambda: w.c.position > 400.3, 5)
    w.c.stop()
    assert not w.c.is_playing


@needs_ffmpeg
def test_export_from_panel(loaded, tmp_path):
    w = loaded
    m = w.c.model
    m.set_in_out(440, 441)
    e = m.project.export
    e.size_from_video = False
    e.width, e.height = 320, 180
    e.fps = "25"
    e.out_dir = str(tmp_path)
    e.filename = "test_export"
    w.c.project_edited()
    assert w.export.size_combo.currentData() == "custom"
    w.export.start()
    assert wait_for(lambda: not w.export.running, 60)
    res = w.export.last_result
    assert res is not None and res.path == tmp_path / "test_export.mov"
    assert res.n_frames == 25 and res.pix_fmt.startswith("yuva")
    assert "Fertig" in w.export.status.text()


@needs_ffmpeg
def test_export_cancel(loaded, tmp_path):
    w = loaded
    m = w.c.model
    m.set_in_out(0, 600)
    e = m.project.export
    e.size_from_video = False
    e.width, e.height = 1280, 720
    out = tmp_path / "out"
    out.mkdir()
    e.out_dir = str(out)
    e.filename = "abbruch"
    w.c.project_edited()
    w.export.start()
    assert wait_for(lambda: w.export.bar.value() > 2, 30)
    w.export.cancel()
    assert wait_for(lambda: not w.export.running, 30)
    assert "abgebrochen" in w.export.status.text()
    assert list(out.iterdir()) == []


@needs_ffmpeg
def test_video_sync_flow(loaded, clip):
    w = loaded
    w.c.model.set_sync_options(timezone="Europe/Berlin")
    w.c.open_video(clip)
    assert wait_for(lambda: w.c.model.has_video)
    w.c.wait_idle()
    assert w.timeline.inout_row.isHidden()
    assert "vollständig" in w.sync.status.text()
    assert w.export.size_combo.currentData() == "video"
    assert w.c.model.track_time(0) == 300
    # Hintergrundbild wird nachgeladen
    w.c.set_position(1.0)
    assert wait_for(lambda: w.c.background is not None, 20)
    assert w.c.background.shape[2] == 3
    # Offset +1 s
    w.c.nudge_offset(seconds=1)
    fps = float(w.c.model.fps())
    one = round(fps) / fps  # Versatz rastet auf ganze Frames ein: 1 s ≈ 30 Frames bei 29,97 FPS
    assert w.c.model.project.offset_s == pytest.approx(one)
    assert w.c.model.track_time(0) == pytest.approx(300 + one)
    # Sync-Punkt: Video 1,0 s gehört zu Track 310 s
    w.c.set_position(1.0)
    w.c.sync_point(310.0)
    assert w.c.model.track_time(1.0) == pytest.approx(310.0, abs=0.04)
    # Wiedergabe mit Videobildern
    w.c.set_position(0)
    w.c.play()
    assert wait_for(lambda: w.c.position > 0.3, 10)
    w.c.stop()


@needs_ffmpeg
def test_video_wrong_timezone_suggestion(loaded, clip):
    w = loaded
    w.c.model.set_sync_options(timezone="UTC")
    w.c.open_video(clip)
    w.c.wait_idle()
    assert wait_for(lambda: w.c.model.video is not None)
    assert not w.sync.suggest_btn.isHidden()
    w.sync._apply_suggestion()
    assert w.c.model.sync_check().status == "ok"


@needs_ffmpeg
def test_project_save_and_open(loaded, clip, tmp_path):
    w = loaded
    w.c.model.set_sync_options(timezone="Europe/Berlin", offset_s=0.5)
    w.c.open_video(clip)
    w.c.wait_idle()
    w.c.add_widget("gradient", "bar")
    path = w.c.save_project(tmp_path / "p")
    assert path.exists() and not w.c.dirty
    w.c.new_project()
    assert not w.c.model.ready
    w.c.open_project(path)
    assert wait_for(lambda: w.c.model.ready and w.c.model.has_video)
    w.c.wait_idle()
    assert w.c.model.project.offset_s == 0.5
    assert w.c.model.widget("gradient_2") is not None or w.c.model.widget("gradient") is not None


def test_open_paths_and_presets(win):
    win.open_paths([str(SAMPLE_GPX)])
    assert wait_for(lambda: win.c.model.ready)
    win._fill_presets()
    names = [a.text() for a in win.preset_menu.actions()]
    assert "rennrad_minimal" in names
    win._apply_preset("rennrad_minimal")
    assert win.c.model.project.layout.name == "Rennrad Minimal"


def test_units_switch(loaded):
    w = loaded
    w.signals.units.setCurrentIndex(w.signals.units.findData("imperial"))
    assert w.c.model.project.units == "imperial"
    assert "mi" in w.signals.gpx_label.text()


def test_main_routes_to_cli(capsys):
    from cycle_overlay.main import main

    assert main(["info", str(SAMPLE_GPX)]) == 0
    assert "Distanz" in capsys.readouterr().out


@needs_ffmpeg
def test_batch_export_from_panel(loaded, tmp_path):
    w = loaded
    a = make_clip(tmp_path / "GX010001.MP4", creation_time="2026-06-14T10:05:00.000000Z", handler="GoPro AVC",
                  seconds=1.0, size="320x180")
    b = make_clip(tmp_path / "GX020001.MP4", creation_time="2026-06-14T10:06:00.000000Z", handler="GoPro AVC",
                  seconds=1.0, size="320x180")
    far = make_clip(tmp_path / "x.mp4", creation_time="2026-07-01T10:00:00.000000Z", seconds=1.0, size="320x180")
    w.c.model.set_sync_options(timezone="Europe/Berlin")
    t0 = time.monotonic()
    w.export.start_batch([str(a), str(b), str(far)])
    assert w.export.running
    assert wait_for(lambda: not w.export.running, 90)
    assert time.monotonic() - t0 < 30  # kein Hängen zwischen den Clips
    assert (tmp_path / "GX010001_overlay.mov").exists() and (tmp_path / "GX020001_overlay.mov").exists()
    assert "2 von 2" in w.export.status.text()


def test_add_map_and_profile_widgets(loaded):
    w = loaded
    w.c.add_widget("position", "text")  # wird automatisch zur Minimap
    cfg = w.c.model.widget(w.c.selected)
    assert cfg.type == "minimap" and cfg.channel == "position"
    w.c.add_widget("position", "map")
    assert w.c.model.widget(w.c.selected).type == "map"
    w.c.add_widget("heart_rate", "profile")
    assert w.c.model.widget(w.c.selected).type == "profile"
    w.canvas.resize(800, 470)
    w.canvas.repaint()


def test_minimap_in_inspector(loaded):
    w = loaded
    w.c.add_widget("position", "minimap")
    cfg = w.c.model.widget(w.c.selected)
    assert cfg.type == "minimap"
    w.inspector._set_minimap("view", "tilt", rebuild=True)
    assert wait_for(lambda: not getattr(w.inspector, "_rebuild_pending", False), 5)
    assert cfg.minimap.view == "tilt"
    w.inspector._set_minimap("theme", "light")
    w.inspector._set_minimap("tilt_deg", 200.0)  # ungültig -> bleibt
    assert cfg.minimap.tilt_deg == 55.0
    w.inspector._change_type("text")
    assert cfg.type == "text" and cfg.channel == "speed"
    QApplication.processEvents()
    w.canvas.resize(800, 470)
    w.canvas.repaint()


def test_map_download_and_bundle_flow(loaded, tmp_path, monkeypatch):
    import json

    from cycle_overlay.core.project import ProjectModel

    fake = json.dumps({"elements": [{"type": "way", "tags": {"highway": "residential"},
                                     "geometry": [{"lat": 48.0, "lon": 11.0}, {"lat": 48.01, "lon": 11.0}]}]}).encode()
    orig = ProjectModel.download_map_data
    monkeypatch.setattr(ProjectModel, "download_map_data",
                        lambda self, *a, **k: orig(self, post=lambda u, q, t: fake))
    w = loaded
    assert w.signals.map_btn.isEnabled()
    w.c.download_map_data()
    assert wait_for(lambda: w.c.model.map_data is not None)
    w.c.wait_idle()
    assert "1 Wege" in w.signals.map_label.text()
    path = w.c.save_project(tmp_path / "paket")
    w.c.new_project()
    w.c.open_project(path)
    assert wait_for(lambda: w.c.model.ready)
    w.c.wait_idle()
    assert w.c.model.map_data is not None
    w.c.clear_map_data()
    assert w.c.model.map_data is None and w.c.dirty


@needs_ffmpeg
def test_offline_video_relink(loaded, clip, tmp_path):
    w = loaded
    w.c.model.set_sync_options(timezone="Europe/Berlin", offset_s=1.5)
    w.c.open_video(clip)
    w.c.wait_idle()
    path = w.c.save_project(tmp_path / "p")
    moved = clip.with_name("neu.MP4")
    clip.rename(moved)
    w.c.open_project(path)
    w.c.wait_idle()
    assert wait_for(lambda: w.c.model.ready)
    assert not w.signals.relink_btn.isHidden()
    assert "offline" in w.signals.video_label.text()
    assert any("offline" in e[1] for e in w.errors) or w.errors
    w.c.relink_video(moved)
    assert wait_for(lambda: w.c.model.has_video)
    w.c.wait_idle()
    assert w.c.model.project.offset_s == 1.5
    assert w.signals.relink_btn.isHidden()


def test_unsaved_project_close_deletes_downloads(loaded, monkeypatch):
    import json

    from cycle_overlay import app_paths
    from cycle_overlay.core.project import ProjectModel

    fake = json.dumps({"elements": [{"type": "way", "tags": {"highway": "residential"},
                                     "geometry": [{"lat": 48.0, "lon": 11.0}, {"lat": 48.01, "lon": 11.0}]}]}).encode()
    orig = ProjectModel.download_map_data
    monkeypatch.setattr(ProjectModel, "download_map_data",
                        lambda self, *a, **k: orig(self, post=lambda u, q, t: fake))
    w = loaded
    w.c.download_map_data()
    assert wait_for(lambda: w.c.model.map_data is not None)
    w.c.wait_idle()
    session = app_paths.session_cache_dir()
    assert list(session.rglob("osm_*.json"))
    w.c.new_project()  # ohne Speichern (Rückfrage wird in der Oberfläche vorher gestellt)
    assert not session.exists()
    assert w.c.model.map_data is None


def test_climb_widget_and_grade_options(loaded):
    w = loaded
    w.c.add_widget("speed", "climb")
    cfg = w.c.model.widget(w.c.selected)
    assert cfg.type == "climb" and cfg.channel == "elevation"
    assert wait_for(lambda: not getattr(w.inspector, "_rebuild_pending", False), 5)
    w.inspector._set_climb("lookahead_m", 5000.0)
    assert cfg.climb.lookahead_m == 5000.0
    w.inspector._set_climb("min_length_m", 10.0)  # ungültig
    assert cfg.climb.min_length_m == 500.0
    w.c.add_widget("position", "minimap")
    mm = w.c.model.widget(w.c.selected)
    w.inspector._set_minimap("route_coloring", "gradient")
    w.inspector._set_minimap("elevation_ribbon", True, rebuild=True)
    w.inspector._set_minimap("view", "tilt", rebuild=True)
    QApplication.processEvents()
    assert mm.minimap.elevation_ribbon and mm.minimap.route_coloring == "gradient"
    w.inspector._set("grade_steps", [-3.0, 1.0, 4.0, 7.0, 10.0])
    assert mm.grade_steps[0] == -3.0
    w.inspector._set("grade_steps", [5.0, 1.0, 4.0, 7.0, 10.0])  # nicht aufsteigend -> abgelehnt
    assert mm.grade_steps[0] == -3.0
    w.canvas.resize(800, 470)
    w.canvas.repaint()


def test_descent_options_in_inspector(loaded):
    from cycle_overlay.rendering.layout import GRADE_SCHEMES

    w = loaded
    w.c.add_widget("elevation", "climb")
    cfg = w.c.model.widget(w.c.selected)
    QApplication.processEvents()
    w.inspector._set_climb("mode", "both", rebuild=True)
    assert cfg.climb.mode == "both"
    _, steps, colors = GRADE_SCHEMES["both"]
    w.inspector._set_many(rebuild=True, grade_steps=list(steps), grade_colors=list(colors))
    assert cfg.grade_steps[0] == -12.0
    w.c.add_widget("position", "minimap")
    mm = w.c.model.widget(w.c.selected)
    QApplication.processEvents()
    w.inspector._set_minimap("ribbon_reference", "lowest")
    assert mm.minimap.ribbon_reference == "lowest"
    w.inspector._set_minimap("ribbon_reference", "irgendwo")
    assert mm.minimap.ribbon_reference == "lowest"
    assert wait_for(lambda: not getattr(w.inspector, "_rebuild_pending", False), 5)


@needs_ffmpeg
def test_wrong_camera_clock_starts_with_track(loaded, tmp_path):
    w = loaded
    old = make_clip(tmp_path / "VID_20210321_094903_024.mp4", creation_time="2021-03-21T08:49:03.000000Z",
                    seconds=2.0, size="320x180")
    w.c.open_video(old)
    assert wait_for(lambda: w.c.model.has_video)
    w.c.wait_idle()
    assert w.c.model.sync.anchored
    assert w.c.model.track_time(0) == 0
    assert w.c.model.sync_check().status == "ok"
    assert "Video beginnt mit dem Track" in w.sync.status.text()
    # Versatz als H:MM:SS eingeben
    box = w.sync.offset
    box.lineEdit().setText("0:01:30,5")
    box.interpretText()
    w.c.wait_idle()
    assert w.c.model.project.offset_s == pytest.approx(90.5)
    assert w.c.model.track_time(0) == pytest.approx(90.5)
    assert box.text() == "+0:01:30.500"
    box.lineEdit().setText("-12")
    box.interpretText()
    assert w.c.model.project.offset_s == pytest.approx(-12)
    # Zeitbezug ausdrücklich auf Aufnahmezeit -> Video liegt außerhalb
    w.sync.time_source.select("metadata")
    assert w.c.model.sync_check().status == "outside"


def _sync_view(w):
    """Sync-Tab öffnen und die Zeitleiste auf eine feste Größe bringen (Fenster ist nicht sichtbar)."""
    w.tabs.setCurrentWidget(w.sync)
    lanes = w.sync_timeline.lanes
    lanes.resize(1000, lanes.total_height())
    lanes.axis.set_width(1000)
    lanes.refresh()
    lanes.fit_clip()
    return lanes


@needs_ffmpeg
def test_sync_timeline_tab_and_keys(loaded, clip):
    w = loaded
    w.c.model.set_sync_options(timezone="Europe/Berlin")
    w.c.open_video(clip)
    assert wait_for(lambda: w.c.model.has_video)
    w.c.wait_idle()
    assert w.sync_timeline.isHidden()
    lanes = _sync_view(w)
    assert not w.sync_timeline.isHidden() and w.c.sync_mode
    assert w.timeline.slider.isHidden()
    fps = float(w.c.model.fps())
    key(lanes, Qt.Key.Key_Right)
    assert w.c.model.project.offset_s == pytest.approx(1 / fps)
    key(lanes, Qt.Key.Key_Left)
    key(lanes, Qt.Key.Key_Left)
    assert w.c.model.project.offset_s == pytest.approx(-1 / fps)
    def snap(v):
        return round(v * fps) / fps

    key(lanes, Qt.Key.Key_Up)
    up = snap(1 - 1 / fps)
    assert w.c.model.project.offset_s == pytest.approx(up)
    key(lanes, Qt.Key.Key_Down)
    key(lanes, Qt.Key.Key_Down)
    down = snap(snap(up - 1) - 1)
    assert w.c.model.project.offset_s == pytest.approx(down)
    # Rückgängig / Wiederherstellen
    w.c.undo_offset()
    assert w.c.model.project.offset_s == pytest.approx(snap(up - 1))
    w.c.redo_offset()
    assert w.c.model.project.offset_s == pytest.approx(down)
    # Pfeiltasten in der Vorschau (ohne ausgewähltes Widget) verschieben im Sync-Tab ebenfalls das Video
    w.c.select("")
    before = w.c.model.project.offset_s
    key(w.canvas, Qt.Key.Key_Right)
    assert w.c.model.project.offset_s == pytest.approx(before + 1 / fps)
    # zurück zum Widget-Tab: normale Zeitleiste
    w.tabs.setCurrentWidget(w.inspector)
    assert w.sync_timeline.isHidden() and not w.c.sync_mode


@needs_ffmpeg
def test_sync_timeline_drag_clip_and_zoom(loaded, clip):
    w = loaded
    w.c.model.set_sync_options(timezone="Europe/Berlin")
    w.c.open_video(clip)
    assert wait_for(lambda: w.c.model.has_video)
    w.c.wait_idle()
    lanes = _sync_view(w)
    a = lanes.axis
    start, end = lanes.clip_range()
    y = lanes.lane_rects()["video"].center().y()
    x0 = (a.x_of(start) + a.x_of(end)) / 2
    mouse(lanes, "press", QPointF(x0, y))
    mouse(lanes, "move", QPointF(x0 + 50, y))
    mouse(lanes, "release", QPointF(x0 + 50, y))
    fps = float(w.c.model.fps())
    expected = round(50 * a.spp * fps) / fps
    assert w.c.model.project.offset_s == pytest.approx(expected, abs=1 / fps)
    assert w.c.offset_history.can_undo
    w.c.undo_offset()
    assert w.c.model.project.offset_s == 0
    # Klick in die Kurve setzt den Abspielkopf (Video-Zeit)
    gy = lanes.lane_rects()["gpx"].center().y()
    mouse(lanes, "press", QPointF(a.x_of(start + 1.5), gy))
    mouse(lanes, "release", QPointF(a.x_of(start + 1.5), gy))
    assert w.c.position == pytest.approx(1.5, abs=a.spp * 2)
    # Zoom: Knöpfe und Regler
    lanes.fit_all()
    assert w.sync_timeline.zoom.value() == 0
    w.sync_timeline.zoom.setValue(1000)
    assert a.spp == pytest.approx(a.min_spp)
    w.sync_timeline.clip_btn.click()
    assert a.start <= start and a.end >= end
    # Sync-Punkt per Klick in die Kurve
    w.c.set_position(1.0)
    w.sync_timeline.pick_btn.setChecked(True)
    mouse(lanes, "press", QPointF(a.x_of(start + 0.5), gy))
    assert not w.sync_timeline.pick_btn.isChecked()
    assert w.c.model.track_time(1.0) == pytest.approx(start + 0.5, abs=a.spp * 2 + 1 / fps)
    # Zeichnen mit allen Spuren läuft ohne Fehler
    img = lanes.grab()
    assert not img.isNull()


@needs_ffmpeg
def test_sync_timeline_analysis_and_auto_sync(loaded, tmp_path):
    w = loaded
    moving = make_clip(tmp_path / "GX010002.MP4", creation_time="2026-06-14T10:05:00.000000Z",
                       handler="GoPro AVC", seconds=3.0, size="320x180")
    w.c.model.set_sync_options(timezone="Europe/Berlin")
    w.c.open_video(moving)
    assert wait_for(lambda: w.c.model.has_video)
    w.c.wait_idle()
    assert wait_for(lambda: w.c.analysis is not None or w.c.analysis_error, 60)
    assert w.c.analysis is not None, w.c.analysis_error
    assert w.c.analysis.thumbs.shape[0] >= 2
    lanes = _sync_view(w)
    assert len(lanes._thumbs()) == w.c.analysis.thumbs.shape[0]
    assert not lanes.grab().isNull()
    w.sync_timeline.auto_btn.click()  # schwarzer Testclip: kein oder unsicherer Vorschlag, aber kein Fehler
    if w.c.suggestion is not None:
        assert w.sync_timeline.accept_btn.isVisibleTo(w.sync_timeline)
        w.sync_timeline.accept_btn.click()
        assert w.c.suggestion is None


@needs_ffmpeg
def test_sync_timeline_far_away_clip(loaded, tmp_path):
    w = loaded
    old = make_clip(tmp_path / "VID_old.mp4", creation_time="2021-03-21T08:49:03.000000Z", seconds=2.0,
                    size="320x180")
    w.c.open_video(old)
    assert wait_for(lambda: w.c.model.has_video)
    w.c.wait_idle()
    w.sync.time_source.select("metadata")
    lanes = _sync_view(w)
    assert lanes.axis.lo < lanes.clip_range()[0]
    lanes.fit_all()
    assert not lanes.grab().isNull()
    # „Clip hierher“ holt ihn in die Mitte der Ansicht
    lanes.axis.show(100, 400)
    w.sync_timeline.here_btn.click()
    a, b = lanes.clip_range()
    assert 100 < a < 400 and abs((a + b) / 2 - 250) < 1


@pytest.mark.parametrize("channel,wtype", [("speed", "text"), ("power", "arc"), ("heart_rate", "bar"),
                                           ("position", "map"), ("elevation", "profile"),
                                           ("position", "minimap"), ("elevation", "climb"),
                                           ("time_of_day", "text")])
def test_inspector_sections_for_every_type(loaded, channel, wtype):
    from cycle_overlay.gui.form_kit import SegmentedControl, SliderSpin

    w = loaded
    w.c.add_widget(channel, wtype)
    QApplication.processEvents()
    ins = w.inspector
    assert "content" in ins.sections and "position" in ins.sections
    # alle Abschnitte aufklappen und jede Knopfreihe einmal bedienen
    for sec in ins.sections.values():
        sec.set_expanded(True)
        assert sec.body.isVisibleTo(sec)
    assert ins.widget().findChildren(SegmentedControl), "kleine Auswahlen sollten als Knopfreihe erscheinen"
    for i in range(6):
        # nach jedem Klick kann das Panel neu aufgebaut werden -> Knopfreihen jedes Mal neu suchen
        segs = ins.widget().findChildren(SegmentedControl)
        if i >= len(segs):
            break
        segs[i].select(segs[i]._values[-1])
        assert wait_for(lambda: not getattr(ins, "_rebuild_pending", False), 5)
    assert ins.widget().findChildren(SliderSpin)
    assert not w.errors


def test_inspector_remembers_open_sections(loaded):
    w = loaded
    w.c.add_widget("position", "minimap")
    QApplication.processEvents()
    ins = w.inspector
    assert ins.sections["view"].expanded and not ins.sections["arrow"].expanded
    ins.sections["arrow"].set_expanded(True)
    ins.sections["view"].set_expanded(False)
    ins.rebuild()
    assert ins.sections["arrow"].expanded and not ins.sections["view"].expanded
    # Knopfreihe „Ansicht“ -> 3D zeigt die Neigung
    from cycle_overlay.gui.form_kit import SegmentedControl

    ins.sections["view"].set_expanded(True)
    cfg = w.c.model.widget(w.c.selected)
    view = next(s for s in ins.sections["view"].findChildren(SegmentedControl) if "tilt" in s._values)
    view.select("tilt")
    assert wait_for(lambda: not getattr(ins, "_rebuild_pending", False), 5)
    assert cfg.minimap.view == "tilt"
    labels = [lab.text() for lab in ins.sections["view"].findChildren(QLabel)]
    assert any("Neigung" in t for t in labels)
    # Anker-Raster
    from cycle_overlay.gui.form_kit import AnchorGrid

    grid = ins.sections["position"].findChildren(AnchorGrid)[0]
    grid.select("top_left")
    assert cfg.anchor == "top_left"


def test_export_and_sync_panels_use_sections(loaded):
    w = loaded
    assert set(w.export.sections) == {"format", "image", "target"}
    keys = w.export.profile._values
    w.export.profile.select(keys[-1])
    assert w.c.model.project.export.profile == keys[-1]
    w.export.profile.select("prores4444")
    assert set(w.sync.sections) == {"time", "offset", "help"}
    assert not w.sync.sections["help"].expanded


def test_language_menu_switches_overlay_texts(loaded):
    from cycle_overlay.core.presets import load_preset

    w = loaded
    lay = w.c.model.project.layout
    assert lay.language == "de"
    w.language_actions["en"].trigger()
    assert lay.language == "en" and w.c.dirty
    w._sync_language_menu()
    assert w.language_actions["en"].isChecked() and not w.language_actions["de"].isChecked()
    w.c.set_layout(load_preset())  # Preset laden behält die Sprache
    assert w.c.model.project.layout.language == "en"
    w.language_actions["de"].trigger()
    assert w.c.model.project.layout.language == "de"


def test_export_profile_list_shows_note(loaded):
    from cycle_overlay.export.profiles import PROFILES

    w = loaded
    keys = w.export.profile._values
    assert len(keys) >= 5  # mehr als 4 Formate -> Auswahlliste
    w.export.profile.select("qtrle")
    assert w.c.model.project.export.profile == "qtrle"
    assert w.export.profile_note.text() == PROFILES["qtrle"].note
    w.export.profile.select("prores4444")


def test_export_content_switch_without_video(loaded):
    w = loaded
    ex = w.export
    assert ex.content.value() == "overlay"
    assert not ex.content.buttons[1].isEnabled()  # ohne Video nicht wählbar
    assert "zuerst ein Video" in ex.content_hint.text()
    assert not ex.video_profile.isVisibleTo(ex) and not ex.quality.isVisibleTo(ex)


@needs_ffmpeg
def test_export_with_video_from_panel(loaded, clip, tmp_path):
    import subprocess

    w = loaded
    w.c.model.set_sync_options(timezone="Europe/Berlin")
    w.c.open_video(clip)
    assert wait_for(lambda: w.c.model.has_video)
    w.c.wait_idle()
    ex = w.export
    assert ex.content.buttons[1].isEnabled()
    ex.content.select("video")
    m = w.c.model
    assert m.project.export.content == "video" and m.export_with_video
    assert ex.video_profile.isVisibleTo(ex) and not ex.profile.isVisibleTo(ex)
    assert ex.quality.isVisibleTo(ex)
    assert "eingebrannt" in ex.content_hint.text()
    ex.video_profile.select("mov_prores422")  # immer verfügbar (prores_ks)
    assert not ex.quality.isVisibleTo(ex)  # Qualität nur für H.264/H.265
    ex.video_profile.select("mp4_h264")
    m.project.export.out_dir = str(tmp_path)
    w.c.project_edited()
    assert m.default_output().name == "GX010001_mit_daten.mp4"
    ex.start()
    assert wait_for(lambda: not ex.running, 120)
    res = ex.last_result
    assert res is not None and res.path.suffix == ".mp4", ex.status.text()
    assert "Video mit Anzeigen" in ex.status.text()
    kinds = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                            str(res.path)], capture_output=True, text=True).stdout.split()
    assert "video" in kinds
    # zurück auf „Nur Anzeigen“: wieder transparentes Format
    ex.content.select("overlay")
    assert m.active_profile().key == m.project.export.profile and not m.active_profile().with_video


def test_multiple_suggestions_cycle(loaded):
    from cycle_overlay.core.video_motion import SyncSuggestion

    w = loaded
    c = w.c
    c.suggestions = [SyncSuggestion(1.0, 101.0, 0.8, 0.5, "hoch", rank=1),
                     SyncSuggestion(2.0, 202.0, 0.6, 0.8, "mittel", rank=2)]
    c.suggestionChanged.emit()
    assert c.suggestion.rank == 1
    c.select_suggestion(1)
    assert c.suggestion.rank == 2
    c.select_suggestion(1)
    assert c.suggestion.rank == 1
    assert "1/2" in w.sync_timeline.info.full_text()
    c.clear_suggestions()
    assert c.suggestion is None


@needs_ffmpeg
def test_suggestions_place_video_and_restore(loaded, clip):
    from cycle_overlay.core.video_motion import SyncSuggestion

    w = loaded
    c = w.c
    c.model.set_sync_options(timezone="Europe/Berlin")
    c.open_video(clip)
    assert wait_for(lambda: c.model.has_video)
    c.wait_idle()
    _sync_view(w)
    fps = float(c.model.fps())
    start = c.model.project.offset_s
    c._probe_origin = start
    c.suggestions = [SyncSuggestion(round(5 * fps) / fps, 305.0, 0.8, 0.5, "hoch", rank=1),
                     SyncSuggestion(round(-7 * fps) / fps, 293.0, 0.6, 0.8, "mittel", rank=2)]
    c.show_suggestion(0)
    assert c.model.project.offset_s == pytest.approx(5, abs=1 / fps)
    c.select_suggestion(1)
    assert c.model.project.offset_s == pytest.approx(-7, abs=1 / fps)
    w.sync_timeline.dismiss_btn.click()  # Verwerfen -> alte Stelle
    assert c.model.project.offset_s == pytest.approx(start)
    # Behalten: bleibt und ist rückgängig machbar
    c._probe_origin = start
    c.suggestions = [SyncSuggestion(round(5 * fps) / fps, 305.0, 0.8, 0.5, "hoch", rank=1)]
    c.show_suggestion(0)
    w.sync_timeline.accept_btn.click()
    assert c.model.project.offset_s == pytest.approx(5, abs=1 / fps) and c.suggestion is None
    c.undo_offset()
    assert c.model.project.offset_s == pytest.approx(start)
    # Übereinstimmung wird angezeigt, sobald die Analyse da ist (schwarzer Clip: kein Wert, aber kein Fehler)
    assert wait_for(lambda: c.analysis is not None or c.analysis_error, 60)
    w.sync_timeline._update_match()


@needs_ffmpeg
def test_save_sync_diagnostics(loaded, clip, tmp_path):
    w = loaded
    w.c.model.set_sync_options(timezone="Europe/Berlin")
    w.c.open_video(clip)
    assert wait_for(lambda: w.c.model.has_video)
    w.c.wait_idle()
    assert wait_for(lambda: w.c.analysis is not None or w.c.analysis_error, 60)
    out = w.save_sync_diagnostics(str(tmp_path / "diag.npz"))
    assert out is not None and out.exists() and out.with_suffix(".txt").exists()
    assert "Richtige Stelle" in out.with_suffix(".txt").read_text(encoding="utf-8")


@needs_ffmpeg
def test_sync_timeline_wind_signal(loaded, tmp_path):
    import subprocess

    clip = tmp_path / "GX010003.MP4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=320x180:r=30000/1001:d=4",
                    "-f", "lavfi", "-i", "anoisesrc=d=4:c=brown:a=0.4", "-shortest",
                    "-metadata", "creation_time=2026-06-14T10:05:00.000000Z",
                    "-metadata:s:v:0", "handler_name=GoPro AVC", "-c:v", "mpeg4", "-c:a", "aac", str(clip)],
                   check=True)
    w = loaded
    w.c.model.set_sync_options(timezone="Europe/Berlin")
    w.c.open_video(clip)
    assert wait_for(lambda: w.c.model.has_video)
    w.c.wait_idle()
    assert wait_for(lambda: w.c.analysis is not None or w.c.analysis_error, 60)
    assert w.c.analysis is not None and w.c.analysis.wind is not None
    lanes = _sync_view(w)
    assert not lanes.grab().isNull()
    w.sync_timeline.signal_mode.select("video")
    assert w.c.sync_signals == "video"
    w.sync_timeline.signal_mode.select("audio")
    w.sync_timeline._update_match()
    w.sync_timeline.auto_btn.click()  # darf mit Ton ohne Fehler laufen


def test_filter_tab(loaded):
    w = loaded
    tel_before = w.c.model.telemetry
    assert w.tabs.indexOf(w.filters) >= 0
    seg = w.filters.controls["speed"]
    assert seg.value() == "low" and seg.isEnabled()
    seg.select("strong")
    assert w.c.model.filter_level("speed") == "strong"
    assert w.c.model.telemetry is not tel_before and w.c.dirty
    w.filters.pause_mode.select("hold")
    assert w.c.model.project.pause_mode == "hold"
    w.filters.count_numbers.select(False)
    assert w.c.model.project.count_numbers is False
    assert w.c.model.frame_values([300.0]).count_numbers is False
    w.filters.reset_btn.click()
    assert w.c.model.project.filters == {} and w.c.model.project.pause_mode == "zero"
    assert w.c.model.project.count_numbers is True
    assert w.filters.controls["speed"].value() == "low"


def test_timeline_toolbar_fits_and_mnemonics_escaped(loaded):
    from cycle_overlay.gui.form_kit import CollapsibleSection

    w = loaded
    w.tabs.setCurrentWidget(w.sync)
    st = w.sync_timeline
    st.resize(900, 400)
    st.info.setText("Vorschlag 1/5: 65 % · Versatz +2:29:31.930 · Sicherheit niedrig – Video liegt probeweise dort")
    assert st.info.full_text().startswith("Vorschlag 1/5")
    # „Kamera & Zeit“ darf nicht als Tastenkürzel verschluckt werden
    sec = w.sync.sections["time"]
    assert isinstance(sec, CollapsibleSection) and sec.header.text() == "Kamera && Zeit"


def _fake_release(version="9.9.0"):
    return {"tag_name": f"v{version}", "html_url": "https://example.invalid/r", "body": "- neu",
            "assets": [{"name": f"CycleOverlay-Setup-{version}.exe", "size": 3,
                        "browser_download_url": "https://example.invalid/s.exe"}]}


def test_update_check_shows_dialog_and_skip(win, monkeypatch):
    from cycle_overlay.gui import update_dialog as ud

    monkeypatch.setattr(win.settings, "save", lambda *a, **k: None)
    shown = []

    def fake_exec(dlg):
        shown.append(dlg)
        dlg.choice = ud.UpdateDialog.SKIP
        return 1

    monkeypatch.setattr(ud.UpdateDialog, "exec", fake_exec)
    mgr = ud.UpdateManager(win, fetch=lambda: _fake_release())
    mgr.check(manual=True)
    assert wait_for(lambda: shown, 10)
    assert mgr.last_info.version == "9.9.0"
    assert not shown[0].install_btn.isVisibleTo(shown[0])  # aus dem Quellcode gestartet: nur Release-Seite
    assert win.settings.skipped_version == "9.9.0"
    # automatische Prüfung zeigt eine übersprungene Version nicht erneut
    shown.clear()
    win.settings.check_updates, win.settings.last_update_check = True, 0
    mgr.auto_check()
    assert wait_for(lambda: mgr._check is None, 10)
    QApplication.processEvents()
    assert not shown and win.settings.last_update_check > 0


def test_update_check_up_to_date_and_errors(win, monkeypatch):
    from cycle_overlay import __version__
    from cycle_overlay.core.updates import UpdateError
    from cycle_overlay.gui import update_dialog as ud

    monkeypatch.setattr(win.settings, "save", lambda *a, **k: None)
    infos = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: infos.append(a[2]))
    mgr = ud.UpdateManager(win, fetch=lambda: _fake_release(__version__))
    mgr.check(manual=True)
    assert wait_for(lambda: infos, 10) and "aktuell" in infos[0]

    def boom():
        raise UpdateError("Keine Verbindung zu GitHub")

    mgr2 = ud.UpdateManager(win, fetch=boom)
    mgr2.check(manual=True)
    assert wait_for(lambda: win.errors, 10) and "Keine Verbindung" in win.errors[-1][1]
    # ausgeschaltet: keine automatische Prüfung
    win.settings.check_updates = False
    mgr2.auto_check()
    assert mgr2._check is None


def test_type_gallery_and_apply_to_all(loaded, monkeypatch):
    from cycle_overlay.rendering.typography import preset_of

    w = loaded
    w.c.select("heart_rate")
    QApplication.processEvents()
    ins = w.inspector
    assert "type" in ins.sections and "style" in ins.sections
    assert ins.type_buttons["classic"].isChecked()
    ins.type_buttons["poster"].click()
    assert wait_for(lambda: preset_of(w.c.model.widget("heart_rate").style) == "poster")
    assert wait_for(lambda: ins.type_buttons["poster"].isChecked())  # nach dem Neuaufbau markiert
    assert w.c.dirty
    # Feineinstellung macht daraus einen eigenen Stil
    ins._set_style("font_width", 0.8)
    assert preset_of(w.c.model.widget("heart_rate").style) is None
    ins._set_style("font_width", 9.0)  # ungültig -> abgelehnt, alter Wert bleibt
    assert w.c.model.widget("heart_rate").style.font_width == pytest.approx(0.8)
    # auf alle übertragen
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    ins._apply_all(("font_family", "font_width"), "die Schrift")
    others = [x for x in w.c.model.project.layout.widgets if x.id != "heart_rate"]
    assert others and all(x.style.font_family == "Anton" and x.style.font_width == pytest.approx(0.8) for x in others)


def test_font_combo_lists_bundled_fonts_first(loaded):
    from cycle_overlay.rendering.typography import BUNDLED_FAMILIES

    w = loaded
    w.c.select("heart_rate")
    QApplication.processEvents()
    seen = []
    box = w.inspector._font_combo("Inter", seen.append)
    assert [box.itemData(i) for i in range(len(BUNDLED_FAMILIES))] == list(BUNDLED_FAMILIES)
    box.setCurrentIndex(box.findData("Fira Sans"))
    assert seen[-1] == "Fira Sans"


def test_add_widget_dialog_with_templates(loaded):
    from cycle_overlay.gui.signal_panel import AddWidgetDialog

    w = loaded
    dlg = AddWidgetDialog(w.c.model.available_channels(), w)
    key, ch = dlg.result_values()
    assert key == "text" and ch == "speed"  # erste Vorlage, Vorschlag zuerst
    dlg.select_template("needle")
    assert dlg.title.text() == "Tacho mit Nadel"
    dlg.select_template("map_arrow")
    assert not dlg.channel_row.isVisibleTo(dlg)  # nur ein passendes Signal -> keine Auswahl nötig
    dlg.select_template("progress")
    assert dlg.channel.currentData() == "distance"
    n = len(w.c.model.project.layout.widgets)
    w.c.add_from_template("needle")
    cfg = w.c.model.widget(w.c.selected)
    assert len(w.c.model.project.layout.widgets) == n + 1
    assert cfg.type == "arc" and cfg.show_needle and cfg.needle_style == "pointer"
    # Inspector zeigt die neuen Optionen
    QApplication.processEvents()
    from cycle_overlay.gui.inspector_spec import visible_sections

    keys = {f.key for _s, fs in visible_sections(cfg) for f in fs}
    assert {"needle_style", "show_tick_labels"} <= keys
    dlg.deleteLater()


def test_add_widget_dialog_shows_preview(loaded):
    import numpy as np

    from cycle_overlay.gui.signal_panel import AddWidgetDialog
    from cycle_overlay.gui.widget_preview import PREVIEW_SIZE, preview_time, template_preview
    from cycle_overlay.rendering.qt_support import image_to_rgba
    from cycle_overlay.rendering.templates import TEMPLATES

    w = loaded
    m = w.c.model
    t = preview_time(m, 0.0)
    assert 0 < t < m.telemetry.duration
    n = len(m.project.layout.widgets)
    for key in TEMPLATES:
        img = template_preview(m, key, None, t)
        assert (img.width(), img.height()) == PREVIEW_SIZE
        a = image_to_rgba(img).astype(int)
        assert (np.abs(a[..., :3] - (58, 64, 74)).max(axis=2) > 40).sum() > 200, key  # es ist etwas zu sehen
    assert len(m.project.layout.widgets) == n  # Vorschau fügt nichts hinzu
    calls = []

    def prev(key, ch):
        calls.append((key, ch))
        return template_preview(m, key, ch, t)

    dlg = AddWidgetDialog(m.available_channels(), w, preview=prev)
    assert calls and not dlg.preview.pixmap().isNull()
    dlg.select_template("scope")
    assert calls[-1][0] == "scope"
    dlg.channel.setCurrentIndex(dlg.channel.findData("speed"))
    assert calls[-1] == ("scope", "speed")
    dlg.deleteLater()


def test_preview_quality_changes_video_resolution(loaded, clip, monkeypatch):
    w = loaded
    monkeypatch.setattr(w.settings, "save", lambda *a, **k: None)
    w.c.model.set_sync_options(timezone="Europe/Berlin")
    w.c.open_video(clip)
    assert wait_for(lambda: w.c.model.has_video)
    v = w.c.model.video
    w.opt_quality.setCurrentIndex(w.opt_quality.findData("full"))
    assert w.c.preview_quality == "full" and w.settings.preview_quality == "full"
    assert w.c.preview_frame_size() == (v.width - v.width % 2, v.height - v.height % 2)
    w.opt_quality.setCurrentIndex(w.opt_quality.findData("quarter"))
    assert w.c.preview_frame_size()[0] <= v.width // 4 + 1
    assert wait_for(lambda: w.c.background is not None and w.c.background.shape[1] == w.c.preview_frame_size()[0])
    assert "×" in w.quality_info.text()
    w.opt_quality.setCurrentIndex(w.opt_quality.findData("auto"))
    w.c.set_preview_target(400)
    assert w.c.preview_frame_size()[0] == min(640, v.width - v.width % 2)
