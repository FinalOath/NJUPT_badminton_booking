"""本地 Tk 操作窗口；后台进程执行预约，关闭窗口不会强杀正在提交的请求。"""
import os
import queue
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import yaml

import book
import configure
import runtime
import scheduling
import token_util

BASE_DIR = Path(__file__).resolve().parent


def load_settings():
    path = configure.CONFIG_PATH
    if not path.exists():
        path = BASE_DIR / "config" / "config.yaml.template"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


class Dashboard:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk, messagebox
        self.tk, self.ttk, self.messagebox, self.root = tk, ttk, messagebox, root
        self.events, self.child, self.busy = queue.Queue(), None, False
        self.closing = False
        self.cfg = load_settings()
        self.available = []
        booking = self.cfg.get("booking") or {}
        self.targets = list(booking.get("targets") or [])
        self.backups = list(booking.get("fallback_targets") or [])
        root.title("南邮羽毛球预约")
        root.geometry("1080x820")
        root.minsize(920, 720)
        root.protocol("WM_DELETE_WINDOW", self.close)
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("TButton", padding=(8, 5))
        frame = ttk.Frame(root, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="羽毛球预约", style="Title.TLabel").pack(anchor="w")
        self.login_text = tk.StringVar(value="正在读取登录状态…")
        self.status_text = tk.StringVar(value="就绪")
        ttk.Label(frame, textvariable=self.login_text).pack(anchor="w", pady=(8, 3))
        ttk.Label(frame, textvariable=self.status_text, wraplength=1000).pack(anchor="w", pady=(0, 8))
        settings_frame = ttk.LabelFrame(frame, text="预约设置", padding=10)
        settings_frame.pack(fill="x")
        self.student = tk.StringVar(value=(self.cfg.get("auth") or {}).get("student_id", ""))
        self.location = tk.StringVar(value=booking.get("location", "仙林") or "不限")
        self.start_time = tk.StringVar(value=booking.get("schedule_time", "12:00"))
        self.weekdays = tk.StringVar(value=",".join(map(str, booking.get("weekdays", range(1, 8)))))
        self.mode = tk.StringVar(value=book.FALLBACK_MODES.get(booking.get("fallback_mode", "strict"), "仅已选场次"))
        for col, (label, var, width) in enumerate((("学号", self.student, 17), ("开抢时间", self.start_time, 8), ("预约日（1–7）", self.weekdays, 18))):
            ttk.Label(settings_frame, text=label).grid(row=0, column=col * 2, sticky="w", padx=4)
            ttk.Entry(settings_frame, textvariable=var, width=width).grid(row=0, column=col * 2 + 1, padx=6)
        ttk.Label(settings_frame, text="校区").grid(row=1, column=0, pady=8)
        ttk.Combobox(settings_frame, textvariable=self.location, values=["仙林", "三牌楼", "不限"], width=14, state="readonly").grid(row=1, column=1)
        ttk.Label(settings_frame, text="顺延规则").grid(row=1, column=2)
        ttk.Combobox(settings_frame, textvariable=self.mode, values=list(book.FALLBACK_MODES.values()), width=16, state="readonly").grid(row=1, column=3, columnspan=2, sticky="w")
        ttk.Button(settings_frame, text="保存设置", command=self.save).grid(row=1, column=5)
        ttk.Label(settings_frame, text="预约日：1=周一，7=周日。主选优先；空目标不会预约。更改计划后需重新安装任务。").grid(row=2, column=0, columnspan=6, sticky="w")
        selection = ttk.Frame(frame)
        selection.pack(fill="both", expand=True, pady=10)
        left = ttk.LabelFrame(selection, text="当天场次", padding=8)
        left.pack(side="left", fill="both", expand=True)
        ttk.Button(left, text="读取场次（只读）", command=self.fetch_slots).pack(anchor="w")
        self.table = ttk.Treeview(left, columns=("court", "time", "state"), show="headings", height=9)
        for col, label, width in (("court", "场地", 240), ("time", "时间", 110), ("state", "当前状态", 90)):
            self.table.heading(col, text=label)
            self.table.column(col, width=width)
        scroll = ttk.Scrollbar(left, command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.table.pack(fill="both", expand=True, pady=5)
        add = ttk.Frame(left)
        add.pack(fill="x")
        ttk.Button(add, text="加入主选 →", command=lambda: self.add_selected(False)).pack(side="left")
        ttk.Button(add, text="加入备选 →", command=lambda: self.add_selected(True)).pack(side="left", padx=8)
        right = ttk.Frame(selection)
        right.pack(side="right", fill="both", padx=(12, 0))
        self.primary_list = self.make_list(right, "主选（从上到下）", self.targets)
        self.backup_list = self.make_list(right, "指定备选", self.backups)
        self.refresh_lists()
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=4)
        for label, cmd in (
            ("刷新登录", lambda: self.launch("capture_token.py", "--refresh")),
            ("预览候选", lambda: self.launch("book.py", "--test", "--now")),
            ("开始预约", lambda: self.launch("book.py")),
            ("停止", self.stop), ("今天跳过", self.skip),
            ("取消今天跳过", self.unskip), ("恢复网络", lambda: self.launch("capture_token.py", "--recover-proxy", save=False)),
        ):
            ttk.Button(actions, text=label, command=cmd).pack(side="left", padx=(0, 5))
        tools = ttk.Frame(frame)
        tools.pack(fill="x", pady=4)
        ttk.Button(tools, text="已核对订单，解除暂停", command=self.acknowledge).pack(side="left")
        ttk.Button(tools, text="查看上次订单", command=self.show_order).pack(side="left", padx=5)
        ttk.Button(tools, text="安装 / 更新计划任务", command=self.install_tasks).pack(side="left")
        ttk.Button(tools, text="查看任务计划", command=self.show_plan).pack(side="left", padx=5)
        ttk.Label(frame, text="登录时请手动打开电脑微信 → 小程序 → 场地页。预约成功后请到小程序核对并支付。").pack(anchor="w", pady=(8, 3))
        self.log = tk.Text(frame, height=9, wrap="word", font=("Microsoft YaHei UI", 9), state="disabled")
        self.log.pack(fill="both", expand=True)
        self.poll()
        # 自动恢复上次异常退出遗留的代理；正在抓取时不会争抢锁。
        from capture_token import recovery_path, recover_proxy
        if recovery_path().exists() and not runtime.is_running("capture"):
            try:
                recover_proxy()
                self.append("已检查上次代理恢复记录。")
            except (RuntimeError, OSError) as e:
                self.append(f"网络恢复尚未完成：{e}")

    def make_list(self, parent, title, values):
        box = self.ttk.LabelFrame(parent, text=title, padding=6)
        box.pack(fill="both", expand=True, pady=3)
        listing = self.tk.Listbox(box, width=38, height=5, exportselection=False)
        listing.pack(fill="both", expand=True)
        buttons = self.ttk.Frame(box)
        buttons.pack(fill="x")
        self.ttk.Button(buttons, text="上移", command=lambda: self.move(listing, values, -1)).pack(side="left")
        self.ttk.Button(buttons, text="下移", command=lambda: self.move(listing, values, 1)).pack(side="left")
        self.ttk.Button(buttons, text="移除", command=lambda: self.remove(listing, values)).pack(side="left")
        return listing

    def refresh_lists(self):
        for listing, values in ((self.primary_list, self.targets), (self.backup_list, self.backups)):
            listing.delete(0, "end")
            for t in values:
                listing.insert("end", f"{t['court_name']}  {t['time']}")

    def move(self, listing, values, delta):
        selected = listing.curselection()
        if selected and 0 <= selected[0] + delta < len(values):
            pos, new = selected[0], selected[0] + delta
            values[pos], values[new] = values[new], values[pos]
            self.refresh_lists()
            listing.selection_set(new)

    def remove(self, listing, values):
        selected = listing.curselection()
        if selected:
            values.pop(selected[0])
            self.refresh_lists()

    def add_selected(self, backup):
        values = self.backups if backup else self.targets
        for key in self.table.selection():
            s = self.available[int(key)]
            item = {"court_name": s[1], "time": f"{s[2]}-{s[3]}"}
            if item not in values:
                values.append(item)
        self.refresh_lists()

    def save(self):
        if runtime.is_running("booking") or runtime.is_running("capture"):
            self.messagebox.showinfo("任务正在运行", "请先停止任务，再修改设置。")
            return False
        try:
            sid = self.student.get().strip()
            if not sid:
                raise ValueError("请填写学号")
            cfg = {**self.cfg, "auth": {**(self.cfg.get("auth") or {}), "student_id": sid}, "booking": {**(self.cfg.get("booking") or {})}}
            b = cfg["booking"]
            b.update(location="" if self.location.get() == "不限" else self.location.get(), schedule_time=self.start_time.get().strip(),
                     weekdays=[int(s.strip()) for s in self.weekdays.get().replace("，", ",").split(",")],
                     targets=list(self.targets), fallback_targets=list(self.backups),
                     fallback_mode=next(k for k, v in book.FALLBACK_MODES.items() if v == self.mode.get()))
            scheduling.settings(b)
            if self.targets:
                book.validate_booking(b)
            configure.save_config(cfg)
            self.cfg = cfg
            self.append("设置已保存。")
            return True
        except (ValueError, OSError) as e:
            self.messagebox.showerror("设置未保存", str(e))
            return False

    def append(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def fetch_slots(self):
        if self.busy or not self.save():
            return
        self.busy = True
        location = self.cfg["booking"]["location"]
        def work():
            try:
                token = book.load_token()
                if not token:
                    raise ValueError("请先刷新登录")
                slots = book.query_slots(token, datetime.now().strftime("%Y-%m-%d"))
                self.events.put(("slots", [s for s in slots if book.is_preferred_location(s[1], location)]))
            except Exception as e:
                self.events.put(("log", f"读取场次失败：{e}"))
            finally:
                self.events.put(("done", None))
        threading.Thread(target=work, daemon=True).start()

    def launch(self, script, *args, save=True):
        if self.busy:
            self.messagebox.showinfo("正在执行", "请等待当前操作结束，或点击停止。")
            return
        if save and not self.save():
            return
        self.start_process([sys.executable, "-u", str(BASE_DIR / script), *args])

    def start_process(self, command):
        try:
            self.child = subprocess.Popen(command, cwd=BASE_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                          text=True, encoding="utf-8", errors="replace",
                                          env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except OSError as e:
            self.messagebox.showerror("无法启动", str(e))
            return
        self.busy = True
        child = self.child
        def read_output():
            for line in child.stdout:
                self.events.put(("log", line))
            code = child.wait()
            self.events.put(("log", f"操作结束，退出码 {code}"))
            self.events.put(("done", None))
        threading.Thread(target=read_output, daemon=True).start()

    def stop(self):
        runtime.request_stop("booking")
        runtime.request_stop("capture")
        self.append("已请求停止；正在发送的请求会先返回结果。")

    def skip(self):
        scheduling.set_skip_today(True)
        self.stop()
        self.append("今天跳过已生效。")

    def unskip(self):
        scheduling.set_skip_today(False)
        self.append("已取消今天跳过。")

    def acknowledge(self):
        if self.messagebox.askyesno("核对订单", "请先在小程序核对未支付/已支付订单。确认已核对，并允许下一次启动重新预约？"):
            self.launch("book.py", "--acknowledge-order", save=False)

    def show_order(self):
        order = runtime.read_json(runtime.DATA_DIR / "last_order.json")
        self.messagebox.showinfo("上次订单", "\n".join(f"{k}: {v}" for k, v in order.items()) if order else "暂无本地订单摘要，请在小程序查看。")

    def show_plan(self):
        if self.save():
            plan = scheduling.task_plan(self.cfg)
            self.messagebox.showinfo("任务计划", f"预约日：{plan['weekdays']}\n登录提醒：{plan['refresh_time']}\n预约准备：{plan['booking_time']}\n仅在 Windows 用户已登录时运行，不补跑错过的任务。")

    def install_tasks(self):
        if self.busy or not self.save():
            return
        if os.name != "nt":
            self.messagebox.showinfo("计划任务", "当前仅支持 Windows 任务计划程序。")
            return
        self.start_process(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                            str(BASE_DIR / "scripts" / "install_task.ps1"), "-PythonPath", sys.executable])

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self.append(value)
                elif kind == "slots":
                    self.available = value
                    self.table.delete(*self.table.get_children())
                    for i, s in enumerate(value):
                        self.table.insert("", "end", iid=str(i), values=(s[1], f"{s[2]}-{s[3]}", "可用" if s[4] else "未开放/不可用"))
                elif kind == "done":
                    self.busy, self.child = False, None
        except queue.Empty:
            pass
        token = token_util.read_cached_token(early_seconds=0)
        exp = token_util.jwt_exp(token)
        if exp:
            try:
                opts = scheduling.settings(self.cfg.get("booking") or {})
                needed = max(datetime.now().timestamp(), scheduling.target_time(self.cfg.get("booking") or {}).timestamp()) + opts["retry_window_seconds"] + opts["token_margin_seconds"]
                covers = book.token_covers(token, needed, str((self.cfg.get("auth") or {}).get("student_id") or ""))
                self.login_text.set(f"登录有效至 {datetime.fromtimestamp(exp):%m-%d %H:%M} · {'可覆盖本次预约（尚需服务器验证）' if covers else '需刷新登录或核对账号'}")
            except ValueError:
                self.login_text.set("请检查预约设置")
        else:
            self.login_text.set("需要刷新登录 · 请打开电脑微信的小程序场地页")
        state = runtime.read_json(runtime.DATA_DIR / "booking_status.json", {})
        prefix = "运行中 · " if runtime.is_running("booking") else ""
        self.status_text.set(prefix + state.get("message", "尚未开始预约"))
        if self.closing and not self.busy and not runtime.is_running("booking") and not runtime.is_running("capture"):
            self.root.destroy()
            return
        self.root.after(500, self.poll)

    def close(self):
        if self.closing:
            return
        if runtime.is_running("booking") or runtime.is_running("capture") or self.busy:
            if not self.messagebox.askyesno("关闭窗口", "任务正在执行。关闭窗口将请求停止，等待中的网络请求会先返回。是否关闭？"):
                return
            self.stop()
            self.closing = True
            self.root.title("正在停止任务并恢复网络，请稍候…")
            return
        self.root.destroy()


def main():
    try:
        import tkinter as tk
    except ImportError:
        print("当前 Python 不包含 Tk 窗口组件。请使用包含 Tcl/Tk 的完整 Python 安装，或继续使用命令行入口。")
        return 1
    with runtime.single_instance("dashboard"):
        try:
            root = tk.Tk()
        except tk.TclError:
            print("无法初始化窗口组件。请检查 Python 的 Tcl/Tk 安装，或使用命令行入口。")
            return 1
        Dashboard(root)
        root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
