"""Kommandozeilen-Befehle, die nicht in anderen Testdateien stecken."""

import pytest



def test_selftest_command(tmp_path):
    pytest.importorskip("PySide6")
    from cycle_overlay.main import main

    out = tmp_path / "st.txt"
    code = main(["selftest", "--out", str(out)])
    text = out.read_text(encoding="utf-8")
    assert "OK    Qt" in text and "OK    Zeichnen" in text and "OK    Presets" in text
    assert code in (0, 1)  # 1 nur, wenn ffmpeg fehlt
