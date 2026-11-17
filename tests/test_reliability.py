import base64
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import requests

import book
import capture_token as capture
import configure
import runtime
import scheduling
import token_util


def jwt(exp=None, student="B1"):
    body = {"exp": exp or time.time() + 3600, "studentId": student}
    return "e30." + base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=") + ".sig"


def cfg(**options):
    return {"booking": {"targets": [{"court_name": "仙林1号", "time": "18:00-19:00"}], **options}}


def candidates():
    return [("1", "仙林1号", "18:00", "19:00", "2026-10-01"),
            ("2", "仙林2号", "18:00", "19:00", "2026-10-01")]


@pytest.mark.parametrize("status,body,kind", [
    (401, {}, "auth"), (403, {}, "auth"), (429, {}, "rate_limit"),
    (500, {}, "unknown"), (302, {}, "unknown"), (200, [], "unknown"),
    (200, {"data": {}}, "unknown"),
])
def test_http_outcomes(monkeypatch, status, body, kind):
    response = SimpleNamespace(status_code=status, headers={"Retry-After": "4"}, json=lambda: body)
    monkeypatch.setattr(book.SESSION, "request", lambda *a, **k: response)
    with pytest.raises(book.ApiError) as exc:
        book.api_post("/test", "not-a-secret", {})
    assert exc.value.kind == kind
    if kind == "rate_limit":
        assert exc.value.retry_after == 4


@pytest.mark.parametrize("method,kind", [("GET", "network"), ("POST", "unknown")])
def test_network_failure_is_ambiguous_only_for_post(monkeypatch, method, kind):
    def fail(*a, **k):
        raise requests.Timeout()
    monkeypatch.setattr(book.SESSION, "request", fail)
    with pytest.raises(book.ApiError) as exc:
        book._request(method, "/test", "not-a-secret")
    assert exc.value.kind == kind


def test_non_json_submission_is_unknown(monkeypatch):
    def malformed():
        raise ValueError("not json")
    monkeypatch.setattr(book.SESSION, "request", lambda *a, **k: SimpleNamespace(status_code=200, headers={}, json=malformed))
    with pytest.raises(book.ApiError, match="格式") as exc:
        book.api_post("/test", "not-a-secret", {})
    assert exc.value.kind == "unknown"


@pytest.mark.parametrize("response,state", [
    ({"success": False, "errCode": 5004}, "auth"),
    ({"success": False, "errMsg": "未知业务错误"}, "rejected"),
    ({"success": False, "errMsg": "存在未支付订单"}, "existing_order"),
])
def test_terminal_errors_stop_after_one_attempt(monkeypatch, response, state):
    calls = []
    def submit(*args):
        calls.append(1)
        return False, response, 1
    monkeypatch.setattr(book, "book", submit)
    assert book.run_attempts(jwt(), candidates(), "B1", cfg()) == 1
    assert len(calls) == 1
    assert runtime.read_json(runtime.DATA_DIR / "booking_status.json")["state"] == state


def test_timeout_guard_blocks_restart_until_acknowledged(monkeypatch):
    def timeout(*args):
        raise book.ApiError("unknown", "timeout")
    monkeypatch.setattr(book, "book", timeout)
    assert book.run_attempts(jwt(), candidates(), "B1", cfg()) == 2
    assert runtime.read_json(book.guard_path())["state"] == "unknown"
    monkeypatch.setattr(book, "load_config", lambda: cfg())
    monkeypatch.setattr(book, "load_token", lambda: pytest.fail("暂停期间不应访问登录"))
    assert book._main({"--now"}) == 2
    book.acknowledge_order()
    assert not book.guard_path().exists()


def test_full_moves_to_next_and_saves_order_without_token(monkeypatch):
    calls = []
    def submit(token, sid, *args):
        calls.append(sid)
        return sid == "2", {"success": sid == "2", "errMsg": "已满", "data": {"order": {"orderId": "order-1"}}}, 12
    monkeypatch.setattr(book, "book", submit)
    monkeypatch.setattr(runtime, "pause", lambda *a: None)
    token = jwt()
    assert book.run_attempts(token, candidates(), "B1", cfg()) == 0
    assert calls == ["1", "2"]
    assert runtime.read_json(runtime.DATA_DIR / "last_order.json")["order_id"] == "order-1"
    assert runtime.read_json(book.guard_path())["state"] == "success"
    for path in runtime.DATA_DIR.rglob("*.json*"):
        assert token not in path.read_text(encoding="utf-8")


def test_retry_after_respected(monkeypatch):
    responses = iter([book.ApiError("rate_limit", "slow", 7), (True, {"data": {}}, 1)])
    delays = []
    def submit(*args):
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return item
    monkeypatch.setattr(book, "book", submit)
    monkeypatch.setattr(runtime, "pause", lambda value: delays.append(value))
    assert book.run_attempts(jwt(), candidates(), "B1", cfg()) == 0
    assert delays == [7]


def test_attempt_limit(monkeypatch):
    calls = []
    monkeypatch.setattr(book, "book", lambda *a: (calls.append(1) or False, {"errMsg": "未开放"}, 1))
    monkeypatch.setattr(runtime, "pause", lambda *a: None)
    assert book.run_attempts(jwt(), candidates(), "B1", cfg(max_attempts=2)) == 1
    assert len(calls) == 2


def test_cancel_no_submission(monkeypatch):
    runtime.request_stop("booking")
    monkeypatch.setattr(book, "book", lambda *a: pytest.fail("停止后不应提交"))
    with pytest.raises(runtime.Cancelled):
        book.run_attempts(jwt(), candidates(), "B1", cfg())


def test_lifetime_covers_whole_window_and_account():
    assert book.token_covers(jwt(), time.time() + 600, "B1")
    assert not book.token_covers(jwt(time.time() + 500), time.time() + 600, "B1")
    assert not book.token_covers(jwt(student="B2"), time.time() + 600, "B1")


def test_final_wait_uses_monotonic_clock(monkeypatch):
    wall_calls = []
    ticks = [0.0]
    now = datetime.now()
    wall = now.replace(second=0, microsecond=0).timestamp() - 1
    monkeypatch.setattr(book.time, "time", lambda: wall_calls.append(1) or wall)
    monkeypatch.setattr(book.time, "monotonic", lambda: ticks[0])
    monkeypatch.setattr(book.time, "sleep", lambda duration: ticks.__setitem__(0, ticks[0] + duration))
    book.wait_until_server(now.hour, now.minute, 0, on_near=lambda: pytest.fail("最终等待不应执行网络回调"))
    assert len(wall_calls) == 1
    assert ticks[0] == pytest.approx(1)


def test_clock_sync_midpoint_formula(monkeypatch):
    monkeypatch.setattr(book.time, "time", lambda: 100.0)
    ticks = iter([0, 0, 0, 0.2])
    monkeypatch.setattr(book.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(book.SESSION, "get", lambda *a, **k: SimpleNamespace(status_code=200, headers={"Date": "Thu, 01 Jan 1970 00:01:40 GMT"}))
    assert book.get_server_offset("fake", samples=1) == pytest.approx(0.4)


def test_task_times_and_skip(tmp_path):
    b = cfg(schedule_time="14:00", weekdays=[1, 3, 5])["booking"]
    assert scheduling.task_plan({"booking": b}) == {"weekdays": [1, 3, 5], "booking_time": "13:55", "refresh_time": "13:30"}
    assert "不是" in scheduling.skip_reason(b, datetime(2026, 10, 1, 13))
    assert "错过" in scheduling.skip_reason(b, datetime(2026, 10, 2, 15))
    assert not scheduling.skip_reason(b, datetime(2026, 10, 2, 15), immediate=True)
    scheduling.set_skip_today()
    assert "跳过" in scheduling.skip_reason({}, immediate=True)
    scheduling.set_skip_today(False)
    assert not scheduling.skip_reason({}, immediate=True)


def test_config_preserves_retry_and_notify():
    original = {**cfg(max_attempts=10, weekdays=[2, 4]), "notify": {"enable_sound": False}}
    import yaml
    result = yaml.safe_load(configure.render_config(original))
    assert result["booking"]["max_attempts"] == 10
    assert result["booking"]["weekdays"] == [2, 4]
    assert result["notify"] == original["notify"]


def test_os_lock_cross_process_and_release():
    code = "from pathlib import Path; import sys,runtime; runtime.DATA_DIR=Path(sys.argv[1]); print(runtime.is_running('booking'))"
    with runtime.single_instance("booking"):
        output = subprocess.check_output([sys.executable, "-c", code, str(runtime.DATA_DIR)], text=True)
        assert output.strip() == "True"
    assert not runtime.is_running("booking")


def test_proxy_readiness_before_switch_and_restore(monkeypatch):
    calls = []
    state = [(0, "original:1")]
    monkeypatch.setattr(capture, "_read_proxy_state", lambda: state[0])
    monkeypatch.setattr(capture, "spawn_mitmdump", lambda *a: calls.append("spawn") or object())
    monkeypatch.setattr(capture, "wait_proxy_ready", lambda *a: calls.append("ready"))
    def switch(enabled, port):
        assert capture.recovery_path().exists()
        calls.append("switch")
        state[0] = (1, f"127.0.0.1:{port}")
    def restore(previous):
        calls.append("restore")
        state[0] = previous
    monkeypatch.setattr(capture, "set_system_proxy", switch)
    monkeypatch.setattr(capture, "_restore_proxy_state", restore)
    monkeypatch.setattr(capture, "_stop_mitmdump", lambda *a: calls.append("stop"))
    with pytest.raises(ValueError):
        with capture.proxy_session(8080, {}):
            raise ValueError("simulated failure")
    assert calls == ["spawn", "ready", "switch", "restore", "stop"]
    assert state[0] == (0, "original:1")
    assert not capture.recovery_path().exists()


def test_proxy_startup_failure_never_changes_system(monkeypatch):
    monkeypatch.setattr(capture, "_read_proxy_state", lambda: (0, ""))
    monkeypatch.setattr(capture, "spawn_mitmdump", lambda *a: object())
    def fail(*args):
        raise RuntimeError("not ready")
    monkeypatch.setattr(capture, "wait_proxy_ready", fail)
    monkeypatch.setattr(capture, "_stop_mitmdump", lambda *a: None)
    with pytest.raises(RuntimeError, match="not ready"):
        with capture.proxy_session(8080, {}):
            pytest.fail("代理未就绪不应继续")
    assert not capture.recovery_path().exists()


@pytest.mark.parametrize("changed", [False, True])
def test_recover_crash_respects_other_proxy_changes(monkeypatch, changed):
    token_util.atomic_write_json(capture.recovery_path(), {"previous": [0, "old"], "managed_proxy": "127.0.0.1:8080"})
    monkeypatch.setattr(capture, "_read_proxy_state", lambda: (1, "other:9" if changed else "127.0.0.1:8080"))
    restored = []
    monkeypatch.setattr(capture, "_restore_proxy_state", lambda state: restored.append(state))
    assert capture.recover_proxy()
    assert restored == ([] if changed else [(0, "old")])


def test_failed_proxy_restore_keeps_recovery_record(monkeypatch):
    token_util.atomic_write_json(capture.recovery_path(), {"previous": [0, "old"], "managed_proxy": "127.0.0.1:8080"})
    monkeypatch.setattr(capture, "_read_proxy_state", lambda: (1, "127.0.0.1:8080"))
    def fail(*args):
        raise OSError("registry unavailable")
    monkeypatch.setattr(capture, "_restore_proxy_state", fail)
    with pytest.raises(OSError):
        capture.recover_proxy()
    assert capture.recovery_path().exists()


def test_cached_token_must_cover_booking_end_before_network(monkeypatch):
    config = cfg()
    config["token_capture"] = {"enabled": False}
    monkeypatch.setattr(book, "load_config", lambda: config)
    monkeypatch.setattr(book, "load_token", lambda: jwt(time.time() + 130))
    monkeypatch.setattr(book, "pre_fetch_type_id", lambda *a: pytest.fail("有效期不足应在网络查询前停止"))
    assert book._main({"--now"}) == 1


def test_server_revoked_token_blocks_get(monkeypatch):
    monkeypatch.setattr(book.SESSION, "request", lambda *a, **k: SimpleNamespace(
        status_code=200, headers={}, json=lambda: {"success": False, "errCode": 5004, "data": None}))
    with pytest.raises(book.ApiError) as exc:
        book.verify_login(jwt())
    assert exc.value.kind == "auth"


def test_force_refresh_accepts_changed_token_with_equal_expiry(monkeypatch, tmp_path):
    import token_capture_addon as addon
    monkeypatch.setattr(token_util, "TOKEN_PATH", tmp_path / "token.json")
    monkeypatch.setenv("NJUPT_STUDENT_ID", "B1")
    monkeypatch.setenv("NJUPT_FORCE_TOKEN", "1")
    monkeypatch.setattr(addon, "_last_written", [None])
    old = jwt()
    new = old.rsplit(".", 1)[0] + ".new-signature"
    token_util.save_token(old)
    addon._consider(new)
    assert token_util.read_cached_token() == new
    assert capture.wait_for_token(token_util.jwt_exp(old), 1, time.time() + 60, old) == new


def test_switch_account_can_replace_longer_lived_wrong_account(monkeypatch, tmp_path):
    import token_capture_addon as addon
    monkeypatch.setattr(token_util, "TOKEN_PATH", tmp_path / "token.json")
    monkeypatch.setenv("NJUPT_STUDENT_ID", "B1")
    monkeypatch.setattr(addon, "_last_written", [None])
    old, new = jwt(time.time() + 7200, "B2"), jwt(student="B1")
    token_util.save_token(old)
    addon._consider(new)
    assert token_util.read_cached_token() == new


def test_clock_sync_skips_new_samples_after_budget(monkeypatch):
    ticks = [0.0]
    calls = []
    monkeypatch.setattr(book.time, "monotonic", lambda: ticks[0])
    def timeout(*args, **kwargs):
        calls.append(kwargs["timeout"])
        ticks[0] += 2
        raise requests.Timeout()
    monkeypatch.setattr(book.SESSION, "get", timeout)
    assert book.get_server_offset("fake", samples=5, budget_seconds=2) == 0
    assert calls == [(1.0, 1.0)]
