#!/usr/bin/env python3
"""
mitmproxy addon：监听南邮小程序流量，自动提取并保存新 token。

运行环境是系统 mitmproxy 自带的 Python（没有仓库 .venv 依赖），
因此本模块顶层不做任何 mitmproxy import（仅在钩子方法内惰性导入），
核心判断逻辑可直接被仓库 .venv 里的 pytest 单测。

用法（由 capture_token.py 启动）:
    mitmdump --listen-port 8080 --ssl-insecure -s token_capture_addon.py

两个提取来源:
  1. 主来源：任意 API 请求的 `token` 请求头（小程序进入场地页即触发）
  2. 补充来源：/login/wxLogin 或 /login/wecomLogin 响应体里的 JWT

三道守卫（见 _consider）:
  1. 过期/即将过期 token 不保存
  2. 学号与 NJUPT_STUDENT_ID 不符（抓到别的账号）不保存
  3. exp 不比现有缓存更高（旧 token 覆盖新 token）不保存
"""

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from token_util import (normalize_token, jwt_exp, token_student_id,
                        save_token, current_best_exp, env_student_id)

HOST = "wechat.njupt.edu.cn"
LOGIN_PATHS = ("/login/wxLogin", "/login/wecomLogin")
EARLY_SECONDS = 300
APPID_PATH = Path(__file__).resolve().parent / "data" / "appid.txt"

_lock = threading.Lock()
_last_written = [None]
_saved_appids = set()


# ---------------------------------------------------------------------------
# 核心判断（可单测，不依赖 mitmproxy）
# ---------------------------------------------------------------------------
def _score(token):
    """返回候选 token 的 exp；不合规返回 None。"""
    tok = normalize_token(token)
    if not tok or tok.count(".") != 2:
        return None
    exp = jwt_exp(tok)
    if not exp or exp <= time.time() + EARLY_SECONDS:
        return None  # 不是有效 JWT，或已过期/即将过期
    sid = env_student_id()
    if sid and token_student_id(tok) != sid:
        return None  # 抓到别的账号，丢弃
    return exp


def _jwts_in_body(obj):
    """递归遍历已解析的 JSON，产出 JWT 形状且可解码的字符串。"""
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _jwts_in_body(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _jwts_in_body(v)
    elif isinstance(obj, str):
        tok = normalize_token(obj)
        if tok.count(".") == 2 and jwt_exp(tok):
            yield tok


def _consider(token, log=lambda msg: None):
    """对候选 token 做守卫后原子保存。log 为可选回调（mitmproxy 里接 ctx.log）。"""
    score = _score(token)
    if not score:
        return
    with _lock:
        if score <= current_best_exp():
            return  # 不比现有缓存更新，不覆盖
        norm = normalize_token(token)
        if norm == _last_written[0]:
            return  # 同一 token 去重
        _last_written[0] = norm
        save_token(norm)
        log(f"[token-capture] saved fresh token exp={score}")


# ---------------------------------------------------------------------------
# mitmproxy 钩子
# ---------------------------------------------------------------------------
def _capture_appid(flow):
    """从小程序请求的 Referer 头提取 appid，写入 data/appid.txt（供开发者工具探测用）。"""
    referer = flow.request.headers.get("Referer", "")
    if "servicewechat.com/" not in referer:
        return
    import re
    m = re.search(r"servicewechat\.com/([0-9a-zA-Z]+)/", referer)
    if not m:
        return
    appid = m.group(1)
    if appid in _saved_appids:
        return
    _saved_appids.add(appid)
    try:
        from mitmproxy import ctx
    except Exception:
        ctx = None
    try:
        APPID_PATH.parent.mkdir(parents=True, exist_ok=True)
        APPID_PATH.write_text(appid, encoding="utf-8")
        if ctx:
            ctx.log.info(f"[appid] saved {appid}")
    except Exception:
        pass


class TokenCapture:
    def request(self, flow):
        if flow.request.pretty_host != HOST:
            return
        # 记录 appid（来自 Referer 头）
        _capture_appid(flow)
        # 主来源：每次 API 请求都会带 token 头
        _consider(flow.request.headers.get("token", ""), self._log)
        auth = flow.request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            _consider(auth[7:], self._log)

    def response(self, flow):
        if flow.request.pretty_host != HOST:
            return
        # 补充来源：登录响应的 token 最新鲜
        if flow.request.path.startswith(LOGIN_PATHS) and flow.response:
            try:
                body = json.loads(flow.response.get_text() or "{}")
            except Exception:
                return
            for t in _jwts_in_body(body):
                _consider(t, self._log)

    @staticmethod
    def _log(msg):
        try:
            from mitmproxy import ctx
            ctx.log.info(msg)
        except Exception:
            pass


addons = [TokenCapture()]
