#!/usr/bin/env python3
"""
自动抓取并刷新预约 token 的 CLI（在仓库 .venv 环境运行）。

原理：启动 mitmdump（已随 mitmproxy 安装），把 Windows 系统代理切到本机端口
（与 Fiddler 相同机制，PC 微信可走代理），你在电脑微信/手机上打开南邮小程序
进入场地页，addon 自动从流量里提取新 token 写入 data/session_cache.json，
随后自动还原代理并退出。

用法:
  python capture_token.py --wait 300      # 等待 300 秒捕获新 token（计划任务用）
  python capture_token.py --refresh       # 强制刷新（即使已有有效 token）
  python capture_token.py --check         # 环境诊断（二进制/证书/端口/代理连通）
  python capture_token.py --port 8081     # 指定监听端口
"""

import argparse
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

import token_util

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config" / "config.yaml"
ADDON_PATH = BASE_DIR / "token_capture_addon.py"
MITM_BIN_DIR = Path("D:/Program Files/mitmproxy/bin")
_INTERNET_SETTINGS = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


# ---------------------------------------------------------------------------
# 环境探测
# ---------------------------------------------------------------------------
def find_mitmdump():
    # 1. 打包内置的便携版（dist/mitmproxy/bin/）
    bundled = BASE_DIR / "mitmproxy" / "bin"
    for name in ("mitmdump.exe", "mitmdump"):
        p = bundled / name
        if p.exists():
            return str(p)
    # 2. 本机常见安装路径
    for name in ("mitmdump.exe", "mitmdump"):
        p = MITM_BIN_DIR / name
        if p.exists():
            return str(p)
    # 3. PATH
    import shutil
    return shutil.which("mitmdump")


def find_free_port(hint=8080):
    for port in range(hint, hint + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise RuntimeError("没有可用的监听端口")


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Windows 系统代理开关（WinINET 注册表 + InternetSetOption 通知生效）
# ---------------------------------------------------------------------------
def _internet_set_option():
    try:
        import ctypes
        SETTINGS_CHANGED, REFRESH = 39, 37
        h = ctypes.windll.wininet.InternetOpenW("capture_token", 1, None, None, 0)
        if h:
            ctypes.windll.wininet.InternetSetOptionW(h, SETTINGS_CHANGED, None, 0)
            ctypes.windll.wininet.InternetSetOptionW(h, REFRESH, None, 0)
            ctypes.windll.wininet.InternetCloseHandle(h)
    except Exception:
        pass


def _read_proxy_state():
    if os.name != "nt":
        return None
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _INTERNET_SETTINGS)
        try:
            enable = winreg.QueryValueEx(key, "ProxyEnable")[0]
        except FileNotFoundError:
            enable = 0
        try:
            server = winreg.QueryValueEx(key, "ProxyServer")[0]
        except FileNotFoundError:
            server = ""
        winreg.CloseKey(key)
        return (enable, server)
    except OSError:
        return None


def set_system_proxy(enable, port):
    if os.name != "nt":
        return
    import winreg
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, _INTERNET_SETTINGS)
    winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1 if enable else 0)
    if enable:
        winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, f"127.0.0.1:{port}")
    winreg.CloseKey(key)
    _internet_set_option()


def _restore_proxy_state(state):
    if os.name != "nt" or state is None:
        return
    import winreg
    enable, server = state
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, _INTERNET_SETTINGS)
    winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, int(enable) if enable else 0)
    if server:
        winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, server)
    winreg.CloseKey(key)
    _internet_set_option()


# ---------------------------------------------------------------------------
# 启动 mitmdump
# ---------------------------------------------------------------------------
def spawn_mitmdump(port, env):
    exe = find_mitmdump()
    if not exe:
        raise RuntimeError("未找到 mitmdump，请先安装 mitmproxy")
    log_path = BASE_DIR / "data" / "mitmdump.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "--listen-port", str(port), "--ssl-insecure", "-s", str(ADDON_PATH)]
    logf = open(log_path, "ab")
    try:
        proc = subprocess.Popen(cmd, env=env, stdout=logf, stderr=subprocess.STDOUT)
        proc._logf = logf
        return proc
    except Exception:
        logf.close()
        raise


def _stop_mitmdump(proc):
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc._logf.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 等待捕获
# ---------------------------------------------------------------------------
def wait_for_token(initial_exp, timeout):
    """轮询缓存：出现 exp 高于 initial_exp 的新 token 即返回。"""
    deadline = time.time() + timeout
    last_report = 0.0
    while time.time() < deadline:
        cur = token_util.current_best_exp()
        if cur > initial_exp:
            return token_util.read_cached_token()
        now = time.time()
        if now - last_report >= 10:
            last_report = now
            print(f"    ... 等待捕获中（还剩 {int(deadline - now)}s）请在微信里打开南邮小程序并进入场地页")
        time.sleep(1)
    return ""


def print_instructions(mode, port):
    if mode == "phone":
        ip = local_ip()
        print(f"[*] 系统代理已开启（供手机使用 {ip}:{port}）")
        print("    手机设置: 同局域网下把 WiFi 代理指向 {ip}:{port}（iOS Shadowrocket 可加规则）")
        print("    然后打开南邮小程序 → 进入场地页，等待自动捕获...")
    else:
        print(f"[*] 系统代理已开启 (127.0.0.1:{port})")
        print("    如果电脑微信已经打开，请先【重启电脑微信】再打开南邮小程序")
        print("    然后进入场地页，等待自动捕获...")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run_refresh(cfg, timeout, force=False):
    tc = cfg.get("token_capture", {}) or {}
    port = find_free_port(tc.get("mitm_port", 8080))
    if port != tc.get("mitm_port", 8080):
        print(f"[!] 默认端口 {tc.get('mitm_port', 8080)} 被占用（Fiddler 可能仍在运行），改用 {port}")

    initial = token_util.current_best_exp()
    if initial > 0 and not force:
        print("[+] 已有有效 token，无需刷新")
        return 0

    sid = cfg.get("auth", {}).get("student_id", "")
    env = {**os.environ, "NJUPT_STUDENT_ID": sid}
    proc = None
    prev_proxy = _read_proxy_state()
    try:
        proc = spawn_mitmdump(port, env)
        set_system_proxy(True, port)
        print_instructions(tc.get("mode", "pc_wechat"), port)
        tok = wait_for_token(initial, timeout)
        if tok:
            exp = token_util.jwt_exp(tok)
            exp_s = datetime.fromtimestamp(exp).strftime("%H:%M:%S") if exp else "?"
            token_util.snapshot_to_history(tok)
            print(f"[+] 新 token 已捕获并保存（有效期至今天 {exp_s}）")
            return 0
        print("[-] 超时未捕获到新 token。请检查：")
        print("    1) 电脑微信是否已重启（改代理后需重启微信才生效）")
        print("    2) 是否已打开南邮小程序并进入场地页（仅打开聊天界面不产生流量）")
        print(f"    3) 运行 python capture_token.py --check 诊断；详细日志见 data/mitmdump.log")
        return 1
    finally:
        _stop_mitmdump(proc)
        _restore_proxy_state(prev_proxy)


# ---------------------------------------------------------------------------
# 诊断
# ---------------------------------------------------------------------------
def ca_is_trusted():
    """检查 mitmproxy CA 是否在 Windows 用户信任存储中。"""
    import subprocess
    try:
        r = subprocess.run(["certutil", "-user", "-store", "Root"],
                           capture_output=True, text=True, timeout=15)
        return "mitmproxy" in r.stdout.lower()
    except Exception:
        return False


def install_ca_cert():
    """把 mitmproxy CA 装入用户信任存储（不需要管理员）。返回 (ok, message)。"""
    import subprocess
    cer = Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.cer"
    pem = Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"
    if not cer.exists() and pem.exists():
        cer = pem
    if not cer.exists():
        return False, "未找到 CA 证书（先运行一次 mitmdump 生成到 ~/.mitmproxy）"
    try:
        r = subprocess.run(["certutil", "-user", "-addstore", "-f", "Root", str(cer)],
                           capture_output=True, text=True, timeout=15)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except Exception as e:
        return False, str(e)


def probe_proxy(cfg, timeout=20):
    """起代理并真实请求南邮后端，用 mitmproxy CA 校验证书链（证明信任生效）。"""
    tc = cfg.get("token_capture", {}) or {}
    port = find_free_port(tc.get("mitm_port", 8080))
    proc = None
    prev = _read_proxy_state()
    ca_pem = Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"
    try:
        proc = spawn_mitmdump(port, {**os.environ, "NJUPT_STUDENT_ID": ""})
        set_system_proxy(True, port)
        time.sleep(1.5)  # 等 mitmdump 绑定端口
        import requests
        proxies = {"http": f"http://127.0.0.1:{port}", "https": f"http://127.0.0.1:{port}"}
        r = requests.get(
            "https://wechat.njupt.edu.cn/mini_program/v4/venue/user/types",
            proxies=proxies, verify=str(ca_pem), timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False
    finally:
        _stop_mitmdump(proc)
        _restore_proxy_state(prev)


def cmd_check(cfg):
    print("=== capture_token 环境诊断 ===")
    ok = True
    exe = find_mitmdump()
    if exe:
        print(f"[OK]   mitmdump: {exe}")
    else:
        print("[FAIL] 未找到 mitmdump，请安装 mitmproxy")
        ok = False
    cert = Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"
    if cert.exists():
        print(f"[OK]   CA 证书文件: {cert}")
    else:
        print("[FAIL] 未找到 CA 证书（先运行一次 mitmdump 生成）")
        ok = False
    if ca_is_trusted():
        print("[OK]   CA 已受 Windows 信任（小程序可识别 mitmdump 证书）")
    else:
        print("[FAIL] CA 未被 Windows 信任！小程序会报'网络异常'。请运行: python capture_token.py --install-cert")
        ok = False
    hint = (cfg.get("token_capture", {}) or {}).get("mitm_port", 8080)
    try:
        port = find_free_port(hint)
        if port != hint:
            print(f"[WARN] 端口 {hint} 被占用（Fiddler 可能仍在运行），可用 {port}")
        else:
            print(f"[OK]   端口 {hint} 空闲")
    except RuntimeError as e:
        print(f"[FAIL] {e}")
        ok = False
    if probe_proxy(cfg):
        print("[OK]   代理链路连通，且 mitmdump 证书链有效（已用 CA 校验）")
    else:
        print("[FAIL] 代理链路不通（mitmdump 无法启动/无法访问后端/证书链无效）")
        ok = False
    print()
    print("结论:", "全部通过 [OK]" if ok else "存在问题 [X]")
    return 0 if ok else 1


def main(argv=None):
    cfg = load_config()
    p = argparse.ArgumentParser(description="自动抓取并刷新预约 token")
    p.add_argument("--wait", type=int, default=None, help="等待捕获秒数（默认取配置 timeout_seconds）")
    p.add_argument("--refresh", action="store_true", help="强制刷新（即使已有有效 token）")
    p.add_argument("--force", action="store_true", help="强制刷新（同 --refresh）")
    p.add_argument("--check", action="store_true", help="环境诊断")
    p.add_argument("--install-cert", action="store_true", help="安装 mitmproxy CA 到系统信任（无需管理员）")
    p.add_argument("--port", type=int, default=None, help="监听端口")
    args = p.parse_args(argv)

    if args.check:
        return cmd_check(cfg)

    if args.install_cert:
        if ca_is_trusted():
            print("[+] mitmproxy CA 已在信任列表中，无需重复安装")
            return 0
        ok, msg = install_ca_cert()
        print(msg)
        print("[+] CA 安装完成，重启电脑微信后再试" if ok else "[-] CA 安装失败")
        return 0 if ok else 1

    tc = cfg.get("token_capture", {}) or {}
    if args.port:
        tc = {**tc, "mitm_port": args.port}
        cfg = {**cfg, "token_capture": tc}
    timeout = args.wait if args.wait is not None else tc.get("timeout_seconds", 300)
    force = args.force or args.refresh
    return run_refresh(cfg, timeout, force=force)


if __name__ == "__main__":
    sys.exit(main())
