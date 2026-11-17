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
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import yaml

import token_util
import runtime
import scheduling

class CaptureError(RuntimeError):
    pass


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config" / "config.yaml"
ADDON_PATH = BASE_DIR / "token_capture_addon.py"
MITM_BIN_DIRS = [
    Path("D:/Program Files/mitmproxy/bin"),
    Path("C:/Program Files/mitmproxy/bin"),
    Path("C:/Program Files (x86)/mitmproxy/bin"),
]
MITM_ALLOW_HOST = r"^wechat\.njupt\.edu\.cn$"
_INTERNET_SETTINGS = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


# ---------------------------------------------------------------------------
# 环境探测
# ---------------------------------------------------------------------------
@contextmanager
def capture_session():
    """Only one capture process may temporarily own the user's system proxy."""
    if os.name != "nt":
        yield
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    ctypes.set_last_error(0)
    handle = kernel.CreateMutexW(None, False, "Local\\NJUPTBadmintonTokenCapture")
    error = ctypes.get_last_error()
    if not handle:
        raise CaptureError("无法创建登录获取会话")
    try:
        if error == 183:  # ERROR_ALREADY_EXISTS
            raise CaptureError("另一个窗口正在获取登录，请先取消或等待它结束")
        yield
    finally:
        kernel.CloseHandle(handle)


def wait_for_proxy_ready(proc, port, stop_event=None, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        runtime.check_stop("capture")
        if stop_event is not None and stop_event.is_set():
            return False
        if proc.poll() is not None:
            raise CaptureError("抓包组件启动失败，请检查 data/mitmdump.log")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.1)
    raise CaptureError("抓包组件未就绪，请检查 data/mitmdump.log")


def find_mitmdump():
    # GUI portable builds include the library in this interpreter, including
    # when launched through pythonw.exe. Never execute a .bat through a shell.
    import importlib.util
    if importlib.util.find_spec("mitmproxy") is not None:
        return sys.executable
    # 1. 打包内置的便携版（dist/mitmproxy/bin/）
    bundled = BASE_DIR / "mitmproxy" / "bin"
    for name in ("mitmdump.exe", "mitmdump"):
        p = bundled / name
        if p.exists():
            return str(p)
    # 2. 本机常见安装路径
    for base in MITM_BIN_DIRS:
        for name in ("mitmdump.exe", "mitmdump"):
            p = base / name
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
    args = ["--listen-port", str(port), "--ssl-insecure",
            "--listen-host", env.get("NJUPT_LISTEN_HOST", "127.0.0.1"),
            "--allow-hosts", MITM_ALLOW_HOST, "-s", str(ADDON_PATH)]
    if exe == sys.executable:
        cmd = [exe, "-c", "from mitmproxy.tools.main import mitmdump; mitmdump()"] + args
    else:
        cmd = [exe] + args
    logf = open(log_path, "ab")
    try:
        proc = subprocess.Popen(cmd, env=env, stdout=logf, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
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
def wait_for_token(initial_exp, timeout, required_until=0, initial_token=None,
                   *, stop_event=None, initial_mtime_ns=None, expected_student_id=""):
    """等待有效期足够的新 token；强制刷新也允许相同 exp 的不同凭据。"""
    deadline = time.monotonic() + timeout
    last_report = 0.0
    while time.monotonic() < deadline:
        runtime.check_stop("capture")
        if stop_event is not None and stop_event.is_set():
            return ""
        cur = token_util.current_best_exp()
        candidate = token_util.read_cached_token()
        changed = candidate and initial_token is not None and candidate != initial_token
        try:
            observed = (initial_mtime_ns is not None and
                        token_util.TOKEN_PATH.stat().st_mtime_ns != initial_mtime_ns)
        except FileNotFoundError:
            observed = False
        account_matches = (not expected_student_id or
                           token_util.token_student_id(candidate) == expected_student_id)
        if candidate and account_matches and (cur > initial_exp or changed or observed) and cur > required_until:
            return candidate
        now = time.monotonic()
        if now - last_report >= 10:
            last_report = now
            print(f"    ... 等待捕获中（还剩 {int(deadline - now)}s）请在微信里打开南邮小程序并进入场地页")
        if stop_event is None:
            runtime.pause(1, "capture")
        else:
            stop_event.wait(0.2)
    return ""


def print_instructions(mode, port):
    if mode == "phone":
        ip = local_ip()
        print(f"[*] 系统代理已开启（供手机使用 {ip}:{port}）")
        print(f"    手机设置: 同局域网下把 WiFi 代理指向 {ip}:{port}（iOS Shadowrocket 可加规则）")
        print("    然后打开南邮小程序 → 进入场地页，等待自动捕获...")
    else:
        print(f"[*] 系统代理已开启 (127.0.0.1:{port})")
        print("    请在电脑微信打开南邮小程序 → 进入场地页，等待自动捕获...")
        print("    （若一直无法获取，再重启电脑微信后重试）")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def recovery_path():
    return runtime.DATA_DIR / "proxy_recovery.json"


def _recover_proxy():
    path = recovery_path()
    if not path.exists():
        return False
    record = runtime.read_json(path)
    if not isinstance(record, dict) or not isinstance(record.get("previous"), list) or len(record["previous"]) != 2:
        raise RuntimeError("代理恢复文件损坏，请检查 Windows 代理设置；原文件已保留")
    previous = tuple(record["previous"])
    current = _read_proxy_state()
    if current is None:
        raise RuntimeError("无法读取当前代理，恢复记录已保留")
    # 只有当前仍是本程序设置的代理才覆盖，保留用户/其他代理软件后续修改。
    if current == (1, record.get("managed_proxy")):
        _restore_proxy_state(previous)
        print("[+] 已恢复抓取前的网络代理")
    elif current != previous:
        print("[*] 代理已被其他程序修改，保留当前设置")
    path.unlink()
    return True


def recover_proxy():
    with runtime.single_instance("capture"):
        recovered = _recover_proxy()
        if not recovered:
            print("[*] 没有待恢复的代理记录")
        return recovered


def wait_proxy_ready(proc, port, timeout=8):
    return wait_for_proxy_ready(proc, port, timeout=timeout)


@contextmanager
def proxy_session(port, env, switch_system=True, stop_event=None):
    previous = _read_proxy_state() if switch_system else None
    if switch_system and os.name == "nt" and previous is None:
        raise RuntimeError("无法读取原代理状态，取消切换")
    proc = None
    recorded = False
    try:
        proc = spawn_mitmdump(port, env)
        if stop_event is None:
            wait_proxy_ready(proc, port)
        elif not wait_for_proxy_ready(proc, port, stop_event):
            raise runtime.Cancelled("已取消登录获取")
        if switch_system and previous is not None:
            token_util.atomic_write_json(recovery_path(), {
                "previous": previous, "managed_proxy": f"127.0.0.1:{port}",
                "owner_pid": os.getpid(),
            })
            recorded = True
            set_system_proxy(True, port)
        yield proc
    finally:
        try:
            if recorded:
                _recover_proxy()
        finally:
            _stop_mitmdump(proc)


def run_refresh(cfg, timeout, force=False, required_until=0, *, stop_event=None):
    with capture_session(), runtime.single_instance("capture"):
        runtime.clear_stop("capture")
        _recover_proxy()
        try:
            return _run_refresh(cfg, timeout, force, required_until, stop_event)
        except runtime.Cancelled:
            return 130


def _run_refresh(cfg, timeout, force=False, required_until=0, stop_event=None):
    tc = cfg.get("token_capture", {}) or {}
    port = find_free_port(tc.get("mitm_port", 8080))
    if port != tc.get("mitm_port", 8080):
        print(f"[!] 默认端口 {tc.get('mitm_port', 8080)} 被占用（Fiddler 可能仍在运行），改用 {port}")

    initial = token_util.current_best_exp()
    initial_token = token_util.read_cached_token()
    sid = str((cfg.get("auth") or {}).get("student_id") or "")
    account_matches = not sid or token_util.token_student_id(initial_token) == sid
    if initial > max(0, required_until) and account_matches and not force:
        print("[+] 已有有效 token，无需刷新")
        return 0

    phone = tc.get("mode", "pc_wechat") == "phone"
    try:
        initial_mtime_ns = token_util.TOKEN_PATH.stat().st_mtime_ns
    except FileNotFoundError:
        initial_mtime_ns = 0
    env = {**os.environ, "NJUPT_STUDENT_ID": sid, "NJUPT_FORCE_TOKEN": "1" if force else "0",
           "NJUPT_LISTEN_HOST": "0.0.0.0" if phone else "127.0.0.1"}
    with proxy_session(port, env, switch_system=not phone, stop_event=stop_event):
        print_instructions(tc.get("mode", "pc_wechat"), port)
        tok = wait_for_token(initial, timeout, required_until, initial_token,
                             stop_event=stop_event, initial_mtime_ns=initial_mtime_ns,
                             expected_student_id=sid)
        if stop_event is not None and stop_event.is_set():
            return 130
        if tok:
            exp = token_util.jwt_exp(tok)
            exp_s = datetime.fromtimestamp(exp).strftime("%H:%M:%S") if exp else "?"
            print(f"[+] 新 token 已捕获并保存（有效期至今天 {exp_s}）")
            return 0
        print("[-] 超时未捕获到新 token。请检查：")
        print("    1) 是否已打开南邮小程序并进入场地页（仅打开聊天界面不产生流量）")
        print("    2) 若还是不行，重启电脑微信再试一次（可排除代理缓存问题）")
        print(f"    3) 运行 python capture_token.py --check 诊断；详细日志见 data/mitmdump.log")
        return 1


# ---------------------------------------------------------------------------
# 诊断
# ---------------------------------------------------------------------------
def ca_is_trusted():
    """检查 mitmproxy CA 是否在 Windows 用户信任存储中。"""
    try:
        from certificate_manager import is_trusted
        return is_trusted()
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
    ca_pem = Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"
    try:
        with runtime.single_instance("capture"):
            runtime.clear_stop("capture")
            _recover_proxy()
            with proxy_session(port, {**os.environ, "NJUPT_STUDENT_ID": ""}, switch_system=False):
                import requests
                proxies = {"http": f"http://127.0.0.1:{port}", "https": f"http://127.0.0.1:{port}"}
                r = requests.get(
                    "https://wechat.njupt.edu.cn/mini_program/v4/venue/user/types",
                    proxies=proxies, verify=str(ca_pem), timeout=timeout)
                return r.status_code == 200
    except Exception:
        return False


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
    p = argparse.ArgumentParser(description="自动抓取并刷新预约 token")
    p.add_argument("--wait", type=int, default=None, help="等待捕获秒数（默认取配置 timeout_seconds）")
    p.add_argument("--refresh", action="store_true", help="强制刷新（即使已有有效 token）")
    p.add_argument("--force", action="store_true", help="强制刷新（同 --refresh）")
    p.add_argument("--check", action="store_true", help="环境诊断")
    p.add_argument("--install-cert", action="store_true", help="安装 mitmproxy CA 到系统信任（无需管理员）")
    p.add_argument("--port", type=int, default=None, help="监听端口")
    p.add_argument("--recover-proxy", action="store_true", help="恢复异常退出后遗留的系统代理")
    p.add_argument("--stop", action="store_true", help="停止登录抓取并恢复代理")
    p.add_argument("--scheduled", action="store_true", help="按预约日与今天跳过规则执行登录提醒")
    args = p.parse_args(argv)

    if args.stop:
        runtime.request_stop("capture")
        return 0
    if args.recover_proxy:
        try:
            recover_proxy()
            return 0
        except (RuntimeError, OSError) as e:
            print(f"[-] {e}")
            return 1
    cfg = load_config()

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
    try:
        required_until = 0
        if args.scheduled:
            booking_cfg = cfg.get("booking") or {}
            reason = scheduling.skip_reason(booking_cfg)
            if reason:
                print(f"[*] {reason}")
                return 0
            opts = scheduling.settings(booking_cfg)
            required_until = scheduling.target_time(booking_cfg).timestamp() + opts["retry_window_seconds"] + opts["token_margin_seconds"]
            if token_util.current_best_exp() <= required_until:
                runtime.notify("请刷新预约登录", "打开电脑微信的小程序场地页，程序将自动捕获登录", cfg)
        return run_refresh(cfg, timeout, force=force, required_until=required_until)
    except Exception as e:
        print(f"[-] 无法刷新 token: {e}")
        print("    请确认已安装 mitmproxy，或参考 README 手动抓取 token")
        return 1


if __name__ == "__main__":
    sys.exit(main())
