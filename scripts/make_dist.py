#!/usr/bin/env python3
"""
组装可分发压缩包（dist/ 目录）。

用法:
  python scripts/make_dist.py          # 纯代码包（~100KB）
  python scripts/make_dist.py --full   # 完整包：内置 mitmproxy（~90MB）

只复制分发必需文件，剔除个人数据（.history/logs/data/tests/node_modules/devtools 探测等）。
"""

import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
DIST = SRC / "dist"
MITM_SOURCES = [Path("D:/Program Files/mitmproxy"), Path("C:/Program Files/mitmproxy")]

CORE_FILES = [
    "book.py", "capture_token.py", "token_capture_addon.py", "token_util.py",
    "configure.py", "setup.py", "setup.bat", "requirements.txt",
]
CONFIG_FILES = ["config/config.yaml.template", "config/config.schema.yaml"]
SCRIPT_FILES = ["scripts/install_task.ps1", "scripts/setup_proxy.ps1",
                "scripts/run.bat", "scripts/allow_mitm.bat"]
DOC_FILES = ["使用说明.md", "软件下载清单.md"]
EXCLUDE_SUFFIXES = (".pyc",)


def main():
    full = "--full" in sys.argv
    if DIST.exists():
        shutil.rmtree(DIST)
    (DIST / "config").mkdir(parents=True)
    (DIST / "scripts").mkdir(parents=True)

    for f in CORE_FILES + CONFIG_FILES + SCRIPT_FILES + DOC_FILES:
        src = SRC / f
        if not src.exists():
            print(f"[跳过] {f}（不存在）")
            continue
        dst = DIST / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"[+] {f}")

    if full:
        mitm_src = next((m for m in MITM_SOURCES if (m / "bin" / "mitmdump.exe").exists()), None)
        if mitm_src:
            print(f"[+] 内置 mitmproxy: {mitm_src} -> dist/mitmproxy")
            shutil.copytree(mitm_src, DIST / "mitmproxy",
                            ignore=shutil.ignore_patterns("*.log", "__pycache__"))
        else:
            print("[!] 未找到 mitmproxy 安装，跳过内置（分发包需用户自装）")

    size = sum(p.stat().st_size for p in DIST.rglob("*") if p.is_file())
    print(f"\n分发包已生成: {DIST}")
    print(f"总大小: {size/1024/1024:.1f} MB")
    print("下一步可打包成 zip 分享: 右键 dist 文件夹 → 压缩为 zip")


if __name__ == "__main__":
    main()
