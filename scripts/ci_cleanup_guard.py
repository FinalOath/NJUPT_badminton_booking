#!/usr/bin/env python3
"""
CI 代码清理回归护栏（纯标准库，无第三方依赖）。

在 Linux / Windows 两个 CI 作业中运行，静态断言以下清理项未被破坏：
  任务1  删除死文件 scripts/install_task.bat；uninstall_task.bat 删除两个计划任务；
         allow_mitm.bat 不再停用防火墙服务 MpsSvc。
  任务2  MITM 收窄到南邮域名（capture_token.py 含 --allow-hosts / MITM_ALLOW_HOST）。
  任务3  book.py 抢票热循环含 0.5s 退避重试。
  任务4  删除死依赖 plyer，以及死配置 password/api/notify/base_url/desktop/console。
  额外   make_dist.py 分发包必须包含 uninstall_task.bat（文档引用了它）。

用法:
  python scripts/ci_cleanup_guard.py
"""
import pathlib
import sys

# 强制标准流使用 UTF-8，避免在 Windows 非 UTF-8 控制台/重定向（如 cp1252、cp936）下
# 打印中文报错时抛 UnicodeEncodeError，导致护栏误判失败。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main():
    errors = []

    # 任务1：死文件/脚本清理
    if (ROOT / "scripts" / "install_task.bat").exists():
        errors.append("scripts/install_task.bat 仍存在（应删除的死文件）")

    uninstall = (ROOT / "scripts" / "uninstall_task.bat").read_text(encoding="utf-8")
    for task in ("BadmintonTokenRefresh", "BadmintonBooking"):
        if f'"{task}"' not in uninstall:
            errors.append(f"uninstall_task.bat 未删除计划任务 {task}")

    allow = (ROOT / "scripts" / "allow_mitm.bat").read_text(encoding="utf-8")
    if "MpsSvc" in allow:
        errors.append("allow_mitm.bat 仍包含 sc stop MpsSvc")

    dist = (ROOT / "scripts" / "make_dist.py").read_text(encoding="utf-8")
    if "uninstall_task.bat" not in dist:
        errors.append("make_dist.py 分发包未包含 uninstall_task.bat（文档引用了它）")

    # 任务2：MITM 收窄到南邮域名
    capture = (ROOT / "capture_token.py").read_text(encoding="utf-8")
    if "--allow-hosts" not in capture or "MITM_ALLOW_HOST" not in capture:
        errors.append("capture_token.py 缺少 --allow-hosts / MITM_ALLOW_HOST（MITM 未收窄）")

    # 任务3：book.py 抢票热循环退避重试（0.5s 退避为唯一标记）
    book = (ROOT / "book.py").read_text(encoding="utf-8")
    if "time.sleep(0.5)" not in book:
        errors.append("book.py 抢票循环缺少 0.5s 退避重试")

    # 任务4：死依赖/死配置清理
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    if "plyer" in req:
        errors.append("requirements.txt 仍含未使用的 plyer")

    for fname in ("config/config.yaml.template", "config/config.schema.yaml"):
        text = (ROOT / fname).read_text(encoding="utf-8")
        for dead in ("password:", "api:", "notify:", "base_url", "enable_desktop", "enable_console"):
            if dead in text:
                errors.append(f"{fname} 仍含死配置字段 {dead!r}")

    # 正向校验：仍被 configure.py 读取的字段不能被误删
    for fname in ("config/config.yaml.template", "config/config.schema.yaml"):
        if "cas_mode" not in (ROOT / fname).read_text(encoding="utf-8"):
            errors.append(f"{fname} 误删了仍被 configure.py 读取的 cas_mode")

    if errors:
        print("cleanup guard FAILED:")
        for e in errors:
            print("  -", e)
        sys.exit(1)

    print("cleanup guard OK: 死文件/死依赖/死配置已清理，MITM 已收窄，book.py 已加退避重试")


if __name__ == "__main__":
    main()
