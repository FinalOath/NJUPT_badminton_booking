#!/usr/bin/env python3
"""
羽毛球预约配置 GUI（tkinter，鼠标点击即可完成配置）

交互流程：
  1. 顶部单选预约地点（仙林 / 三牌楼 / 不限），切换后自动刷新场次
  2. 左侧按时间段分组展示当天场地，双击场地（或选中后点「加入所选」）加入目标
  3. 右侧按点击顺序作为抢票优先级，可上移 / 下移 / 移除
  4. 点「保存配置」写回 config/config.yaml

复用 book.py（查询场次）与 configure.py（渲染配置），零额外依赖（tkinter 为标准库）。
"""

import sys
import copy
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

import yaml

import token_util
from capture_token import CaptureError
import certificate_manager
from book import pre_fetch_type_id, query_slots
from configure import CONFIG_PATH


LOCATIONS = [("仙林", "仙林"), ("三牌楼", "三牌楼"), ("不限", "")]


def load_config():
    if not CONFIG_PATH.exists():
        return None
    try:
        return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return None


class App:
    def __init__(self, root):
        self.root = root
        root.title("南邮羽毛球预约配置")
        width = min(1060, root.winfo_screenwidth() - 80)
        height = min(760, root.winfo_screenheight() - 100)
        root.geometry(f"{width}x{height}+30+30")
        root.minsize(min(900, width), min(600, height))

        self.cfg = load_config() or {}
        self.slots = []        # [(sid, name, start, end, status, date), ...]
        self.selected = []     # [(sid, name, start, end), ...] 按优先级
        self.loc = tk.StringVar(value="仙林")
        self._loading = False
        self._capturing = False
        self._closing = False
        self._cert_busy = False
        self._cert_info = None
        self._cert_trusted = False
        self._capture_stop = threading.Event()
        self._events = queue.Queue()
        self._last_token_check = 0
        root.protocol("WM_DELETE_WINDOW", self.close)

        self._build_ui()
        self._apply_cfg_location()
        self._apply_cfg_targets()
        self.check_token()
        self.check_certificate()
        self.root.after(100, self._poll_events)
        self.root.after(100, self.refresh_slots)

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_ui(self):
        onboarding = ttk.LabelFrame(self.root, text="首次使用：1 保存学号 → 2 检查并安装证书 → 3 获取登录", padding=8)
        onboarding.pack(fill="x", padx=8, pady=8)
        account = ttk.Frame(onboarding)
        account.pack(fill="x")
        ttk.Label(account, text="学号：").pack(side="left")
        self.student_id = tk.StringVar(value=str((self.cfg.get("auth") or {}).get("student_id") or ""))
        self.student_entry = ttk.Entry(account, textvariable=self.student_id, width=24)
        self.student_entry.pack(side="left")
        self.account_button = ttk.Button(account, text="保存学号", command=self.save_student_id)
        self.account_button.pack(side="left", padx=6)
        certrow = ttk.Frame(onboarding)
        certrow.pack(fill="x", pady=6)
        self.cert_check_button = ttk.Button(certrow, text="检查 / 准备证书", command=lambda: self.check_certificate(prepare=True))
        self.cert_check_button.pack(side="left")
        self.cert_install_button = ttk.Button(certrow, text="审查并安装证书", command=self.review_certificate, state="disabled")
        self.cert_install_button.pack(side="left", padx=6)
        self.cert_label = ttk.Label(certrow, text="尚未检查证书", wraplength=560)
        self.cert_label.pack(side="left")
        ttk.Label(onboarding, text="获取登录时：在电脑微信打开南邮小程序 → 进入场地页。成功、取消或超时后恢复原代理设置。",
                  wraplength=980).pack(anchor="w")

        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")

        ttk.Label(top, text="预约地点:").pack(side="left")
        for text, value in LOCATIONS:
            ttk.Radiobutton(
                top, text=text, value=value, variable=self.loc,
                command=self._on_location_change,
            ).pack(side="left", padx=6)

        self.student_label = ttk.Label(top, text="")
        self.student_label.pack(side="right")

        login = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        login.pack(fill="x")
        self.capture_button = ttk.Button(login, text="获取 / 刷新登录", command=self.capture_token)
        self.capture_button.pack(side="left")
        ttk.Button(login, text="检测登录状态", command=self.check_token).pack(side="left", padx=6)
        self.cancel_button = ttk.Button(login, text="取消获取", command=self._capture_stop.set, state="disabled")
        self.cancel_button.pack(side="left")
        self.token_label = ttk.Label(login, text="", wraplength=470)
        self.token_label.pack(side="left", padx=8)

        body = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        # 左侧：场地列表
        left = ttk.LabelFrame(body, text="当天场地（双击加入目标）", padding=6)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(left, columns=("time",), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="场地名称")
        self.tree.heading("time", text="时间段")
        self.tree.column("#0", width=300)
        self.tree.column("time", width=90, anchor="center")
        self.tree.tag_configure("free", foreground="black")
        self.tree.tag_configure("full", foreground="gray")
        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        # 右侧：已选目标
        right = ttk.LabelFrame(body, text="已选目标（顺序 = 抢票优先级）", padding=6)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        self.listbox = tk.Listbox(right, height=18, activestyle="dotbox")
        lvsb = ttk.Scrollbar(right, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=lvsb.set)
        self.listbox.grid(row=0, column=0, sticky="nsew", columnspan=2)
        lvsb.grid(row=0, column=2, sticky="ns")

        btns = ttk.Frame(right)
        btns.grid(row=1, column=0, columnspan=3, pady=6)
        ttk.Button(btns, text="加入所选", command=self._add_selected).pack(side="left", padx=2)
        ttk.Button(btns, text="上移", command=self._move_up).pack(side="left", padx=2)
        ttk.Button(btns, text="下移", command=self._move_down).pack(side="left", padx=2)
        ttk.Button(btns, text="移除", command=self._remove_selected).pack(side="left", padx=2)
        ttk.Button(btns, text="清空", command=self._clear_selected).pack(side="left", padx=2)

        # 底部
        bottom = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        bottom.pack(fill="x")
        self.status = ttk.Label(bottom, text="加载中...", foreground="blue", wraplength=500)
        self.status.pack(side="left")
        ttk.Button(bottom, text="保存配置", command=self.save_config).pack(side="right", padx=4)
        ttk.Button(bottom, text="刷新", command=self.refresh_slots).pack(side="right", padx=4)
        ttk.Button(bottom, text="退出", command=self.close).pack(side="right", padx=4)

    def save_student_id(self):
        if self._capturing or self._loading or self._cert_busy:
            self._set_status("请等待当前操作结束后再修改学号", "blue")
            return False
        sid = self.student_id.get().strip()
        if not sid or not sid.isascii() or not sid.isalnum():
            self._set_status("请输入只包含英文字母和数字的学号", "red")
            return False
        cfg = copy.deepcopy(self.cfg)
        cfg.setdefault("auth", {})["student_id"] = sid
        cfg.setdefault("booking", {}).setdefault("targets", [])
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            # Preserve all other fields, including settings created by older releases.
            CONFIG_PATH.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
        except OSError:
            self._set_status("学号保存失败，请把安装包解压到有写入权限的文件夹", "red")
            return False
        self.cfg = cfg
        self.student_id.set(sid)
        self._student_display()
        self.check_token()
        self._set_status("学号已保存；下一步检查证书，再获取登录", "green")
        return True

    def check_certificate(self, prepare=False):
        if self._cert_busy or self._capturing or self._closing:
            return
        self._start_certificate_job("prepare" if prepare else "check")

    def _start_certificate_job(self, action, fingerprint=None):
        self._cert_busy = True
        self.cert_check_button.config(state="disabled")
        self.cert_install_button.config(state="disabled")
        self.cert_label.config(text="正在处理证书，请稍候…")
        threading.Thread(target=self._certificate_worker, args=(action, fingerprint), daemon=False).start()

    def _certificate_worker(self, action, fingerprint):
        try:
            message = ""
            if action == "install":
                _ok, message = certificate_manager.install_reviewed_certificate(fingerprint)
            info = (certificate_manager.prepare_certificate() if action == "prepare"
                    else certificate_manager.inspect_certificate())
            trusted = certificate_manager.is_trusted(info) if info else False
            self._events.put(("certificate", (info, trusted, message)))
        except Exception as exc:
            self._events.put(("certificate_error", type(exc).__name__))

    def review_certificate(self):
        if self._cert_busy or self._capturing or not self._cert_info or self._closing:
            return
        info = self._cert_info
        detail = ("此根证书用于本机代理解密南邮小程序的 HTTPS 流量以获取登录凭证。\n"
                  "安装会将它加入当前 Windows 用户的受信任根证书库；拥有对应私钥的代理可以解密经它转发的 HTTPS 流量。\n"
                  "本程序仅拦截 wechat.njupt.edu.cn；请妥善保管 ~/.mitmproxy 下的私钥。\n\n"
                  f"主体：{info['subject']}\n颁发者：{info['issuer']}\n"
                  f"有效期：{info['not_before']} 至 {info['not_after']}\n"
                  f"SHA-256：{info['sha256']}\n文件：{info['path']}\n\n"
                  "确认安装此证书？如 Windows 再次询问，请核对后确认。")
        if messagebox.askyesno("审查根证书并确认安装", detail, parent=self.root):
            self._start_certificate_job("install", info["sha256"])

    def check_token(self):
        """检测本地缓存，不发送网络请求。"""
        sid = (self.cfg.get("auth") or {}).get("student_id", "")
        info = token_util.cached_token_status(sid)
        state = info["state"]
        messages = {"missing": "未获取登录，请点击“获取 / 刷新登录”",
                    "unreadable": "无法读取登录缓存，请检查文件权限",
                    "invalid": "登录缓存无效，请重新获取",
                    "expired": "登录已过期，请重新获取",
                    "wrong_account": "登录账号与配置学号不一致，请重新获取"}
        if state in ("valid", "expiring"):
            seconds = info["remaining_seconds"]
            try:
                expiry = datetime.fromtimestamp(info["expires_at"]).strftime("%m-%d %H:%M:%S")
                text = f"{'即将过期' if state == 'expiring' else '本地未过期'} · 剩余 {seconds // 60}分{seconds % 60}秒 · 到期 {expiry}（未验证服务器）"
            except (ValueError, OSError, OverflowError):
                text = "登录到期时间异常，请重新获取"
        else:
            text = messages[state]
        self.token_label.config(text=text, foreground="green" if state == "valid" else "darkorange" if state == "expiring" else "red")
        self._last_token_check = time.monotonic()
        return info

    def capture_token(self):
        if self._capturing or self._closing or self._cert_busy:
            return
        if self._loading:
            self._set_status("请等待场次查询结束后再获取登录", "blue")
            return
        if not (self.cfg.get("auth") or {}).get("student_id") or self.student_id.get().strip() != str(self.cfg["auth"]["student_id"]):
            self._set_status("请先输入并保存学号", "red")
            return
        if not self._cert_trusted:
            self._set_status("请先检查证书，并审查安装尚未受信任的证书", "red")
            return
        import capture_token
        if not capture_token.find_mitmdump():
            self._set_status("缺少抓包组件，请使用完整 GUI 绿色包；源码运行需安装 mitmproxy", "red")
            return
        self._capturing = True
        self._capture_stop.clear()
        self.capture_button.config(state="disabled")
        self.cancel_button.config(state="normal")
        self.account_button.config(state="disabled")
        self.student_entry.config(state="disabled")
        self._set_status("正在获取登录：请在电脑微信打开南邮小程序 → 场地页；最多等待 300 秒", "blue")
        threading.Thread(target=self._capture_worker, daemon=False).start()

    def _capture_worker(self):
        try:
            from capture_token import run_refresh
            code = run_refresh(self.cfg, 300, force=True, stop_event=self._capture_stop)
            self._events.put(("capture_done", code))
        except CaptureError as exc:
            self._events.put(("capture_error", str(exc)))
        except Exception as exc:
            # 异常类型足以定位失败；不把可能含 token 的异常正文展示在窗口中。
            self._events.put(("capture_error", type(exc).__name__))

    def _poll_events(self):
        try:
            while True:
                kind, value = self._events.get_nowait()
                if kind in ("capture_done", "capture_error"):
                    self._capturing = False
                    self.capture_button.config(state="normal")
                    self.cancel_button.config(state="disabled")
                    self.account_button.config(state="normal")
                    self.student_entry.config(state="normal")
                    info = self.check_token()
                    self._student_display()
                    if kind == "capture_error":
                        self._set_status(f"获取登录失败（{value}）；请检查证书、抓包组件与 app/data/mitmdump.log", "red")
                    elif value == 130:
                        self._set_status("已取消获取登录，代理清理流程已结束", "blue")
                    elif value == 0 and info["state"] == "valid":
                        self._set_status("登录已获取，正在刷新场次", "green")
                        if not self._closing:
                            self.refresh_slots()
                    else:
                        self._set_status("未获取到可用的新登录，请重试；必要时重启电脑微信", "red")
                elif kind == "slots":
                    slots, date, location = value
                    if location == self.loc.get():
                        self._apply_slots(slots, date)
                    else:
                        self._finalize_load()
                        self.refresh_slots()
                elif kind == "load_error":
                    self._finalize_load()
                    self._set_status(value, "red")
                elif kind in ("certificate", "certificate_error"):
                    self._cert_busy = False
                    self.cert_check_button.config(state="normal")
                    self._cert_info = None
                    self._cert_trusted = False
                    if kind == "certificate_error":
                        self.cert_label.config(text=f"证书检查失败（{value}），请使用完整 GUI 包后重试", foreground="red")
                    else:
                        self._cert_info, self._cert_trusted, message = value
                        label = ("此证书已受 Windows 信任" if self._cert_trusted else
                                 "证书已准备，请点击“审查并安装证书”" if self._cert_info else
                                 "未找到证书，请点击“检查 / 准备证书”")
                        self.cert_label.config(text=message or label,
                                               foreground="green" if self._cert_trusted else "darkorange")
                        self.cert_install_button.config(state="normal" if self._cert_info else "disabled")
        except queue.Empty:
            pass
        if self._closing and not self._capturing and not self._cert_busy:
            self.root.destroy()
            return
        if time.monotonic() - self._last_token_check >= 30:
            self.check_token()
        self.root.after(100, self._poll_events)

    def close(self):
        if self._capturing or self._cert_busy:
            self._closing = True
            self._capture_stop.set()
            self._set_status("正在结束后台操作并恢复代理，请稍候…", "blue")
            return
        self.root.destroy()

    # ------------------------------------------------------------------
    # 配置加载
    # ------------------------------------------------------------------
    def _apply_cfg_location(self):
        loc = (self.cfg.get("booking") or {}).get("location", "仙林")
        self.loc.set(loc if loc else "")

    def _apply_cfg_targets(self):
        """加载已保存目标；当天查不到的场次也保留，避免刷新登录时丢失选择。"""
        targets = (self.cfg.get("booking") or {}).get("targets") or []
        self._cfg_targets = []
        for t in targets:
            if isinstance(t, dict):
                self._cfg_targets.append((t.get("court_name", ""), t.get("time", "")))
                period = t.get("time", "").split("-")
                if len(period) == 2:
                    self.selected.append(("", t.get("court_name", ""), *period))
        self._refresh_listbox()
        self._student_display()

    def _student_display(self):
        sid = (self.cfg.get("auth") or {}).get("student_id", "")
        if not sid:
            tok = token_util.read_cached_token()
            if tok:
                sid = token_util.token_student_id(tok) or ""
        self.student_label.config(text=f"学号: {sid or '未设置'}")

    # ------------------------------------------------------------------
    # 场次加载（后台线程，避免阻塞 UI）
    # ------------------------------------------------------------------
    def _on_location_change(self):
        self._clear_tree()
        self.refresh_slots()

    def refresh_slots(self):
        if self._loading or self._capturing or self._closing:
            return
        self._loading = True
        self._set_status("加载场次中...", "blue")
        location = self.loc.get()
        threading.Thread(target=self._load_worker, args=(location,), daemon=True).start()

    def _load_worker(self, location):
        try:
            sid = (self.cfg.get("auth") or {}).get("student_id", "")
            if not sid:
                self._events.put(("load_error", "请先输入并保存学号，再获取 / 刷新登录"))
                return
            if token_util.cached_token_status(sid)["state"] == "wrong_account":
                self._events.put(("load_error", "登录账号与学号不一致，请获取 / 刷新登录"))
                return
            token = token_util.read_cached_token()
            if not token:
                self._events.put(("load_error", "无有效登录，请点击顶部“获取 / 刷新登录”"))
                return
            type_id = pre_fetch_type_id(token)
            if not type_id:
                self._events.put(("load_error", "未找到羽毛球类型，登录可能已失效，请重新获取"))
                return
            date = datetime.now().strftime("%Y-%m-%d")
            slots = query_slots(token, date, type_id)
            filtered = [s for s in slots if not location or location in s[1]]
            self._events.put(("slots", (filtered, date, location)))
        except Exception as e:
            self._events.put(("load_error", f"场次查询失败（{type(e).__name__}），请检查网络或重新获取登录"))

    def _finalize_load(self):
        self._loading = False

    def _apply_slots(self, slots, date):
        self._loading = False
        self.slots = slots
        self._populate_tree()
        self._restore_selected_from_cfg()
        free = sum(1 for s in slots if s[4])
        self._set_status(f"{date} · {len(slots)} 个场次，其中可预约 {free} 个", "green")

    def _clear_tree(self):
        for iid in self.tree.get_children():
            self.tree.delete(iid)

    def _populate_tree(self):
        self._clear_tree()
        groups = {}
        for idx, (_sid, _name, start, end, _status, _date) in enumerate(self.slots):
            groups.setdefault(f"{start}-{end}", []).append(idx)
        for period in sorted(groups):
            pid = self.tree.insert("", "end", text=period, values=(period,), open=True)
            for idx in groups[period]:
                _sid, name, start, end, status, _date = self.slots[idx]
                tag = "free" if status else "full"
                self.tree.insert(
                    pid, "end", iid=f"c:{idx}", text=name,
                    values=(f"{start}-{end}",), tags=(tag,),
                )

    def _restore_selected_from_cfg(self):
        """更新已选目标的场次 ID，保留当前编辑内容和顺序。"""
        by_key = {}
        for idx, (_sid, name, start, end, status, _date) in enumerate(self.slots):
            by_key[(name, f"{start}-{end}")] = (_sid, name, start, end)
        self.selected = [by_key.get((name, f"{start}-{end}"), (sid, name, start, end))
                         for sid, name, start, end in self.selected]
        self._refresh_listbox()

    # ------------------------------------------------------------------
    # 选择操作
    # ------------------------------------------------------------------
    def _slot_of_tree_selection(self):
        sel = self.tree.selection()
        if not sel:
            return None
        iid = sel[0]
        if not iid.startswith("c:"):
            return None
        idx = int(iid[2:])
        return self.slots[idx]

    def _on_tree_double_click(self, _event):
        slot = self._slot_of_tree_selection()
        if slot:
            self._append_slot(slot)

    def _add_selected(self):
        slot = self._slot_of_tree_selection()
        if slot:
            self._append_slot(slot)

    def _append_slot(self, slot):
        sid, name, start, end, status, _date = slot
        if not status:
            self._set_status(f"「{name} {start}-{end}」已满，无法加入", "red")
            return
        if slot[1:4] in [s[1:4] for s in self.selected]:
            self._set_status(f"「{name} {start}-{end}」已在目标中", "orange")
            return
        self.selected.append(slot[:4])
        self._refresh_listbox()
        self._set_status(f"已加入: {name} {start}-{end}", "green")

    def _refresh_listbox(self):
        self.listbox.delete(0, "end")
        for i, (_sid, name, start, end) in enumerate(self.selected, 1):
            self.listbox.insert("end", f"{i}. {name}  {start}-{end}")

    def _selected_index(self):
        sel = self.listbox.curselection()
        return sel[0] if sel else None

    def _move_up(self):
        i = self._selected_index()
        if i is not None and i > 0:
            self.selected[i], self.selected[i - 1] = self.selected[i - 1], self.selected[i]
            self._refresh_listbox()
            self.listbox.selection_set(i - 1)

    def _move_down(self):
        i = self._selected_index()
        if i is not None and i < len(self.selected) - 1:
            self.selected[i], self.selected[i + 1] = self.selected[i + 1], self.selected[i]
            self._refresh_listbox()
            self.listbox.selection_set(i + 1)

    def _remove_selected(self):
        i = self._selected_index()
        if i is not None:
            del self.selected[i]
            self._refresh_listbox()

    def _clear_selected(self):
        self.selected = []
        self._refresh_listbox()
        self._set_status("已清空目标", "blue")

    # ------------------------------------------------------------------
    # 保存
    # ------------------------------------------------------------------
    def save_config(self):
        if self._capturing or self._cert_busy:
            self._set_status("请等待当前操作结束后再保存配置", "blue")
            return
        targets = [
            {"court_name": name, "time": f"{start}-{end}"}
            for (_sid, name, start, end) in self.selected
        ]
        booking = self.cfg.setdefault("booking", {})
        booking["location"] = self.loc.get()
        booking["targets"] = targets
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(yaml.safe_dump(self.cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
        except Exception as e:
            messagebox.showerror("保存失败", str(e))
            return
        self._set_status(
            f"已保存: 地点「{self.loc.get() or '不限'}」 · 目标 {len(targets)} 个", "green")
        messagebox.showinfo("保存成功", f"已保存 {len(targets)} 个目标场次到 config/config.yaml")

    def _set_status(self, text, color):
        self.status.config(text=text, foreground=color)


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except OSError:
            pass
    try:
        root = tk.Tk()
    except tk.TclError as e:
        print(f"[-] 无法启动 GUI（缺少图形环境或 tkinter）: {e}")
        print("    提示: 请使用系统 Python（带 tkinter）运行，例如 `python gui.py`")
        return 1
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
