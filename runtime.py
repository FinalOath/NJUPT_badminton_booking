"""本机运行协调、状态与通知；不记录 token 或完整接口响应。"""
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from token_util import atomic_write_json

DATA_DIR = Path(__file__).resolve().parent / "data"


class AlreadyRunning(RuntimeError):
    pass


class Cancelled(RuntimeError):
    pass


@contextmanager
def single_instance(name):
    """操作系统文件锁：崩溃自动释放；保留锁文件避免删除/重建竞争。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_DIR / f"{name}.lock", "a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            raise AlreadyRunning(f"{name} 已在运行，请先停止现有任务") from e
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def is_running(name):
    try:
        with single_instance(name):
            return False
    except AlreadyRunning:
        return True


def request_stop(name):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / f"{name}.stop").touch()


def clear_stop(name):
    (DATA_DIR / f"{name}.stop").unlink(missing_ok=True)


def check_stop(name="booking"):
    if (DATA_DIR / f"{name}.stop").exists():
        raise Cancelled("已停止；如请求正在发送，请到小程序核对订单")


def pause(seconds, name="booking"):
    deadline = time.monotonic() + max(0, seconds)
    while True:
        check_stop(name)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.2))


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def report(state, message, **details):
    event = {"state": state, "message": message, "updated_at": datetime.now().isoformat(timespec="seconds"), **details}
    print(message, flush=True)
    try:
        atomic_write_json(DATA_DIR / "booking_status.json", event)
        log_dir = DATA_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / f"{datetime.now():%Y-%m-%d}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        print("[!] 本地状态写入失败，请保留窗口中的结果", flush=True)


def notify(title, message, cfg=None):
    opts = (cfg or {}).get("notify") or {}
    if opts.get("enable_sound", True) and os.name == "nt":
        try:
            import winsound
            winsound.MessageBeep()
        except Exception:
            pass
    if opts.get("enable_desktop", True):
        try:
            from plyer import notification
            notification.notify(title=title, message=message, app_name="羽毛球预约", timeout=15)
        except Exception:
            print("[!] 桌面通知不可用，请查看操作窗口/本地状态", flush=True)
