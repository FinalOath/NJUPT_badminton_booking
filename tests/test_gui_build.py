"""Portable launchers must survive CMD and preserve the package boundary."""
import importlib.util
from pathlib import Path


def test_launcher_bytes_use_ascii_and_crlf(tmp_path):
    spec = importlib.util.spec_from_file_location("build_gui", Path(__file__).resolve().parents[1] / "scripts/build_gui.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path / "launch.bat"
    module.write_bat(path, '@echo off\nstart "" "%~dp0python\\pythonw.exe" "%~dp0app\\gui.py"\n')
    data = path.read_bytes()
    assert data.decode("ascii")
    assert data.count(b"\n") == data.count(b"\r\n")
    assert "gui.py" in module.APP_FILES
    assert "certificate_manager.py" in module.APP_FILES
    assert all("config.yaml" not in p and "session_cache" not in p for p in module.APP_FILES)
