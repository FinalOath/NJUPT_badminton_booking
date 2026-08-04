"""token_capture_addon 的核心判断逻辑单测。

addon 顶层不 import mitmproxy，因此这些纯逻辑测试可在 .venv 直接跑；
完整的 mitmproxy 流量测试需在装有 mitmproxy 的环境运行（此处不做依赖）。
"""

import base64
import json
import time

import pytest

import token_capture_addon as addon
import token_util as tu


def make_jwt(payload):
    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return f"{b64({'alg': 'HS256'})}.{b64(payload)}.sig"


def future_token(sid="B21000001", lifetime=7200):
    return make_jwt({
        "userInfo": json.dumps({"studentId": sid, "role": ["POSTGRADUATE"]}),
        "exp": int(time.time()) + lifetime,
    })


def expired_token(sid="B21000001"):
    return make_jwt({
        "userInfo": json.dumps({"studentId": sid}),
        "exp": int(time.time()) - 100,
    })


@pytest.fixture(autouse=True)
def reset_state():
    addon._last_written[0] = None
    yield
    addon._last_written[0] = None


class TestScore:
    def test_accepts_fresh_matching(self, monkeypatch):
        monkeypatch.delenv("NJUPT_STUDENT_ID", raising=False)
        assert addon._score(future_token()) is not None

    def test_rejects_expired(self, monkeypatch):
        monkeypatch.delenv("NJUPT_STUDENT_ID", raising=False)
        assert addon._score(expired_token()) is None

    def test_rejects_wrong_account(self, monkeypatch):
        monkeypatch.setenv("NJUPT_STUDENT_ID", "9999999999")
        assert addon._score(future_token(sid="B21000001")) is None

    def test_rejects_non_jwt(self):
        assert addon._score("hello world") is None


class TestJwtsInBody:
    def test_finds_nested_token(self):
        t = future_token()
        body = {"data": {"token": t, "list": [{"x": "not-a-jwt"}]}}
        assert list(addon._jwts_in_body(body)) == [t]

    def test_ignores_garbage_strings(self):
        assert list(addon._jwts_in_body({"a": "hello", "b": [1, 2, None]})) == []


class FakeRequest:
    def __init__(self, host, path, headers=None):
        self.pretty_host = host
        self.path = path
        self.headers = headers or {}


class FakeResponse:
    def __init__(self, text):
        self._text = text

    def get_text(self):
        return self._text


class FakeFlow:
    def __init__(self, request, response=None):
        self.request = request
        self.response = response


@pytest.fixture
def live_capture(tmp_path, monkeypatch):
    """把 addon 的保存目标指向临时文件，驱动真实钩子方法。"""
    monkeypatch.setattr(tu, "TOKEN_PATH", tmp_path / "session_cache.json")
    monkeypatch.setattr(addon, "env_student_id", lambda: "B21000001")
    monkeypatch.setattr(addon, "current_best_exp", tu.current_best_exp)
    monkeypatch.setattr(addon, "save_token", tu.save_token)
    return addon.TokenCapture()


class TestHooks:
    def test_token_header_source(self, live_capture):
        t = future_token()
        flow = FakeFlow(FakeRequest("wechat.njupt.edu.cn", "/venue/user/types", {"token": t}))
        live_capture.request(flow)
        assert tu.read_cached_token() == t

    def test_login_response_source(self, live_capture):
        t = future_token()
        body = json.dumps({"success": True, "data": {"token": t}})
        flow = FakeFlow(FakeRequest("wechat.njupt.edu.cn", "/login/wxLogin"), FakeResponse(body))
        live_capture.response(flow)
        assert tu.read_cached_token() == t

    def test_authorization_bearer_source(self, live_capture):
        t = future_token()
        flow = FakeFlow(FakeRequest("wechat.njupt.edu.cn", "/venue/user/types",
                                    {"Authorization": f"Bearer {t}"}))
        live_capture.request(flow)
        assert tu.read_cached_token() == t

    def test_ignores_other_hosts(self, live_capture):
        t = future_token()
        flow = FakeFlow(FakeRequest("other.edu.cn", "/login/wxLogin", {"token": t}))
        live_capture.request(flow)
        assert tu.read_cached_token() == ""

    def test_ignores_non_login_other_flow(self, live_capture):
        # 非 /login 路径、无 token 头的请求不应触发
        flow = FakeFlow(FakeRequest("wechat.njupt.edu.cn", "/venue/user/types", {}),
                        FakeResponse(json.dumps({"data": [1, 2, 3]})))
        live_capture.response(flow)
        assert tu.read_cached_token() == ""


class TestConsider:
    def test_saves_and_never_regresses(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tu, "TOKEN_PATH", tmp_path / "session_cache.json")
        monkeypatch.setattr(addon, "env_student_id", lambda: "B21000001")
        monkeypatch.setattr(addon, "current_best_exp", tu.current_best_exp)
        monkeypatch.setattr(addon, "save_token", tu.save_token)

        t_a = future_token(lifetime=3600)   # +1h
        t_b = future_token(lifetime=7200)   # +2h
        t_c = future_token(lifetime=5400)   # +1.5h

        addon._consider(t_a)
        assert tu.read_cached_token() == t_a

        addon._consider(t_c)                # 比 t_a 新 → 保存
        assert tu.read_cached_token() == t_c

        addon._consider(t_b)                # 比 t_c 新 → 保存
        assert tu.read_cached_token() == t_b

        addon._consider(t_c)                # 比 t_b 旧 → 不覆盖
        assert tu.read_cached_token() == t_b

        addon._consider(t_b)                # 同一 token → 去重
        assert tu.read_cached_token() == t_b

    def test_expired_candidate_not_saved(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tu, "TOKEN_PATH", tmp_path / "session_cache.json")
        monkeypatch.setattr(addon, "env_student_id", lambda: "B21000001")
        monkeypatch.setattr(addon, "current_best_exp", tu.current_best_exp)
        monkeypatch.setattr(addon, "save_token", tu.save_token)

        addon._consider(expired_token())
        assert tu.read_cached_token() == ""
