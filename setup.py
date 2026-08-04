#!/usr/bin/env python3
"""
一键配置向导：检查环境 → 引导填写配置 → 安装 CA 证书 → 注册计划任务。

用法:
  setup.bat               # 一键（含创建虚拟环境 + 装依赖）
  python setup.py         # 已装好依赖时直接运行

适用于两种分发方式:
  - 自装版: 用户自己装了 Python 和 mitmproxy 后运行本向导
  - 打包版: 解压后运行本向导（含内置 mitmproxy 时自动检测）
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config" / "config.yaml"
TEMPLATE_PATH = BASE_DIR / "config" / "config.yaml.template"


def ask(question, default=""):
    prompt = f"{question} [{default}]> " if default else f"{question}> "
    try:
        val = input(prompt).strip()
    except EOFError:
        return default
    return val or default


def ensure_config():
    """config.yaml 不存在则从模板创建；学号为空则引导填写（保留模板注释）。"""
    if not CONFIG_PATH.exists():
        if not TEMPLATE_PATH.exists():
            print("[FAIL] 缺少 config/config.yaml 模板")
            return False
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(TEMPLATE_PATH, CONFIG_PATH)
        print(f"[+] 已从模板创建 {CONFIG_PATH}")

    import yaml
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    sid = (cfg.get("auth") or {}).get("student_id", "")
    if not sid:
        print("[!] 请填写你的学号（南邮统一身份认证账号）:")
        sid = ask("学号")
        if not sid:
            print("[FAIL] 学号不能为空")
            return False
        text = CONFIG_PATH.read_text(encoding="utf-8")
        text = re.sub(r'student_id:\s*""', f'student_id: "{sid}"', text, count=1)
        CONFIG_PATH.write_text(text, encoding="utf-8")
        print(f"[+] 学号 {sid} 已写入 {CONFIG_PATH}")
    else:
        print(f"[OK] 学号已配置: {sid}")

    print("[*] 如需调整预约目标（场地/时间段），请编辑 config/config.yaml 的 booking.targets")
    return True


def check_mitmproxy():
    import capture_token
    exe = capture_token.find_mitmdump()
    if exe:
        print(f"[OK] mitmproxy: {exe}")
        return True
    print("[!] 未找到 mitmproxy。二选一：")
    print("    1) 内置版: 把 mitmproxy 目录放到本项目下（./mitmproxy/bin/mitmdump.exe）")
    print("    2) 自装版: 到 https://mitmproxy.org/downloads/ 下载 Windows 版安装")
    return False


def install_cert():
    import capture_token
    if capture_token.ca_is_trusted():
        print("[OK] mitmproxy CA 已受信任")
        return True
    print("[*] 安装 mitmproxy CA 证书（无需管理员）...")
    ok, msg = capture_token.install_ca_cert()
    print(msg)
    if ok:
        print("[OK] CA 证书已安装")
        return True
    print("[FAIL] CA 证书安装失败")
    return False


def setup_scheduled_tasks():
    if not ask("是否注册每日计划任务（11:30 刷新 token + 11:55 抢票）？[y/N]", "n").lower() in ("y", "yes"):
        print("[*] 跳过计划任务。可稍后运行: scripts\\install_task.ps1")
        return
    ps = BASE_DIR / "scripts" / "install_task.ps1"
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps)],
            capture_output=True, text=True, timeout=60)
        print(r.stdout)
        if r.returncode != 0:
            print("[WARN] 计划任务注册可能失败（管理员权限？），可手动运行 scripts\\install_task.ps1")
    except Exception as e:
        print(f"[WARN] 计划任务注册出错: {e}")


def main():
    print("=== 南邮羽毛球预约 配置向导 ===\n")
    print(f"[*] Python: {sys.version.split()[0]}")
    for mod in ("requests", "yaml", "rich"):
        try:
            __import__(mod)
        except ImportError:
            print(f"[!] 缺少依赖 {mod}，请先运行: setup.bat 或 pip install -r requirements.txt")
            return 1

    ok = ensure_config()
    if not ok:
        return 1
    has_mitm = check_mitmproxy()
    install_cert()
    if has_mitm:
        setup_scheduled_tasks()

    print("\n=== 完成！每日使用 ===")
    print(" 1) 每天 11:30 前后：在电脑微信打开南邮小程序 → 进入场地页（token 自动刷新）")
    print(" 2) 11:55 抢票任务自动运行；手动抢票: python book.py")
    print(" 3) 查看场次: python book.py --slots")
    print(" 4) 强制刷新 token: python capture_token.py --refresh")
    if not has_mitm:
        print("\n[!] 注意：mitmproxy 未就绪，token 自动刷新不可用。请先安装 mitmproxy 后重跑本向导。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
