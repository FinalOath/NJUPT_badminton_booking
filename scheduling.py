"""计划参数与当天运行规则，供命令行、窗口和 Windows 任务共用。"""
from datetime import datetime, timedelta

import runtime
from token_util import atomic_write_json


def settings(booking):
    def number(key, default, low, high):
        value = booking.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            raise ValueError(f"{key} 必须在 {low} 到 {high} 之间")
        return value

    schedule = booking.get("schedule_time", "12:00")
    try:
        h, m = map(int, schedule.split(":"))
        if len(schedule) != 5 or not 0 <= h < 24 or not 0 <= m < 60:
            raise ValueError()
    except (AttributeError, TypeError, ValueError):
        raise ValueError("schedule_time 必须为 HH:MM")
    days = booking.get("weekdays", list(range(1, 8)))
    if not isinstance(days, list) or not days or any(type(d) is not int or d not in range(1, 8) for d in days):
        raise ValueError("weekdays 需为 1 至 7 的非空列表（周一至周日）")
    result = {
        "schedule_time": schedule, "weekdays": sorted(set(days)),
        "max_attempts": number("max_attempts", 30, 1, 200),
        "retry_window_seconds": number("retry_window_seconds", 60, 10, 300),
        "retry_interval_seconds": number("retry_interval_seconds", 0.5, 0.1, 30),
        "late_grace_seconds": number("late_grace_seconds", 60, 0, 300),
        "token_margin_seconds": number("token_margin_seconds", 120, 30, 600),
        "prepare_minutes": number("prepare_minutes", 5, 1, 60),
        "refresh_minutes": number("refresh_minutes", 30, 2, 120),
    }
    for key in ("max_attempts", "prepare_minutes", "refresh_minutes"):
        if int(result[key]) != result[key]:
            raise ValueError(f"{key} 必须为整数")
        result[key] = int(result[key])
    if result["refresh_minutes"] <= result["prepare_minutes"]:
        raise ValueError("登录提醒需早于预约准备时间")
    if h * 60 + m < result["refresh_minutes"]:
        raise ValueError("当前仅支持当天准备，请将开抢时间设在登录提前量之后")
    return result


def target_time(booking, now=None):
    now = now or datetime.now()
    h, m = map(int, settings(booking)["schedule_time"].split(":"))
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


def set_skip_today(skip=True):
    atomic_write_json(runtime.DATA_DIR / "skip_today.json", {"date": datetime.now().date().isoformat() if skip else ""})


def skip_reason(booking, now=None, immediate=False):
    now = now or datetime.now()
    opts = settings(booking)
    if now.isoweekday() not in opts["weekdays"]:
        return "今天不是配置的预约日"
    skipped = runtime.read_json(runtime.DATA_DIR / "skip_today.json", {})
    if skipped.get("date") == now.date().isoformat():
        return "已设置今天跳过"
    if not immediate and now.timestamp() > target_time(booking, now).timestamp() + opts["late_grace_seconds"]:
        return "已错过今天的开抢窗口，停止补跑"
    return ""


def task_plan(cfg):
    booking = cfg.get("booking") or {}
    opts = settings(booking)
    start = target_time(booking)
    return {
        "weekdays": opts["weekdays"],
        "booking_time": (start - timedelta(minutes=opts["prepare_minutes"])).strftime("%H:%M"),
        "refresh_time": (start - timedelta(minutes=opts["refresh_minutes"])).strftime("%H:%M"),
    }
