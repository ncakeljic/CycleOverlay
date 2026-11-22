"""Windows-Anwendungssteuerung (Smart App Control, WDAC): WinError 4551 verständlich melden und ausweichen."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cycle_overlay import platform_utils as pu
from cycle_overlay.export import ffmpeg_exporter as fe
from cycle_overlay.export import ffmpeg_locator as fl
from cycle_overlay.export.profiles import PROFILES


class _WinBlocked(OSError):
    def __init__(self):
        super().__init__(13, "Eine Anwendungssteuerungsrichtlinie hat diese Datei blockiert")
        self.winerror = 4551


def _raise_blocked(*_a, **_k):
    raise _WinBlocked()


def test_run_and_popen_translate_blocked(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _raise_blocked)
    monkeypatch.setattr(subprocess, "Popen", _raise_blocked)
    with pytest.raises(pu.ExecutableBlocked) as ei:
        pu.run([r"C:\Programme\CycleOverlay\bin\ffprobe.exe", "-version"])
    assert "ffprobe.exe" in str(ei.value) and "blockiert" in str(ei.value)
    assert isinstance(ei.value, OSError)
    with pytest.raises(pu.ExecutableBlocked):
        pu.popen(["ffmpeg.exe"])
    with pytest.raises(pu.ExecutableBlocked):
        pu.run_bytes(["ffmpeg.exe"])


def test_other_oserrors_pass_through(monkeypatch):
    def nope(*_a, **_k):
        raise FileNotFoundError(2, "fehlt")

    monkeypatch.setattr(subprocess, "run", nope)
    with pytest.raises(FileNotFoundError):
        pu.run(["gibtsnicht"])
    assert not pu.is_blocked_error(FileNotFoundError())
    assert pu.is_blocked_error(_WinBlocked())


def test_writer_reports_blocked_ffmpeg_as_export_error(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _raise_blocked)
    with pytest.raises(fe.ExportError, match="blockiert"):
        fe.FFmpegWriter(["ffmpeg.exe", "-version"], 4).start()


def test_verify_skipped_when_ffprobe_blocked(monkeypatch, tmp_path):
    out = tmp_path / "x.mov"
    out.write_bytes(b"0")
    monkeypatch.setattr(fe, "run", lambda *a, **k: _raise_blocked())
    assert fe.verify_output(out, PROFILES["prores4444"], 1, ffprobe="ffprobe.exe") == (None, None)


def test_locator_skips_blocked_candidate(monkeypatch, tmp_path):
    bundled = tmp_path / "bundled"
    system = tmp_path / "system"
    for d in (bundled, system):
        d.mkdir()
        exe = d / "ffmpeg"
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
    monkeypatch.setattr(fl, "IS_WINDOWS", True)
    monkeypatch.setattr(fl, "executable_name", lambda n: n)
    monkeypatch.setattr(fl, "bundled_bin_dir", lambda: bundled)
    monkeypatch.setattr(fl.shutil, "which", lambda _n: str(system / "ffmpeg"))
    monkeypatch.setattr(fl, "_common_dirs", lambda: [])
    monkeypatch.setattr(fl, "_LAUNCHABLE", {})
    monkeypatch.setattr(fl, "BLOCKED", set())
    monkeypatch.setattr(fl, "_CONFIGURED", {"ffmpeg": None, "ffprobe": None})

    def fake_run(cmd, timeout=None):
        if Path(str(cmd[0])).parent == bundled:
            raise _WinBlocked()
        return subprocess.CompletedProcess(cmd, 0, "ffmpeg version 7", "")

    monkeypatch.setattr(fl, "run", fake_run)
    assert fl.find_executable("ffmpeg") == system / "ffmpeg"
    assert bundled / "ffmpeg" in fl.BLOCKED

    # nichts Startbares mehr -> klare Meldung statt „nicht gefunden“
    monkeypatch.setattr(fl.shutil, "which", lambda _n: None)
    assert fl.find_executable("ffmpeg") is None
    st = fl.locate()
    assert any("blockiert" in e for e in st.errors)


def test_configured_path_is_used_everywhere(monkeypatch, tmp_path):
    exe = tmp_path / "meinffmpeg"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setattr(fl, "_CONFIGURED", {"ffmpeg": None, "ffprobe": None})
    fl.configure(str(exe), None)
    assert fl.find_executable("ffmpeg") == exe
    fl.configure(None, None)
    assert fl.find_executable("ffmpeg") != exe
