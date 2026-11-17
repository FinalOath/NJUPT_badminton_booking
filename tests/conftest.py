"""所有测试使用临时状态目录；禁止真实网络、代理修改和桌面通知。"""
import pytest
import requests
import runtime
import capture_token
import token_util


@pytest.fixture(scope="session")
def tk_root():
    tkinter = pytest.importorskip("tkinter")
    try:
        root = tkinter.Tk()
    except tkinter.TclError as exc:
        pytest.skip(f"当前测试环境无法创建窗口: {exc}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture(autouse=True)
def isolate_side_effects(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(token_util, "TOKEN_PATH", tmp_path / "data" / "session_cache.json")
    monkeypatch.setattr(token_util, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(capture_token, "_read_proxy_state", lambda: (0, ""))
    monkeypatch.setenv("NJUPT_FORCE_TOKEN", "0")
    monkeypatch.setattr(runtime, "notify", lambda *a, **k: None)
    def blocked(*args, **kwargs):
        raise AssertionError("真实网络/代理操作被测试隔离层禁止")
    monkeypatch.setattr(requests.Session, "request", blocked)
    monkeypatch.setattr(capture_token, "spawn_mitmdump", blocked)
    monkeypatch.setattr(capture_token, "set_system_proxy", blocked)
    monkeypatch.setattr(capture_token, "_restore_proxy_state", blocked)
