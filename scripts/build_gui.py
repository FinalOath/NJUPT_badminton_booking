"""Build a self-contained Windows GUI ZIP, using a full Python with Tk.

Run: uv run --no-project --python 3.12 python scripts/build_gui.py
Build output never reads or copies user config, tokens, certificates or logs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent
APP_FILES = ("gui.py", "gui_start.py", "certificate_manager.py", "capture_token.py", "token_util.py",
             "token_capture_addon.py", "book.py", "configure.py", "setup.py")


def write_bat(path, text):
    path.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("ascii"))


def build(output):
    if sys.platform != "win32" or sys.version_info[:2] != (3, 12):
        raise RuntimeError("Build with Windows x64 full Python 3.12 (including tkinter)")
    import tkinter
    tkinter.Tcl().eval("info patchlevel")
    if output.exists():
        raise FileExistsError(f"Use a new output directory to preserve existing files: {output}")
    home = Path(sys.base_prefix).resolve()
    if home == output.resolve() or home in output.resolve().parents:
        raise ValueError("Output must be outside the Python runtime")
    output.mkdir(parents=True)
    app = output / "app"
    app.mkdir()
    runtime = output / "python"

    def ignore(directory, names):
        return [name for name in names if name in
                {"site-packages", "__pycache__", "Scripts", "include", "libs", "test", "tests"}
                or name.endswith((".pyc", ".pyo"))]

    shutil.copytree(home, runtime, ignore=ignore)
    python = runtime / "python.exe"
    subprocess.run(["uv", "pip", "install", "--python", str(python),
                    "--target", str(runtime / "Lib" / "site-packages"),
                    "-r", str(ROOT / "requirements-gui.txt")], check=True)
    # Isolated app runtime: ignore machine registry, user site packages and PYTHONPATH.
    (runtime / "python312._pth").write_text(
        ".\nLib\nDLLs\nLib/site-packages\n../app\nimport site\n", encoding="ascii")
    for name in APP_FILES:
        shutil.copy2(ROOT / name, app / name)
    config = app / "config"
    config.mkdir()
    # Fresh GUI builds intentionally have no real config or example booking targets.
    (config / "config.yaml.template").write_text(
        'auth:\n  student_id: ""\n  cas_mode: wecom\n'
        'booking:\n  location: 仙林\n  targets: []\n  schedule_time: "12:00"\n'
        'token_capture:\n  enabled: true\n  mitm_port: 8080\n  mode: pc_wechat\n', encoding="utf-8")
    launcher = ('@echo off\nsetlocal\ncd /d "%~dp0"\n'
                'if not exist "%~dp0python\\pythonw.exe" (\n'
                '  echo Missing runtime. Extract the complete ZIP first.\n'
                '  pause\n  exit /b 1\n)\n'
                'start "" "%~dp0python\\pythonw.exe" "%~dp0app\\gui_start.py"\n')
    for name in ("启动GUI.bat", "0-首次配置.bat"):
        write_bat(output / name, launcher)
    (output / "使用说明.txt").write_text(
        "南邮羽毛球 GUI 绿色版（Windows 10/11 x64）\n\n"
        "解压完整文件夹到有写入权限的位置，双击“启动GUI.bat”。无需安装 Python。\n"
        "1. 输入并保存学号。\n2. 点击“检查 / 准备证书”，再“审查并安装证书”。核对用途、主体和 SHA-256 后确认。\n"
        "3. 点击“获取 / 刷新登录”，在电脑微信打开南邮小程序，进入场地页。\n"
        "4. 登录状态显示本地到期时间；场次查询用于确认服务端是否接受此登录。\n"
        "获取过程可取消；结束后恢复原系统代理。窗口关闭时会等待后台清理结束。\n"
        "学号和预约配置保存在 app/config/config.yaml；token 在 app/data/session_cache.json。\n"
        "请勿分享上述个人文件，也不要与旧包的抓包脚本同时运行。\n"
        "本包提供配置和登录管理，不会自动提交预约或创建计划任务。\n"
        "证书私钥留在当前用户 ~/.mitmproxy 中，不随安装包分发。\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "TCL_LIBRARY", "TK_LIBRARY")}
    subprocess.run([str(python), "-c",
                    "import tkinter, requests, yaml, cryptography, gui; "
                    "from mitmproxy.tools.main import mitmdump; "
                    "r=tkinter.Tk(); r.withdraw(); r.update(); r.destroy(); print('portable smoke OK')"],
                   check=True, env=env, cwd=output)
    versions = subprocess.check_output(["uv", "pip", "freeze", "--python", str(python)], text=True)
    (output / "DEPENDENCIES.txt").write_text(versions, encoding="utf-8")
    manifest = {"python": sys.version.split()[0], "entrypoint": "启动GUI.bat",
                "source_sha256": {name: hashlib.sha256((app / name).read_bytes()).hexdigest() for name in APP_FILES}}
    (output / "BUILD.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    archive = output.with_suffix(".zip")
    if archive.exists():
        raise FileExistsError(archive)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for file in sorted(output.rglob("*")):
            if file.is_file() and "__pycache__" not in file.parts:
                z.write(file, Path(output.name) / file.relative_to(output))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    print(f"GUI package: {archive}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist-gui" / "badminton-gui-windows-x64")
    build(parser.parse_args().output.resolve())
