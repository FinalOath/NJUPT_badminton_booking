"""Windowed entrypoint with a startup error dialog instead of silent failure."""
from pathlib import Path
import sys
import traceback


def main():
    log_dir = Path(__file__).resolve().parent / "data"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "gui.log").open("a", encoding="utf-8", buffering=1) as log:
            sys.stdout = sys.stderr = log
            try:
                import gui
                return gui.main()
            except Exception:
                traceback.print_exc()
                raise
    except Exception:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("GUI 启动失败", f"请完整解压安装包到可写目录后重试。\n错误日志：{log_dir / 'gui.log'}", parent=root)
        root.destroy()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
