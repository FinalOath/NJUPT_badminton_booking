"""token_util 的单元测试（纯 stdlib，可在 .venv 运行）。"""

import base64
import json
import time

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


class TestParse:
    def test_normalize_prefix_quotes(self):
        assert tu.normalize_token("  token: eyJ.abc.def  ") == "eyJ.abc.def"
        assert tu.normalize_token("token=eyJ.abc.def") == "eyJ.abc.def"
        assert tu.normalize_token('"eyJ.abc.def"') == "eyJ.abc.def"
        assert tu.normalize_token("") == ""
        assert tu.normalize_token(None) == ""

    def test_jwt_decode_exp_student(self):
        t = future_token()
        assert tu.jwt_exp(t) is not None
        assert tu.token_student_id(t) == "B21000001"
        assert tu.jwt_decode("garbage") is None
        assert tu.jwt_exp("garbage") is None
        assert tu.token_student_id("garbage") is None

    def test_is_valid(self):
        assert tu.token_is_valid(future_token())
        assert not tu.token_is_valid(expired_token())
        assert not tu.token_is_valid("garbage")

    def test_extract_malformed_text(self):
        t = future_token()
        txt = '{\n  "token": token: ' + t + '\n}'
        assert tu.normalize_token(tu._extract_token_from_text(txt)) == t


class TestCache:
    def test_status_distinguishes_missing_expired_expiring_and_account(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tu, "TOKEN_PATH", tmp_path / "session_cache.json")
        assert tu.cached_token_status()["state"] == "missing"
        tu.save_token(make_jwt({"exp": 2000, "studentId": "B1"}))
        assert tu.cached_token_status(now=2000)["state"] == "expired"
        assert tu.cached_token_status(now=1900)["state"] == "expiring"
        info = tu.cached_token_status("B1", now=1000)
        assert info["state"] == "valid"
        assert info["remaining_seconds"] == 1000
        assert "token" not in info
        assert tu.cached_token_status("B2", now=1000)["state"] == "wrong_account"

    def test_status_invalid_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tu, "TOKEN_PATH", tmp_path / "session_cache.json")
        for raw in ('{}', '[]', 'null', 'broken', '{"token": 12}'):
            tu.TOKEN_PATH.write_text(raw, encoding="utf-8")
            assert tu.cached_token_status()["state"] == "invalid"
        for exp in (True, float("inf"), float("nan")):
            assert tu.jwt_exp(make_jwt({"exp": exp})) is None

    def test_save_read_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tu, "TOKEN_PATH", tmp_path / "session_cache.json")
        t = future_token()
        assert tu.save_token("token: " + t) == t
        assert tu.read_cached_token() == t
        assert tu.current_best_exp() == tu.jwt_exp(t)
        data = json.loads((tmp_path / "session_cache.json").read_text(encoding="utf-8"))
        assert data["token"] == t
        assert data["expires_at"]

    def test_expired_cache_reads_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tu, "TOKEN_PATH", tmp_path / "session_cache.json")
        tu.save_token(expired_token())
        assert tu.read_cached_token() == ""
        assert tu.current_best_exp() == 0

    def test_missing_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tu, "TOKEN_PATH", tmp_path / "nope.json")
        assert tu.read_cached_token() == ""
        assert tu.current_best_exp() == 0

    def test_save_rejects_garbage(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tu, "TOKEN_PATH", tmp_path / "session_cache.json")
        assert tu.save_token("not-a-jwt") == ""
