"""预约范围回归：所有网络、等待、登录抓取均隔离，绝不发送预约请求。"""
import sys
from datetime import datetime

import pytest

import book
import runtime


DATE = datetime.now().strftime("%Y-%m-%d")


@pytest.fixture(autouse=True)
def valid_login(monkeypatch):
    monkeypatch.setattr(book.token_util, "jwt_exp", lambda token: datetime.now().timestamp() + 86400)
    monkeypatch.setattr(book, "verify_login", lambda token: None)
    monkeypatch.setattr(runtime, "pause", lambda *args: None)


def target(name="仙林1号", period="18:00-19:00"):
    return {"court_name": name, "time": period}


def slots():
    return [
        ("1", "仙林1号", "18:00", "19:00", True, DATE),
        ("2", "仙林2号", "18:00", "19:00", True, DATE),
        ("3", "仙林1号", "15:00", "16:00", True, DATE),
        ("4", "三牌楼1号", "18:00", "19:00", True, DATE),
        ("5", "仙林3号", "18:00", "19:00", False, DATE),
        ("6", "仙林1号", "18:00", "19:00", True, "2000-01-01"),
    ]


def config(mode="strict"):
    return {"location": "仙林", "targets": [target()], "fallback_mode": mode}


@pytest.mark.parametrize("mode,expected", [("strict", ["1"]), ("same_time", ["1", "2"]), ("explicit", ["1", "3"])])
def test_scope_and_priority(mode, expected):
    cfg = config(mode)
    cfg["fallback_targets"] = [target(), target(period="15:00-16:00"), target("三牌楼1号")]
    assert [s[0] for s in book.build_booking_candidates(slots(), cfg, DATE)] == expected


def test_legacy_defaults_to_strict_and_exact_names():
    cfg = config()
    del cfg["fallback_mode"]
    assert len(book.build_booking_candidates(slots(), cfg, DATE)) == 1
    cfg["targets"] = [target("仙林")]
    assert book.build_booking_candidates(slots(), cfg, DATE) == []


def test_preload_unavailable_primary_but_not_unavailable_fallback():
    cfg = config("same_time")
    cfg["targets"] = [target("仙林3号")]
    assert [s[0] for s in book.build_booking_candidates(slots(), cfg, DATE, True)] == ["5", "1", "2"]
    assert [s[0] for s in book.build_booking_candidates(slots(), cfg, DATE)] == ["1", "2"]


@pytest.mark.parametrize("patch", [
    {"targets": []}, {"targets": None}, {"fallback_mode": "all"},
    {"targets": [target("")]}, {"targets": [target(period="")]},
    {"targets": [target(period="25:00-26:00")]},
    {"targets": [target(period="19:00-18:00")]},
    {"fallback_mode": "explicit", "fallback_targets": []},
])
def test_invalid_scope_rejected(patch):
    cfg = config()
    cfg.update(patch)
    with pytest.raises(ValueError):
        book.validate_booking(cfg)


def test_main_empty_targets_stops_before_login_or_network(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["book.py", "--now"])
    monkeypatch.setattr(book, "load_config", lambda: {"booking": {"targets": []}})
    monkeypatch.setattr(book, "load_token", lambda: pytest.fail("不应读取登录或启动抓包"))
    assert book.main() == 1


@pytest.mark.parametrize("reload_slots", [False, True])
@pytest.mark.parametrize("mode,expected", [("strict", ["1", "1", "1"]), ("same_time", ["1", "2", "1"]), ("explicit", ["1", "3", "1"])])
def test_main_submits_only_authorized_candidates(monkeypatch, reload_slots, mode, expected):
    cfg = config(mode)
    cfg["fallback_targets"] = [target(period="15:00-16:00")]
    monkeypatch.setattr(sys, "argv", ["book.py", "--now"])
    monkeypatch.setattr(book, "load_config", lambda: {"booking": cfg})
    monkeypatch.setattr(book, "load_token", lambda: "fake")
    monkeypatch.setattr(book, "pre_fetch_type_id", lambda token: 1)
    monkeypatch.setattr(book, "get_server_offset", lambda token: 0)
    responses = iter([[] if reload_slots else slots(), slots()])
    monkeypatch.setattr(book, "query_slots", lambda *a: next(responses))
    submitted = []

    def submit(token, sid, date, student_id):
        submitted.append(sid)
        return len(submitted) == 3, {"data": {}, "errMsg": "尚未开始"}, 1

    monkeypatch.setattr(book, "book", submit)
    assert book.main() == 0
    assert submitted == expected


def test_slots_still_works_without_targets(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["book.py", "--slots"])
    monkeypatch.setattr(book, "load_config", lambda: {"booking": {"targets": []}})
    monkeypatch.setattr(book, "load_token", lambda: "fake")
    monkeypatch.setattr(book, "pre_fetch_type_id", lambda token: 1)
    monkeypatch.setattr(book, "get_server_offset", lambda token: 0)
    monkeypatch.setattr(book, "query_slots", lambda *a: slots())
    monkeypatch.setattr(book, "book", lambda *a: pytest.fail("查看场次不应提交"))
    assert book.main() == 0


def test_all_primary_targets_precede_backup(monkeypatch):
    cfg = config("same_time")
    cfg["targets"] = [target(f"仙林{i}号") for i in (4, 3, 2, 1)]
    available = [(str(i), f"仙林{i}号", "18:00", "19:00", True, DATE) for i in range(1, 6)]
    monkeypatch.setattr(sys, "argv", ["book.py", "--now"])
    monkeypatch.setattr(book, "load_config", lambda: {"booking": cfg})
    monkeypatch.setattr(book, "load_token", lambda: "fake")
    monkeypatch.setattr(book, "pre_fetch_type_id", lambda token: 1)
    monkeypatch.setattr(book, "get_server_offset", lambda token: 0)
    monkeypatch.setattr(book, "query_slots", lambda *a: available)
    submitted = []

    def submit(token, sid, date, student_id):
        submitted.append(sid)
        return len(submitted) == 5, {"data": {}, "errMsg": "尚未开始"}, 1

    monkeypatch.setattr(book, "book", submit)
    assert book.main() == 0
    assert submitted == ["4", "3", "2", "1", "5"]


def test_preview_does_not_submit(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["book.py", "--test", "--now"])
    monkeypatch.setattr(book, "load_config", lambda: {"booking": config()})
    monkeypatch.setattr(book, "load_token", lambda: "fake")
    monkeypatch.setattr(book, "pre_fetch_type_id", lambda token: 1)
    monkeypatch.setattr(book, "get_server_offset", lambda token: 0)
    monkeypatch.setattr(book, "query_slots", lambda *a: slots())
    monkeypatch.setattr(book, "book", lambda *a: pytest.fail("演练不应提交"))
    assert book.main() == 0
