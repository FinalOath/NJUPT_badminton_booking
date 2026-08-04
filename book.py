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

import calendar, hashlib, sys, time, yaml
from datetime import datetime, timedelta
from pathlib import Path

import requests

import token_util

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config" / "config.yaml"

TOKEN_SALT = "4pGmY6s9zX"  # 从反编译源码提取


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_token():
    """从缓存加载 token，自动检查 JWT 过期。"""
    return token_util.read_cached_token(early_seconds=300)


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


def api_get(path, token, params=None):
    r = requests.get(f"{BASE}{path}", headers={"token": token}, params=params, timeout=10, verify=False)
    return r.json()


def api_post(path, token, data, content_type="application/x-www-form-urlencoded"):
    r = requests.post(f"{BASE}{path}", headers={"token": token, "Content-Type": content_type},
                      data=data, timeout=10, verify=False)
    return r.json()


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
_warned_5004 = [False]


def _warn_token_5004():
    """服务端返回 token 失效时提示一次（避免抢票循环里刷屏）。"""
    if not _warned_5004[0]:
        _warned_5004[0] = True
        print("[!] 服务端返回 token 已失效 (errCode 5004)。请运行: python capture_token.py --refresh 后重试")


def book(token, stadium_id, date, student_id):
    """预约单个场次，返回 (success, response_json)"""
    ts = str(int(time.time() * 1000))
    fp = calc_fingerprint(stadium_id, student_id, date, ts)
    resp = api_post(
        f"/venue/user/booking/pomelo/v2/{stadium_id}",
        token,
        {"timestamp": ts, "fingerprint": fp, "date": date},
    )
    if isinstance(resp, dict) and resp.get("errCode") == 5004:
        _warn_token_5004()
    return resp.get("success", False), resp


# ---------------------------------------------------------------------------
# 服务器时间同步
# ---------------------------------------------------------------------------
def get_server_offset(token):
    """获取服务器时间与本地时间的偏移（秒）。"""
    try:
        t1 = time.time()
        r = requests.get(f"{BASE}/venue/user/types", headers={"token": token}, timeout=5)
        rtt = time.time() - t1
        date_str = r.headers.get("Date", "")
        if date_str:
            # HTTP Date 是 GMT，用 calendar.timegm 按 UTC 解析
            dt = datetime.strptime(date_str[:-4], "%a, %d %b %Y %H:%M:%S")
            server_ts = calendar.timegm(dt.timetuple()) + dt.microsecond / 1e6
            offset = server_ts + rtt / 2 - t1
            print(f"    服务器时间偏移: {offset:+.1f}s  (RTT={rtt*1000:.0f}ms)")
            # 如果本地时间不准给出告警
            if abs(offset) > 3:
                print(f"[!] 警告：本地时间与服务器时间差 {offset:.0f}s，已自动修正")
            return offset
    except Exception as e:
        print(f"    [-] 无法同步服务器时间: {e}")
    return 0.0


# ---------------------------------------------------------------------------
# 主逻辑
# ---------------------------------------------------------------------------
def wait_until_server(h, m, server_offset, advance_seconds=0):
    """忙等直到服务器时间到达 h:m:00，advance_seconds 可提前触发。"""
    target_s = (datetime.now() + timedelta(seconds=server_offset)).replace(
        hour=h, minute=m, second=0, microsecond=0).timestamp()
    target_s -= advance_seconds  # 提前 N 秒（用于预查场次）
    while True:
        remaining = target_s - (time.time() + server_offset)
        if remaining <= 0:
            return -remaining  # 返回超时量（毫秒级精度）
        if remaining > 5:
            time.sleep(1.0)
        elif remaining > 1:
            time.sleep(0.1)
        elif remaining > 0.05:
            time.sleep(0.01)
        # 最后 50ms 忙等（精确到 ~1ms）


def main():
    cfg = load_config()
    args = set(sys.argv[1:])

    # --refresh: 强制重新抓取 token 后退出（不预约）
    if "--refresh" in args:
        from capture_token import run_refresh
        tc = cfg.get("token_capture", {}) or {}
        return run_refresh(cfg, tc.get("timeout_seconds", 300), force=True)

    token = load_token()
    if not token:
        # token 缺失/过期 → 尝试短时自动抓包，失败再给出手动指引
        tc = cfg.get("token_capture", {}) or {}
        if tc.get("enabled", True):
            print("[*] Token 已过期或缺失，尝试自动抓取（在电脑微信打开南邮小程序即可）...")
            try:
                from capture_token import run_refresh
                if run_refresh(cfg, tc.get("fallback_timeout_seconds", 60), force=False) == 0:
                    token = load_token()
            except Exception as e:
                # 未安装 mitmproxy 等环境问题：不应崩溃，给出手动指引
                print(f"[-] 自动抓取不可用: {e}")
        if not token:
            print("[-] 无有效 token。请运行: python capture_token.py --wait 300")
            print("    （在电脑微信打开南邮小程序 → 进入场地页，捕获后自动保存）")
            return 1

    student_id = cfg.get("auth", {}).get("student_id", "")
    targets = cfg.get("booking", {}).get("targets", [])
    schedule_time = cfg.get("booking", {}).get("schedule_time", "12:00")
    book_ahead = cfg.get("booking", {}).get("book_ahead_days", 7)
    location = cfg.get("booking", {}).get("location", "仙林")  # 仙林/三牌楼/空=不限

    is_test = "--test" in args
    is_now = "--now" in args
    is_slots = "--slots" in args

    # 计算目标日期：--date 参数 > 配置 target_date > 默认 today+N
    if "--date" in args:
        idx = sys.argv.index("--date") + 1
        target_date = sys.argv[idx] if idx < len(sys.argv) else ""
    else:
        target_date = cfg.get("booking", {}).get("target_date") or \
            (datetime.now() + timedelta(days=book_ahead)).strftime("%Y-%m-%d")

    h, m = map(int, schedule_time.split(":"))

    # ===== 1. 预拉取 + 时间同步 =====
    print("[*] 预拉取运动类型...")
    type_id = pre_fetch_type_id(token)
    if not type_id:
        return 1

    print("[*] 同步服务器时间...")
    server_offset = get_server_offset(token)

    # ===== 2. 提前查场次（获取场地 ID 映射）=====
    print(f"[*] 预查场次: {target_date}")
    slots = query_slots(token, target_date, type_id)
    slot_map = {}   # (court_name, start, end) -> stadium_id
    for sid, name, start, end, status, date in slots:
        if is_preferred_location(name, location):
            slot_map[(name, start, end)] = sid

    # 同时构建顺延池：所有可用场次
    expand_pool = [(s[0], s[1], s[2], s[3], s[5])
                   for s in slots if s[4] and is_preferred_location(s[1], location)]

    # 如果是 --slots，显示后退出
    if is_slots:
        avail = [s for s in slots if s[4] and is_preferred_location(s[1], location)]
        loc_txt = f"（{location}）" if location else "（不限地点）"
        print(f"\n  可预约 ({len(avail)} 个{loc_txt}):")
        for sid, name, start, end, status, date in avail:
            mark = "[ ]" if status else "[X]"
            print(f"  {mark} [{sid:>4}] {name}  {start}-{end}")
        return 0

    # 若有 slot_map，直接从目标生成抢购列表（不依赖 status，到点直接抢）
    if slot_map and targets:
        direct_targets = []
        for tgt in targets:
            key = (tgt["court_name"], *tgt["time"].split("-"))
            sid = slot_map.get(key)
            if sid:
                entry = (sid, tgt["court_name"], key[1], key[2], target_date)
                if entry not in direct_targets:
                    direct_targets.append(entry)
        if direct_targets:
            ranked = direct_targets
            print(f"[*] 预加载目标 ({len(ranked)} 个):")
            for sid, name, start, end, date in ranked:
                print(f"    [{sid}] {name}  {start}-{end}")
        else:
            print("[!] 预加载未匹配到目标，将在 12:00 实时查询")
            ranked = []
    else:
        ranked = []

    # ===== 3. 等待 + 抢购 =====
    if is_now:
        pass
    elif not ranked:
        # 无预加载目标 → 提前 2 秒查场次，然后卡 12:00
        wait_until_server(h, m, server_offset, advance_seconds=2.0)
    else:
        # 有预加载目标 → 精确等到 12:00:00.000
        wait_until_server(h, m, server_offset, advance_seconds=0.0)

    # 如果没有预加载到目标，现在查（等待已结束或即将结束）
    if not ranked:
        print(f"[*] 查询场次: {target_date}")
        slots = query_slots(token, target_date, type_id)
        ranked = match_targets(slots, targets) if targets else [
            (s[0], s[1], s[2], s[3], s[5]) for s in slots if s[4] and is_preferred_location(s[1], location)
        ]
        # 也构建顺延池
        expand_pool = [(s[0], s[1], s[2], s[3], s[5])
                       for s in slots if s[4] and is_preferred_location(s[1], location)]
        if not ranked:
            print("[-] 无可用场次")
            return 1
        print(f"[*] 目标场次 ({len(ranked)} 个):")
        for sid, name, start, end, date in ranked[:5]:
            print(f"    [{sid}] {name}  {start}-{end}")
        # 还有时间剩余 → 精确卡 12:00（仅非 --now 模式）
        if not is_now:
            wait_until_server(h, m, server_offset, advance_seconds=0.0)

    if is_test:
        print("[*] 演练模式，未实际预约")
        return 0

    # ===== 4. 开始抢购 =====
    top3 = ranked[:3]
    print(f"\n{'='*50}")
    print(f"[*] 开始抢购！目标: {[f'{s[1]}-{s[2]}' for s in top3]}")
    print(f"{'='*50}")
    for attempt in range(200):
        now = datetime.now()
        phase = "high" if (now.hour == h and now.minute == m and now.second < 2) else "normal"

        # 顺延：偏好场次尝试一轮后仍失败 → 扩大到所有仙林可用场次
        if attempt >= len(top3) and expand_pool and len(ranked) < len(expand_pool):
            new_count = 0
            for entry in expand_pool:
                if entry not in ranked:
                    ranked.append(entry)
                    new_count += 1
            if new_count > 0:
                top3 = ranked[:3]
                print(f"    [+] 顺延至所有仙林场次（共 {len(ranked)} 个目标）")

        if phase == "high":
            slot = top3[(attempt) % len(top3)]
        else:
            slot = ranked[attempt % len(ranked)]

        sid, name, start, end, date = slot
        ok, resp = book(token, sid, date, student_id)

        if ok:
            detail = resp.get("data", {}).get("detail", {})
            print(f"\n[+] 预约成功！")
            print(f"    场地: {detail.get('stadiumName', name)}")
            print(f"    时间: {detail.get('startTime', start)}-{detail.get('endTime', end)}")
            print(f"    金额: {detail.get('price', 8)} 元")
            print(f"    编号: {resp.get('data', {}).get('order', {}).get('orderId', '')}")
            print(f"[!] 请在 5 分钟内完成支付！")
            return 0

        err = resp.get("errMsg", "")
        if "超限" in err:
            print(f"[-] 预约超限（已有未支付的订单），先取消旧订单")
            return 1

        if attempt > 50 and phase == "normal":
            time.sleep(2.0)

        if attempt % 10 == 0:
            print(f"    # 尝试 {attempt+1}: {name} {start}-{end} -> {err or '失败'}")

    print("[-] 抢购失败，所有场次均已满")
    return 1


if __name__ == "__main__":
    sys.exit(main())
