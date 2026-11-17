#!/usr/bin/env python3
"""
南邮羽毛球场地预约脚本 - 单文件版

用法:
  python book.py              # 预约（自动等待到 12:00）
  python book.py --slots      # 查看可用场次
  python book.py --test       # 演练（不实际预约）
  python book.py --now        # 立即预约（不等待 12:00）

配置: config/config.yaml
Token: data/session_cache.json（自动保存，过期可重新抓取）
"""

import argparse
import hashlib
import re
import sys
import time

import yaml
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta
from pathlib import Path

import requests

import token_util
import runtime
import scheduling

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config" / "config.yaml"

TOKEN_SALT = "4pGmY6s9zX"  # 从反编译源码提取


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError("配置文件必须为对象")
    return cfg


def load_token():
    """读取未过期 token；具体有效期余量由本次预约窗口统一校验。"""
    return token_util.read_cached_token(early_seconds=0)


def save_token(token):
    tok = token_util.save_token(token)
    if tok:
        print("[+] Token 已保存")
    return tok


# ---------------------------------------------------------------------------
# 签名
# ---------------------------------------------------------------------------
def calc_fingerprint(stadium_id: str, student_id: str, date: str, ts: str) -> str:
    raw = f"{stadium_id}|{student_id}|{date}|{ts}|{TOKEN_SALT}"
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# API 调用
# ---------------------------------------------------------------------------
BASE = "https://wechat.njupt.edu.cn/mini_program/v4"


# 禁用 SSL 警告（南邮小程序后端使用非公开证书）
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 复用连接，减少重复握手；实际延迟由运行日志测量。
SESSION = requests.Session()
# 抢票流量强制直连：无视系统代理/环境变量，避免代理规则把请求绕到远程节点拖慢 RTT
SESSION.trust_env = False
LAST_SUBMISSION_TIME = None


class ApiError(RuntimeError):
    def __init__(self, kind, message, retry_after=0):
        super().__init__(message)
        self.kind, self.retry_after = kind, retry_after


def retry_after_seconds(value):
    try:
        return max(0, float(value))
    except (ValueError, TypeError):
        try:
            return max(0, parsedate_to_datetime(value).timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            return 2.0


def classify_response(resp):
    if resp.get("success") is True:
        return "success"
    code = str(resp.get("errCode", ""))
    msg = str(resp.get("errMsg") or "").lower()
    if code == "5004" or "token" in msg and any(s in msg for s in ("失效", "过期", "错误")):
        return "auth"
    if any(s in msg for s in ("未支付", "超限", "已有订单", "重复预约")):
        return "existing_order"
    if any(s in msg for s in ("频繁", "限流", "too many")):
        return "rate_limit"
    if any(s in msg for s in ("已满", "满场", "被预约", "无剩余", "已被预订")) or msg == "满":
        return "full"
    if any(s in msg for s in ("未开放", "未开始", "尚未开始")):
        return "not_open"
    return "rejected"


def _request(method, path, token, timeout=5, **kwargs):
    global LAST_SUBMISSION_TIME
    submitting = method == "POST"
    try:
        if submitting:
            LAST_SUBMISSION_TIME = time.time()
        r = SESSION.request(method, f"{BASE}{path}", headers={"token": token, **kwargs.pop("headers", {})},
                            timeout=timeout, verify=False, allow_redirects=False, **kwargs)
    except requests.RequestException as e:
        raise ApiError("unknown" if submitting else "network", "提交结果未知，请到小程序核对订单" if submitting else "只读查询网络异常") from e
    if r.status_code in (401, 403):
        raise ApiError("auth", "登录失效或无权限，请重新登录")
    if r.status_code == 429:
        raise ApiError("rate_limit", "服务端限流", retry_after_seconds(r.headers.get("Retry-After")))
    if not 200 <= r.status_code < 300:
        raise ApiError("unknown" if submitting else "http", f"接口返回 HTTP {r.status_code}")
    try:
        body = r.json()
    except ValueError as e:
        raise ApiError("unknown" if submitting else "format", "接口返回格式异常") from e
    if not isinstance(body, dict):
        raise ApiError("unknown" if submitting else "format", "接口响应不是对象")
    if not submitting and classify_response(body) == "auth":
        raise ApiError("auth", "登录已被服务端判定失效，请重新登录")
    if not submitting and body.get("success") is False:
        raise ApiError(classify_response(body), "只读查询被服务端拒绝，请稍后重试或到小程序查看")
    if submitting and not isinstance(body.get("success"), bool):
        raise ApiError("unknown", "提交响应缺少明确结果，请核对订单")
    return body


def api_get(path, token, params=None, timeout=5):
    return _request("GET", path, token, params=params, timeout=timeout)


def api_post(path, token, data, content_type="application/x-www-form-urlencoded", timeout=5):
    return _request("POST", path, token, headers={"Content-Type": content_type}, data=data, timeout=timeout)


# ---------------------------------------------------------------------------
# 场地过滤
# ---------------------------------------------------------------------------
def is_preferred_location(name: str, location: str) -> bool:
    """按配置的预约地点过滤场地（location 为空表示不限）。"""
    if not location:
        return True
    return location in name


# ---------------------------------------------------------------------------
# 场次查询
# ---------------------------------------------------------------------------
def pre_fetch_type_id(token):
    """预拉取羽毛球的 typeId（静态数据，只需查一次）。"""
    types = api_get("/venue/user/types", token)
    data = types.get("data") or []  # token 失效时 data 为 null，防御
    for t in data:
        if "羽毛球" in str(t.get("name", "")):
            tid = t["id"]
            print(f"[+] 羽毛球 typeId = {tid}")
            return tid
    print("[-] 未找到羽毛球类型")
    return None


def query_slots(token, date, type_id=None):
    """查询可用场次，返回 [(stadium_id, court_name, start, end, status, local_date), ...]"""
    if type_id is None:
        type_id = pre_fetch_type_id(token)
    if not type_id:
        return []

    data = api_get(f"/venue/user/time/display/{type_id}", token, {"date": date})
    slots = []
    for entry in (data.get("data") or []):  # token 失效时 data 为 null，防御
        local_date = entry.get("localDate", date)
        for tf in entry.get("timeFields", []):
            start, end = tf.get("startTime", "")[:5], tf.get("endTime", "")[:5]
            for st in tf.get("stadiumInfos", []):
                slots.append((
                    str(st["id"]), st.get("name", ""), start, end, st.get("status", False), local_date
                ))
    return slots


# ---------------------------------------------------------------------------
# 匹配目标
# ---------------------------------------------------------------------------
def match_targets(slots, cfg_targets):
    """按配置的 targets 顺序匹配可用场次，返回 [(stadium_id, court, start, end, date), ...]"""
    matched = []
    seen = set()
    for tgt in cfg_targets:
        tgt_name = tgt.get("court_name", "")
        tgt_time = tgt.get("time", "")
        for sid, name, start, end, status, date in slots:
            key = (sid, start)
            if key in seen:
                continue
            if not status:
                continue
            if tgt_name and tgt_name not in name:
                continue
            if tgt_time and f"{start}-{end}" != tgt_time:
                continue
            matched.append((sid, name, start, end, date))
            seen.add(key)
    return matched


# ---------------------------------------------------------------------------
# 预约
# ---------------------------------------------------------------------------
FALLBACK_MODES = {"strict": "仅已选场次", "same_time": "同时段换场", "explicit": "指定备选"}


def validate_booking(booking):
    """预约范围必须明确；旧配置默认严格模式，空目标不能视为全选。"""
    mode = booking.get("fallback_mode", "strict")
    if mode not in FALLBACK_MODES:
        raise ValueError("fallback_mode 必须是 strict、same_time 或 explicit")
    targets = booking.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError("未设置预约目标，请运行 configure.py 选择场次；空目标不会自动预约")
    groups = [targets]
    if mode == "explicit":
        backups = booking.get("fallback_targets")
        if not isinstance(backups, list) or not backups:
            raise ValueError("指定备选模式需要设置 fallback_targets")
        groups.append(backups)
    for group in groups:
        for target in group:
            if not isinstance(target, dict):
                raise ValueError("场次配置必须包含完整场地名和时间段")
            name, period = target.get("court_name"), target.get("time")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("请填写完整场地名，不支持空名称匹配所有场地")
            if not isinstance(period, str) or not re.fullmatch(
                r"(?:[01]\d|2[0-3]):[0-5]\d-(?:[01]\d|2[0-3]):[0-5]\d", period
            ) or period[:5] >= period[6:]:
                raise ValueError("场次时间必须为 HH:MM-HH:MM，且结束时间晚于开始时间")


def build_booking_candidates(slots, booking, date, include_unavailable=False):
    """所有查询路径共用范围规则；主选优先，备选去重且受校区和日期限制。"""
    validate_booking(booking)
    location = booking.get("location", "仙林")
    eligible = [s for s in slots if s[5] == date and is_preferred_location(s[1], location)]
    result, seen = [], set()

    def add(s):
        entry = (s[0], s[1], s[2], s[3], s[5])
        if entry not in seen:
            seen.add(entry)
            result.append(entry)

    def select(targets, allow_unavailable=False):
        for target in targets:
            for s in eligible:
                if (s[4] or allow_unavailable) and s[1] == target["court_name"] and f"{s[2]}-{s[3]}" == target["time"]:
                    add(s)

    select(booking["targets"], include_unavailable)
    mode = booking.get("fallback_mode", "strict")
    if mode == "same_time":
        for period in dict.fromkeys(t["time"] for t in booking["targets"]):
            for s in eligible:
                if s[4] and f"{s[2]}-{s[3]}" == period:
                    add(s)
    elif mode == "explicit":
        select(booking["fallback_targets"])
    return result


_warned_5004 = [False]


def _warn_token_5004():
    """服务端返回 token 失效时提示一次（避免抢票循环里刷屏）。"""
    if not _warned_5004[0]:
        _warned_5004[0] = True
        print("[!] 服务端返回 token 已失效 (errCode 5004)。请运行: python capture_token.py --refresh 后重试")


def book(token, stadium_id, date, student_id):
    """预约单个场次，返回 (success, response_json, rtt_ms)"""
    ts = str(int(time.time() * 1000))
    fp = calc_fingerprint(stadium_id, student_id, date, ts)
    t0 = time.perf_counter()
    resp = api_post(
        f"/venue/user/booking/pomelo/v2/{stadium_id}",
        token,
        {"timestamp": ts, "fingerprint": fp, "date": date},
    )
    rtt_ms = (time.perf_counter() - t0) * 1000
    if isinstance(resp, dict) and resp.get("errCode") == 5004:
        _warn_token_5004()
    return resp.get("success", False), resp, rtt_ms


# ---------------------------------------------------------------------------
# 服务器时间同步
# ---------------------------------------------------------------------------
def get_server_offset(token, samples=3, budget_seconds=6):
    """HTTP Date 只有秒级精度；取低 RTT 样本的中点估计，不声称毫秒对时。"""
    samples_found = []
    deadline = time.monotonic() + budget_seconds
    for _ in range(samples):
        remaining = deadline - time.monotonic()
        if remaining < 0.2:
            break
        runtime.check_stop()
        try:
            wall = time.time()
            started = time.monotonic()
            # connect/read 各自限时，避免最后几秒串行长等待。
            part = min(1.0, remaining / 2)
            r = SESSION.get(f"{BASE}/venue/user/types", headers={"token": token},
                            timeout=(part, part), verify=False, allow_redirects=False)
            rtt = time.monotonic() - started
            if r.status_code != 200 or not r.headers.get("Date"):
                continue
            stamp = parsedate_to_datetime(r.headers["Date"]).timestamp()
            offset = stamp + 0.5 - (wall + rtt / 2)
            samples_found.append((rtt, offset))
        except (requests.RequestException, ValueError, TypeError, OverflowError):
            continue
    if not samples_found:
        print("[!] 对时不可用，使用本地时钟；本次没有服务器对时保证")
        return 0.0
    rtt, offset = min(samples_found)
    print(f"[*] 时钟估计偏移 {offset:+.3f}s，参考不确定度至少约 ±{0.5 + rtt / 2:.3f}s")
    return offset


def wait_until_server(h, m, server_offset, advance_seconds=0, on_near=None):
    """将一次墙钟估计转换为单调时钟截止时间；等待期间不发网络请求。"""
    target = (datetime.now() + timedelta(seconds=server_offset)).replace(
        hour=h, minute=m, second=0, microsecond=0).timestamp() - advance_seconds
    deadline = time.monotonic() + target - (time.time() + server_offset)
    return wait_for_deadline(deadline)


def wait_for_deadline(deadline):
    while True:
        runtime.check_stop()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return -remaining
        time.sleep(min(0.2, remaining))


def token_covers(token, required_until, student_id="", server_offset=0):
    expiry = token_util.jwt_exp(token) or 0
    return (expiry > required_until and expiry > time.time() + server_offset
            and (not student_id or token_util.token_student_id(token) == student_id))


def verify_login(token):
    data = api_get("/venue/user/types", token, timeout=3)
    if not data.get("data") or data.get("success") is False:
        raise ApiError("auth", "登录只读验证未通过，请重新登录")


def guard_path():
    return runtime.DATA_DIR / "order_guard.json"


def acknowledge_order():
    with runtime.single_instance("booking"):
        guard_path().unlink(missing_ok=True)
    print("[+] 已记录人工核对，可再次启动预约")


def save_guard(state, slot):
    token_util.atomic_write_json(guard_path(), {
        "state": state, "date": slot[4], "court": slot[1], "period": f"{slot[2]}-{slot[3]}",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    })


def run_attempts(token, ranked, student_id, cfg, fire_epoch=None, server_offset=0):
    global LAST_SUBMISSION_TIME
    opts = scheduling.settings(cfg.get("booking") or {})
    start = time.monotonic()
    deadline = start + opts["retry_window_seconds"]
    remaining_slots = list(ranked)
    index = 0
    throttles = 0
    first_sent_at = None
    for attempt in range(opts["max_attempts"]):
        runtime.check_stop()
        remaining = deadline - time.monotonic()
        if remaining < 5:
            break  # 留出单次请求的超时预算，不在截止边缘启动新提交。
        if not remaining_slots:
            runtime.report("full", "[-] 允许范围内的候选均已返回满场")
            return 1
        slot = remaining_slots[index % len(remaining_slots)]
        sid, name, begin, end, date = slot
        if not token_covers(token, time.time() + server_offset + 10, student_id, server_offset):
            runtime.report("auth", "[-] 登录有效期不足，停止提交并请重新登录")
            return 1
        # 必须先写待核对标记：进程被杀时，下一次启动不会盲目重复提交。
        save_guard("submitting", slot)
        sent_at = time.time()
        LAST_SUBMISSION_TIME = None
        try:
            ok, resp, rtt = book(token, sid, date, student_id)
            kind = "success" if ok else classify_response(resp)
            retry_after = 0
        except ApiError as e:
            kind, retry_after = e.kind, e.retry_after
            rtt, resp = None, {}
        if first_sent_at is None:
            first_sent_at = LAST_SUBMISSION_TIME or sent_at
        deviation_ms = (first_sent_at + server_offset - fire_epoch) * 1000 if fire_epoch else None
        if kind == "unknown":
            save_guard("unknown", slot)
            runtime.report("unknown", "[!] 提交结果未知，已暂停。请到小程序核对订单，确认后解除暂停",
                           attempt=attempt + 1)
            runtime.notify("预约结果待核对", "网络或响应异常，请到小程序核对订单；程序已停止重试", cfg)
            return 2
        if kind == "success":
            data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
            detail = data.get("detail") if isinstance(data.get("detail"), dict) else {}
            order = data.get("order") if isinstance(data.get("order"), dict) else {}
            summary = {"court": detail.get("stadiumName", name), "date": date,
                       "start": detail.get("startTime", begin), "end": detail.get("endTime", end),
                       "price": detail.get("price"), "order_id": order.get("orderId"),
                       "created_at": datetime.now().isoformat(timespec="seconds")}
            save_guard("success", slot)
            try:
                token_util.atomic_write_json(runtime.DATA_DIR / "last_order.json", summary)
            except OSError:
                print("[!] 订单摘要保存失败，请到小程序查看")
            runtime.report("success", f"[+] 预约成功：{summary['court']} {summary['start']}-{summary['end']}。请立即到小程序核对并支付（以页面倒计时为准）",
                           attempt=attempt + 1, rtt_ms=rtt, estimated_first_send_deviation_ms=deviation_ms,
                           elapsed_seconds=round(time.monotonic() - start, 3))
            runtime.notify("预约成功，待支付", f"{name} {begin}-{end}，请立即到小程序支付", cfg)
            return 0
        if kind == "existing_order":
            save_guard("existing_order", slot)
            runtime.report(kind, "[-] 已有订单或预约超限，停止；请到小程序核对")
            return 1
        guard_path().unlink(missing_ok=True)  # 仅在收到明确未成功响应后清除。
        if kind in ("auth", "rejected"):
            runtime.report(kind, "[-] 登录失效，请重新登录" if kind == "auth" else "[-] 服务端拒绝预约，已停止；请在小程序查看原因",
                           error_code=str(resp.get("errCode", ""))[:40])
            return 1
        if kind == "full":
            remaining_slots.remove(slot)
        elif kind == "rate_limit":
            throttles += 1
        else:
            index += 1
        runtime.report("retrying", f"[*] 第 {attempt + 1} 次：{'满场，切换候选' if kind == 'full' else '限流，等待' if kind == 'rate_limit' else '尚未开放，等待'}",
                       attempt=attempt + 1, rtt_ms=rtt)
        delay = max(retry_after, min(2 ** throttles, 30)) if kind == "rate_limit" else opts["retry_interval_seconds"]
        if time.monotonic() + delay >= deadline:
            break
        runtime.pause(delay)
    runtime.report("exhausted", "[-] 已达到本次尝试次数或时间上限，预约未成功")
    return 1


def _main(args):
    cfg = load_config()
    if "--refresh" in args:
        from capture_token import run_refresh
        tc = cfg.get("token_capture") or {}
        return run_refresh(cfg, tc.get("timeout_seconds", 300), force=True)
    booking_cfg = cfg.get("booking") or {}
    is_slots, is_test, is_now = "--slots" in args, "--test" in args, "--now" in args
    if not is_slots:
        validate_booking(booking_cfg)
    opts = scheduling.settings(booking_cfg)
    if not is_slots and not is_test:
        reason = scheduling.skip_reason(booking_cfg, immediate=is_now)
        if reason:
            runtime.report("skipped", f"[*] {reason}")
            return 0
        if guard_path().exists():
            runtime.report("blocked", "[!] 上次提交需要核对。请到小程序查看订单，确认后执行 --acknowledge-order 或在窗口解除暂停")
            return 2
    planned = scheduling.target_time(booking_cfg).timestamp()
    required_until = (time.time() if is_now or is_slots or is_test else max(time.time(), planned)) + opts["retry_window_seconds"] + opts["token_margin_seconds"]
    student_id = str((cfg.get("auth") or {}).get("student_id") or "")
    token = load_token()
    if not token_covers(token, required_until, student_id):
        tc = cfg.get("token_capture") or {}
        runtime.report("auth", "[!] 登录缺失、账号不符或有效期无法覆盖本次预约，请打开电脑微信的小程序场地页")
        runtime.notify("需要刷新登录", "请打开电脑微信的小程序场地页，刷新后继续预约", cfg)
        if tc.get("enabled", True):
            runtime.check_stop()
            from capture_token import run_refresh
            if run_refresh(cfg, tc.get("fallback_timeout_seconds", 60), force=True, required_until=required_until) == 0:
                token = load_token()
        if not token_covers(token, required_until, student_id):
            runtime.report("auth", "[-] 登录未就绪，停止预约")
            return 1
    expiry = datetime.fromtimestamp(token_util.jwt_exp(token)).strftime("%m-%d %H:%M:%S")
    print(f"[*] 登录有效至 {expiry}")
    type_id = pre_fetch_type_id(token)
    if not type_id:
        runtime.report("auth", "[-] 无法读取运动类型，请核对登录")
        return 1
    date = datetime.now().strftime("%Y-%m-%d")
    slots = query_slots(token, date, type_id)
    if is_slots:
        for sid, name, begin, end, available, day in slots:
            if day == date and available and is_preferred_location(name, booking_cfg.get("location", "仙林")):
                print(f"{name} {begin}-{end}")
        return 0
    ranked = build_booking_candidates(slots, booking_cfg, date, include_unavailable=not is_now)
    if is_now and not ranked:
        ranked = build_booking_candidates(query_slots(token, date, type_id), booking_cfg, date)
    if is_test:
        print(f"[*] 预约范围：{FALLBACK_MODES[booking_cfg.get('fallback_mode', 'strict')]}")
        for _, name, begin, end, _ in ranked:
            print(f"    {name} {begin}-{end}")
        print("[*] 演练完成，未提交预约")
        return 0
    offset = get_server_offset(token)
    h, m = map(int, opts["schedule_time"].split(":"))
    if not is_now:
        runtime.report("waiting", f"[*] 等待 {opts['schedule_time']}，最后 30 秒完成登录和场次检查")
        wait_until_server(h, m, offset, advance_seconds=30)
        runtime.check_stop()
        # 有足够预算才做最后的只读校验；最后几秒不再校准/查询。
        if planned - (time.time() + offset) >= 12:
            fresh = load_token()
            required_until = planned + opts["retry_window_seconds"] + opts["token_margin_seconds"]
            if not token_covers(fresh, required_until, student_id, offset):
                runtime.report("auth", "[-] 开抢前登录有效期不足，请刷新登录")
                runtime.notify("需要重新登录", "本次预约已停止，请刷新登录", cfg)
                return 1
            token = fresh
            verify_login(token)
            slots = query_slots(token, date, type_id)
            ranked = build_booking_candidates(slots, booking_cfg, date, include_unavailable=True)
        if time.time() + offset > planned + opts["late_grace_seconds"]:
            runtime.report("skipped", "[-] 准备阶段已错过开抢窗口，停止")
            return 1
        wait_until_server(h, m, offset)
    else:
        verify_login(token)
    if not ranked:
        runtime.report("empty", "[-] 所选范围内无可预约候选")
        return 1
    # 等待期间的日期切换、今天跳过等改动也必须生效。
    if datetime.now().strftime("%Y-%m-%d") != date:
        runtime.report("skipped", "[-] 日期已变化，请重新启动")
        return 1
    reason = scheduling.skip_reason(booking_cfg, immediate=is_now)
    if reason:
        runtime.report("skipped", f"[*] {reason}")
        return 0
    return run_attempts(token, ranked, student_id, cfg, None if is_now else planned, offset)


def main():
    parser = argparse.ArgumentParser(description="?????????")
    for flag, help_text in [
        ("slots", "?????????"), ("test", "????"),
        ("now", "????"), ("refresh", "?????????"),
        ("stop", "?????????"), ("skip-today", "???????"),
        ("acknowledge-order", "?????????"),
    ]:
        parser.add_argument("--" + flag, action="store_true", help=help_text)
    parsed = parser.parse_args()
    args = {"--" + name.replace("_", "-") for name, enabled in vars(parsed).items() if enabled}
    if "--stop" in args:
        runtime.request_stop("booking")
        runtime.request_stop("capture")
        print("[*] 已请求停止，正在发送的请求会先返回结果")
        return 0
    if "--skip-today" in args:
        scheduling.set_skip_today()
        runtime.request_stop("booking")
        return 0
    try:
        if "--acknowledge-order" in args:
            acknowledge_order()
            return 0
        with runtime.single_instance("booking"):
            runtime.clear_stop("booking")
            from capture_token import recovery_path, recover_proxy
            if recovery_path().exists() and not runtime.is_running("capture"):
                recover_proxy()
            return _main(args)
    except runtime.AlreadyRunning as e:
        print(f"[-] {e}")
        return 1
    except runtime.Cancelled as e:
        runtime.report("stopped", str(e))
        return 1
    except KeyboardInterrupt:
        runtime.report("stopped", "[*] 已停止，请在小程序核对可能已发送的预约")
        return 1
    except ApiError as e:
        runtime.report(e.kind, f"[-] {e}")
        return 1
    except RuntimeError as e:
        runtime.report("error", f"[-] {e}")
        return 1
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as e:
        runtime.report("error", f"[-] 配置或运行错误（{type(e).__name__}），请检查配置和本地文件")
        return 1


if __name__ == "__main__":
    sys.exit(main())
