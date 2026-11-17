"""首次配置应将输入的学号写入从仓库模板生成的配置。"""
import importlib.util
from pathlib import Path

import yaml


def test_first_use_saves_student_id_from_template(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("booking_setup", root / "setup.py")
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    config_path = tmp_path / "config" / "config.yaml"
    monkeypatch.setattr(setup, "CONFIG_PATH", config_path)
    monkeypatch.setattr(setup, "ask", lambda *args: "B21000001")

    assert setup.ensure_config()
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["auth"]["student_id"] == "B21000001"
    assert saved["booking"]["targets"] == []
    assert saved["token_capture"]["enabled"] is True
