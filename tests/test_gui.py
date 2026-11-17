"""GUI 登录流程隔离测试：不抓包、不访问网络、不修改系统代理。"""
import threading
import time

import pytest

pytest.importorskip("tkinter")

import capture_token
import gui
import token_util


@pytest.fixture(scope="module")
def tk_root():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(str(exc))
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def app(monkeypatch, tmp_path, tk_root):
    root = tk_root
    monkeypatch.setattr(gui, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(token_util, "TOKEN_PATH", tmp_path / "session_cache.json")
    monkeypatch.setattr(gui.App, "check_certificate", lambda *a, **k: None)
    monkeypatch.setattr(capture_token, "find_mitmdump", lambda: "mock-mitmdump")
    window = gui.App(root)
    window.cfg["auth"] = {"student_id": "B21000001"}
    window.student_id.set("B21000001")
    window._cert_trusted = True
    # 明确驱动事件队列，避免自动查询启动额外线程。
    for timer in root.tk.call("after", "info"):
        root.after_cancel(timer)
    yield window
    for timer in root.tk.call("after", "info"):
        root.after_cancel(timer)
    for widget in root.winfo_children():
        widget.destroy()


def drain_capture(app):
    deadline = time.monotonic() + 3
    while app._events.empty() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not app._events.empty(), "后台获取没有结束"
    app._poll_events()


def test_missing_login_has_button_guidance(app):
    assert app.check_token()["state"] == "missing"
    app._load_worker("仙林")
    app._poll_events()
    assert "获取 / 刷新登录" in app.status.cget("text")
    assert not app._loading


def test_capture_success_refreshes_slots(app, monkeypatch):
    from test_token_util import future_token
    calls = []
    def capture(cfg, timeout, force, stop_event):
        calls.append((timeout, force))
        token_util.save_token(future_token())
        return 0
    monkeypatch.setattr(capture_token, "run_refresh", capture)
    monkeypatch.setattr(app, "refresh_slots", lambda: calls.append("refresh"))
    app.capture_token()
    app.capture_token()  # 重复点击不能启动第二个抓取。
    drain_capture(app)
    assert calls == [(300, True), "refresh"]
    assert not app._capturing
    assert "本地未过期" in app.token_label.cget("text")


@pytest.mark.parametrize("outcome", [1, "error", 130])
def test_capture_failure_and_cancel_restore_controls(app, monkeypatch, outcome):
    def capture(*args, **kwargs):
        if outcome == "error":
            raise RuntimeError("sensitive-token-must-not-appear")
        return outcome
    monkeypatch.setattr(capture_token, "run_refresh", capture)
    app.capture_token()
    drain_capture(app)
    assert not app._capturing
    assert str(app.capture_button.cget("state")) == "normal"
    assert str(app.cancel_button.cget("state")) == "disabled"
    assert "sensitive-token" not in app.status.cget("text")


def test_close_requests_cancel_before_destroy(app, monkeypatch):
    app._capturing = True
    destroyed = []
    monkeypatch.setattr(app.root, "destroy", lambda: destroyed.append(True))
    app.close()
    assert app._capture_stop.is_set()
    assert not destroyed
    app._events.put(("capture_done", 130))
    app._poll_events()
    assert destroyed == [True]


def test_refresh_keeps_edited_and_unavailable_targets(app):
    slot = ("1", "仙林1号", "18:00", "19:00", True, "2026-11-05")
    app._append_slot(slot)
    app._apply_slots([], "2026-11-05")
    assert app.selected == [slot[:4]]
    app._apply_slots([slot], "2026-11-05")
    assert app.selected == [slot[:4]]


def test_capture_cancel_restores_proxy(monkeypatch):
    event = threading.Event()
    restored = []
    monkeypatch.setattr(capture_token, "find_free_port", lambda p: p)
    monkeypatch.setattr(capture_token, "_read_proxy_state", lambda: (0, ""))
    monkeypatch.setattr(token_util, "current_best_exp", lambda: 0)
    monkeypatch.setattr(capture_token, "spawn_mitmdump", lambda *a: object())
    monkeypatch.setattr(capture_token, "wait_for_proxy_ready", lambda *a: True)
    monkeypatch.setattr(capture_token, "set_system_proxy", lambda *a: event.set())
    monkeypatch.setattr(capture_token, "_stop_mitmdump", lambda p: restored.append("stopped"))
    monkeypatch.setattr(capture_token, "_restore_proxy_state", lambda s: restored.append(s))
    assert capture_token.run_refresh({}, 300, force=True, stop_event=event) == 130
    assert restored == ["stopped", (0, "")]


def test_first_use_saves_account_without_booking_targets(app, tmp_path):
    import yaml
    app.cfg = {"token_capture": {"mode": "phone"}}
    app.student_id.set("B21000001")
    assert app.save_student_id()
    saved = yaml.safe_load(gui.CONFIG_PATH.read_text(encoding="utf-8"))
    assert saved["auth"]["student_id"] == "B21000001"
    assert saved["booking"]["targets"] == []
    assert saved["token_capture"]["mode"] == "phone"


@pytest.mark.parametrize("sid", ["", 'abc"\nother: 1', "学号"])
def test_invalid_student_id_is_not_saved(app, sid):
    app.student_id.set(sid)
    assert not app.save_student_id()
    assert not gui.CONFIG_PATH.exists()


def test_capture_requires_saved_account_and_trusted_certificate(app, monkeypatch):
    calls = []
    monkeypatch.setattr(app, "_capture_worker", lambda: calls.append(True))
    app.student_id.set("B99999999")
    app.capture_token()
    assert "保存学号" in app.status.cget("text")
    app.student_id.set("B21000001")
    app._cert_trusted = False
    app.capture_token()
    assert "证书" in app.status.cget("text")
    assert not calls


def test_certificate_install_requires_user_confirmation(app, monkeypatch):
    app._cert_info = dict(subject="CN=test", issuer="CN=test", not_before="2026",
                          not_after="2027", sha256="A" * 64, path="test.pem")
    calls = []
    monkeypatch.setattr(app, "_start_certificate_job", lambda *a: calls.append(a))
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: False)
    app.review_certificate()
    assert not calls
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: True)
    app.review_certificate()
    assert calls == [("install", "A" * 64)]


def test_proxy_not_enabled_if_worker_start_fails(monkeypatch):
    restored = []
    monkeypatch.setattr(capture_token, "find_free_port", lambda p: p)
    monkeypatch.setattr(capture_token, "spawn_mitmdump", lambda *a: object())
    def fail(*args):
        raise RuntimeError("worker exited")
    monkeypatch.setattr(capture_token, "wait_for_proxy_ready", fail)
    monkeypatch.setattr(capture_token, "set_system_proxy", lambda *a: pytest.fail("proxy enabled too early"))
    monkeypatch.setattr(capture_token, "_stop_mitmdump", lambda p: restored.append("stopped"))
    monkeypatch.setattr(capture_token, "_restore_proxy_state", lambda s: restored.append("restored"))
    with pytest.raises(RuntimeError):
        capture_token.run_refresh({}, 300, force=True)
    assert restored == ["stopped", "restored"]


def test_forced_capture_accepts_observed_same_token(monkeypatch):
    from test_token_util import future_token
    import token_capture_addon as addon
    monkeypatch.setenv("NJUPT_STUDENT_ID", "B21000001")
    monkeypatch.setenv("NJUPT_FORCE_TOKEN", "1")
    monkeypatch.setattr(addon, "_last_written", [None])
    token = future_token()
    token_util.save_token(token)
    stamp = token_util.TOKEN_PATH.stat().st_mtime_ns
    time.sleep(0.02)
    addon._consider(token, lambda msg: None)
    assert capture_token.wait_for_token(token_util.jwt_exp(token), 0.2,
               initial_mtime_ns=stamp, expected_student_id="B21000001") == token


def test_switch_account_accepts_matching_token_with_shorter_lifetime(monkeypatch):
    from test_token_util import future_token
    import token_capture_addon as addon
    monkeypatch.setenv("NJUPT_STUDENT_ID", "B21000001")
    monkeypatch.setattr(addon, "_last_written", [None])
    old = future_token("B99999999", lifetime=14400)
    new = future_token("B21000001", lifetime=7200)
    token_util.save_token(old)
    stamp = token_util.TOKEN_PATH.stat().st_mtime_ns
    time.sleep(0.02)
    addon._consider(new, lambda msg: None)
    assert token_util.read_cached_token() == new
    assert capture_token.wait_for_token(token_util.jwt_exp(old), 0.2,
               initial_mtime_ns=stamp, expected_student_id="B21000001") == new


@pytest.mark.skipif(capture_token.os.name != "nt", reason="Windows capture session")
def test_second_capture_session_is_rejected_without_leaking_handle():
    with capture_token.capture_session():
        with pytest.raises(capture_token.CaptureError):
            with capture_token.capture_session():
                pytest.fail("concurrent capture allowed")
    with capture_token.capture_session():
        pass
