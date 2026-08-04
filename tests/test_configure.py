"""configure.py 单测（配置渲染、序号解析去重）。"""

import yaml

import configure


def filtered():
    """模拟 show_court_table 返回的场次列表（6 元组）。"""
    return [
        ("245", "仙林1号", "19:00", "20:00", True, "2026-08-11"),
        ("246", "仙林2号", "19:00", "20:00", True, "2026-08-11"),
        ("247", "仙林3号", "19:00", "20:00", True, "2026-08-11"),
        ("248", "仙林4号", "20:00", "21:00", True, "2026-08-11"),
    ]


class TestParsePicks:
    def test_dedup(self):
        targets, dup = configure.parse_picks("1，3，1，1", filtered())
        assert dup == 2
        assert len(targets) == 2
        assert targets[0]["court_name"] == "仙林1号"

    def test_order_preserved(self):
        targets, dup = configure.parse_picks("4,1,2", filtered())
        assert dup == 0
        assert [t["court_name"] for t in targets] == ["仙林4号", "仙林1号", "仙林2号"]
        assert targets[0]["time"] == "20:00-21:00"

    def test_invalid_ignored(self):
        targets, dup = configure.parse_picks("1,x,99,-3", filtered())
        assert len(targets) == 1
        assert dup == 0

    def test_empty_input(self):
        targets, dup = configure.parse_picks("", filtered())
        assert targets == []
        assert dup == 0

    def test_out_of_range(self):
        targets, dup = configure.parse_picks("5", filtered())  # 只有 4 个
        assert targets == []
        assert dup == 0


class TestRenderConfig:
    def test_no_date_fields(self):
        text = configure.render_config({
            "auth": {"student_id": "B21000001"},
            "booking": {"location": "仙林",
                        "targets": [{"court_name": "仙林1号", "time": "19:00-20:00"}]},
            "token_capture": {"mode": "pc_wechat"},
        })
        assert "target_date" not in text
        assert "book_ahead_days" not in text
        assert "B21000001" in text
        assert "仙林1号" in text

    def test_valid_yaml(self):
        text = configure.render_config({
            "auth": {"student_id": "B21000001"},
            "booking": {"location": "三牌楼",
                        "targets": [{"court_name": "三牌楼1号", "time": "19:00-20:00"}]},
            "token_capture": {"mode": "pc_wechat", "enabled": True},
        })
        cfg = yaml.safe_load(text)
        assert cfg["auth"]["student_id"] == "B21000001"
        assert cfg["booking"]["location"] == "三牌楼"
        assert cfg["booking"]["targets"][0]["court_name"] == "三牌楼1号"
        assert cfg["token_capture"]["enabled"] is True
