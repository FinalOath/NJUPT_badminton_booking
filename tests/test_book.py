"""book.py 核心逻辑单测（抢票策略、场地匹配、API 解析）。"""

import book


class TestFingerprint:
    def test_deterministic(self):
        a = book.calc_fingerprint("245", "B21000001", "2026-08-11", "1780000000000")
        b = book.calc_fingerprint("245", "B21000001", "2026-08-11", "1780000000000")
        assert a == b
        assert len(a) == 64  # sha256 hex

    def test_changes_with_input(self):
        a = book.calc_fingerprint("245", "B21000001", "2026-08-11", "1780000000000")
        b = book.calc_fingerprint("246", "B21000001", "2026-08-11", "1780000000000")
        assert a != b


class TestLocation:
    def test_xianlin(self):
        assert book.is_preferred_location("仙林体育馆1号场地", "仙林")
        assert not book.is_preferred_location("三牌楼体育馆1号场地", "仙林")

    def test_sanpailou(self):
        assert book.is_preferred_location("三牌楼体育馆1号场地", "三牌楼")
        assert not book.is_preferred_location("仙林体育馆1号场地", "三牌楼")

    def test_all_when_empty(self):
        assert book.is_preferred_location("仙林体育馆1号场地", "")
        assert book.is_preferred_location("三牌楼体育馆1号场地", "")


def slot(sid, name, start, end, status=True, date="2026-08-11"):
    return (sid, name, start, end, status, date)


class TestMatchTargets:
    """抢票策略：按配置优先级匹配。"""

    def test_priority_order(self):
        slots = [slot("245", "仙林1号", "16:00", "17:00"),
                 slot("294", "仙林2号", "16:00", "17:00")]
        targets = [{"court_name": "仙林2号", "time": "16:00-17:00"},
                   {"court_name": "仙林1号", "time": "16:00-17:00"}]
        result = book.match_targets(slots, targets)
        assert result[0][0] == "294"   # 2号在配置里优先级更高
        assert result[1][0] == "245"

    def test_time_filter(self):
        slots = [slot("245", "仙林1号", "16:00", "17:00"),
                 slot("301", "仙林3F1号", "17:00", "18:00")]
        targets = [{"court_name": "", "time": "17:00-18:00"}]
        result = book.match_targets(slots, targets)
        assert len(result) == 1
        assert result[0][2] == "17:00"

    def test_skips_unavailable(self):
        slots = [slot("245", "仙林1号", "16:00", "17:00", status=False)]
        targets = [{"court_name": "仙林", "time": ""}]
        assert book.match_targets(slots, targets) == []

    def test_court_name_substring(self):
        slots = [slot("245", "仙林体育馆主馆1F羽毛球1号场地", "16:00", "17:00")]
        targets = [{"court_name": "羽毛球1号场地", "time": ""}]
        result = book.match_targets(slots, targets)
        assert len(result) == 1

    def test_no_duplicate_sid_start(self):
        slots = [slot("245", "仙林1号", "16:00", "17:00"),
                 slot("245", "仙林1号", "16:00", "17:00")]
        targets = [{"court_name": "仙林1号", "time": ""}]
        result = book.match_targets(slots, targets)
        assert len(result) == 1  # 完全相同 (sid, start) 去重

    def test_same_sid_diff_time_kept(self):
        slots = [slot("245", "仙林1号", "16:00", "17:00"),
                 slot("245", "仙林1号", "17:00", "18:00")]
        targets = [{"court_name": "仙林1号", "time": ""}]
        result = book.match_targets(slots, targets)
        assert len(result) == 2  # 同一场地不同时间段 = 不同可预约场次


class TestQuerySlots:
    def test_parse_structure(self, monkeypatch):
        def fake_api_get(*a, **k):
            return {"success": True, "data": [
                {"localDate": "2026-08-11", "timeFields": [
                    {"startTime": "16:00:00", "endTime": "17:00:00", "stadiumInfos": [
                        {"id": 245, "name": "仙林1号", "status": True},
                        {"id": 294, "name": "仙林2号", "status": False},
                    ]},
                ]},
            ]}
        monkeypatch.setattr(book, "api_get", fake_api_get)
        slots = book.query_slots("token", "2026-08-11", type_id=1)
        assert len(slots) == 2
        assert slots[0][0] == "245"
        assert slots[1][4] is False   # status

    def test_data_null_graceful(self, monkeypatch):
        # 后端返回 data:null（token 失效）不应崩溃
        monkeypatch.setattr(book, "api_get", lambda *a, **k: {"success": False, "data": None})
        assert book.query_slots("token", "2026-08-11", type_id=1) == []


class TestPreFetchTypeId:
    def test_find_badminton(self, monkeypatch):
        monkeypatch.setattr(book, "api_get", lambda *a, **k: {
            "data": [{"id": 1, "name": "羽毛球"}, {"id": 2, "name": "篮球"}]})
        assert book.pre_fetch_type_id("token") == 1

    def test_data_null_graceful(self, monkeypatch):
        monkeypatch.setattr(book, "api_get", lambda *a, **k: {"data": None})
        assert book.pre_fetch_type_id("token") is None


class TestBook:
    def test_success(self, monkeypatch):
        monkeypatch.setattr(book, "api_post", lambda *a, **k: {"success": True, "data": {}})
        ok, resp = book.book("token", "245", "2026-08-11", "B21000001")
        assert ok is True

    def test_failure(self, monkeypatch):
        monkeypatch.setattr(book, "api_post", lambda *a, **k: {"success": False, "errMsg": "满"})
        ok, resp = book.book("token", "245", "2026-08-11", "B21000001")
        assert ok is False

    def test_5004_warns(self, monkeypatch):
        book._warned_5004[0] = False
        monkeypatch.setattr(book, "api_post",
                            lambda *a, **k: {"success": False, "errCode": 5004, "errMsg": "token错误"})
        book.book("token", "245", "2026-08-11", "B21000001")
        assert book._warned_5004[0] is True
