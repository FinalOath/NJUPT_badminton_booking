#!/usr/bin/env python3
"""
token 读取/校验/保存的共享模块（纯 stdlib）。

被三处使用：
  1. book.py            （仓库 .venv 环境）
  2. capture_token.py   （仓库 .venv 环境）
  3. token_capture_addon.py（系统 mitmproxy 的 Python 环境，没有项目依赖）

因此本模块只能使用标准库，不得 import requests / yaml / rich 等。
路径统一基于本文件所在目录解析，与运行时的 cwd 无关。
"""

import base64
import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TOKEN_PATH = BASE_DIR / "data" / "session_cache.json"
HISTORY_DIR = BASE_DIR / ".history" / "data"


# ---------------------------------------------------------------------------
# JWT 解析
# ---------------------------------------------------------------------------
def normalize_token(raw):
    """清洗 token 文本：去空白、去 `token:` / `token=` 前缀、去包裹引号。"""
    if not raw or not isinstance(raw, str):
        return ""
    tok = raw.strip()
    lower = tok.lower()
    for prefix in ("token:", "token="):
        if lower.startswith(prefix):
            tok = tok[len(prefix):].strip()
            lower = tok.lower()
            break
    if len(tok) > 1 and tok[0] in ('"', "'") and tok[-1] in ('"', "'"):
        tok = tok[1:-1].strip()
    return tok


def jwt_decode(token):
    """解码 JWT 的 payload 部分，返回 dict；任何异常返回 None。"""
    tok = normalize_token(token)
    if not tok:
        return None
    parts = tok.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1]
        payload += "=" * (4 - len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def jwt_exp(token):
    """返回 JWT 的 exp（epoch 秒），无法解析返回 None。"""
    data = jwt_decode(token)
    if not data:
        return None
    exp = data.get("exp")
    if not isinstance(exp, (int, float)):
        return None
    return int(exp)


def token_student_id(token):
    """从 JWT 提取学号（优先顶层 studentId，其次 userInfo 字符串）。"""
    data = jwt_decode(token)
    if not data:
        return None
    sid = data.get("studentId")
    if sid:
        return str(sid)
    ui = data.get("userInfo")
    if isinstance(ui, str):
        try:
            info = json.loads(ui)
            if isinstance(info, dict) and info.get("studentId"):
                return str(info["studentId"])
        except Exception:
            pass
    elif isinstance(ui, dict) and ui.get("studentId"):
        return str(ui["studentId"])
    return None


def token_is_valid(token, early_seconds=300):
    """token 是否有效（未过期，且留出 early_seconds 提前量）。"""
    exp = jwt_exp(token)
    if not exp:
        return False
    return time.time() < exp - early_seconds


# ---------------------------------------------------------------------------
# 缓存读写（原子写，轮询方不会读到半个文件）
# ---------------------------------------------------------------------------
def atomic_write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_cached_token(early_seconds=300):
    """读取缓存 token；缺失/损坏/过期一律返回空串。"""
    if not TOKEN_PATH.exists():
        return ""
    raw_text = TOKEN_PATH.read_text(encoding="utf-8")
    try:
        data = json.loads(raw_text)
        tok = data.get("token", "")
    except Exception:
        tok = _extract_token_from_text(raw_text)
    tok = normalize_token(tok)
    if not token_is_valid(tok, early_seconds):
        return ""
    return tok


def _extract_token_from_text(raw_text):
    """容错：文件损坏时扫描文本，取第一个可解码的 JWT 形状字符串。"""
    import re
    for m in re.finditer(r'[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+', raw_text):
        tok = m.group(0)
        if jwt_exp(tok):
            return tok
    return ""


def current_best_exp(early_seconds=300):
    """当前缓存中有效 token 的 exp；无有效 token 返回 0。"""
    tok = read_cached_token(early_seconds)
    if not tok:
        return 0
    return jwt_exp(tok) or 0


def save_token(token):
    """原子保存 token 并重新计算 expires_at；返回清洗后的 token。"""
    tok = normalize_token(token)
    if not tok or not jwt_exp(tok):
        return ""  # 非 JWT 输入直接拒绝，避免覆盖有效缓存
    exp = jwt_exp(tok)
    expires_at = datetime.fromtimestamp(exp).strftime("%Y-%m-%dT%H:%M:%S") if exp else ""
    atomic_write_json(TOKEN_PATH, {"token": tok, "expires_at": expires_at})
    return tok


def snapshot_to_history(token):
    """写入 .history/data/session_cache_<时间戳>.json 做审计留痕。"""
    tok = normalize_token(token)
    if not tok:
        return None
    exp = jwt_exp(tok)
    expires_at = datetime.fromtimestamp(exp).strftime("%Y-%m-%dT%H:%M:%S") if exp else ""
    path = HISTORY_DIR / f"session_cache_{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
    try:
        atomic_write_json(path, {"token": tok, "expires_at": expires_at})
    except Exception:
        return None
    return path


def env_student_id():
    """从环境变量读取期望学号（addon 用它校验是否抓到本账号）。"""
    return os.environ.get("NJUPT_STUDENT_ID", "").strip() or None
