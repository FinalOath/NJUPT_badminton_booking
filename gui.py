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
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

import yaml

import token_util
from book import pre_fetch_type_id, query_slots
from configure import render_config, CONFIG_PATH


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
        root.geometry("900x580")

        self.cfg = load_config() or {}
        self.slots = []        # [(sid, name, start, end, status, date), ...]
        self.selected = []     # [(sid, name, start, end), ...] 按优先级
        self.loc = tk.StringVar(value="仙林")
        self._loading = False

        self._build_ui()
        self._apply_cfg_location()
        self._apply_cfg_targets()
        self.root.after(100, self.refresh_slots)

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_ui(self):
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
        self.status = ttk.Label(bottom, text="加载中...", foreground="blue")
        self.status.pack(side="left")
        ttk.Button(bottom, text="保存配置", command=self.save_config).pack(side="right", padx=4)
        ttk.Button(bottom, text="刷新", command=self.refresh_slots).pack(side="right", padx=4)
        ttk.Button(bottom, text="退出", command=self.root.destroy).pack(side="right", padx=4)

    # ------------------------------------------------------------------
    # 配置加载
    # ------------------------------------------------------------------
    def _apply_cfg_location(self):
        loc = (self.cfg.get("booking") or {}).get("location", "仙林")
        self.loc.set(loc if loc else "")

    def _apply_cfg_targets(self):
        """把已有 config 中的 targets 恢复为选中状态（仅加载当天能匹配到的）。"""
        targets = (self.cfg.get("booking") or {}).get("targets") or []
        self._cfg_targets = []
        for t in targets:
            if isinstance(t, dict):
                self._cfg_targets.append((t.get("court_name", ""), t.get("time", "")))
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
        if self._loading:
            return
        self._loading = True
        self._set_status("加载场次中...", "blue")
        location = self.loc.get()
        threading.Thread(target=self._load_worker, args=(location,), daemon=True).start()

    def _load_worker(self, location):
        try:
            token = token_util.read_cached_token()
            if not token:
                self.root.after(0, lambda: self._set_status(
                    "无有效 token，请先运行: python capture_token.py --wait 300", "red"))
                self.root.after(0, self._finalize_load)
                return
            type_id = pre_fetch_type_id(token)
            if not type_id:
                self.root.after(0, lambda: self._set_status("未找到羽毛球类型，token 可能已失效", "red"))
                self.root.after(0, self._finalize_load)
                return
            date = datetime.now().strftime("%Y-%m-%d")
            slots = query_slots(token, date, type_id)
            filtered = [s for s in slots if not location or location in s[1]]
            self.root.after(0, lambda: self._apply_slots(filtered, date))
        except Exception as e:
            self.root.after(0, lambda: self._set_status(f"加载失败: {e}", "red"))
            self.root.after(0, self._finalize_load)

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
        """把 config 中已有的目标映射回当天场次并恢复选中。"""
        self.selected = []
        by_key = {}
        for idx, (_sid, name, start, end, status, _date) in enumerate(self.slots):
            by_key[(name, f"{start}-{end}")] = (_sid, name, start, end)
        for name, time_ in self._cfg_targets:
            entry = by_key.get((name, time_))
            if entry and entry not in self.selected:
                self.selected.append(entry)
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
        if slot[:4] in [s[:4] for s in self.selected]:
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
        targets = [
            {"court_name": name, "time": f"{start}-{end}"}
            for (_sid, name, start, end) in self.selected
        ]
        booking = self.cfg.setdefault("booking", {})
        booking["location"] = self.loc.get()
        booking["targets"] = targets
        try:
            CONFIG_PATH.write_text(render_config(self.cfg), encoding="utf-8")
        except Exception as e:
            messagebox.showerror("保存失败", str(e))
            return
        self._set_status(
            f"已保存: 地点「{self.loc.get() or '不限'}」 · 目标 {len(targets)} 个", "green")
        messagebox.showinfo("保存成功", f"已保存 {len(targets)} 个目标场次到 config/config.yaml")

    def _set_status(self, text, color):
        self.status.config(text=text, foreground=color)


def main():
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
