#!/usr/bin/env python3
"""
交互式配置向导：用表格展示场地 → 选择预约地点/场次/时间 → 写回 config.yaml。

用法:
  python configure.py          # 交互式配置
  python configure.py --slots  # 只查看某天场地表格（不修改配置）

需要先有有效 token（运行一次 capture_token.py 捕获后）。
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from rich.console import Console
from rich.table import Table

import token_util
from book import pre_fetch_type_id, query_slots

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config" / "config.yaml"
console = Console()


def load_config():
    if not CONFIG_PATH.exists():
        console.print(f"[red]缺少 {CONFIG_PATH}，请先运行 setup.bat 生成[/red]")
        sys.exit(1)
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def render_config(cfg):
    """把配置渲染成带注释的干净 YAML（只保留脚本用到的字段）。"""
    L = []
    L.append("# ============================")
    L.append("# 羽毛球预约脚本配置")
    L.append("# 由 configure.py 维护；也可手动编辑")
    L.append("# ============================")
    L.append("")
    L.append("auth:")
    L.append(f'  student_id: "{cfg.get("auth", {}).get("student_id", "")}"')
    L.append('  cas_mode: "wecom"')
    L.append("")
    L.append("booking:")
    L.append('  # 预约地点: "仙林" / "三牌楼" / 空（不限）')
    L.append(f'  location: "{cfg.get("booking", {}).get("location", "仙林")}"')
    L.append('  # 预约日期（空=默认抢今天+book_ahead_days 的场次；用 configure.py 会填上）')
    L.append(f'  target_date: "{cfg.get("booking", {}).get("target_date", "")}"')
    L.append('  # 要抢的场次（按优先级从高到低）')
    L.append("  targets:")
    for t in cfg.get("booking", {}).get("targets", []):
        L.append(f'    - court_name: "{t.get("court_name", "")}"')
        L.append(f'      time: "{t.get("time", "")}"')
    L.append(f'  book_ahead_days: {cfg.get("booking", {}).get("book_ahead_days", 7)}')
    L.append(f'  schedule_time: "{cfg.get("booking", {}).get("schedule_time", "12:00")}"')
    L.append("")
    L.append("token_capture:")
    tc = cfg.get("token_capture", {})
    L.append(f'  enabled: {tc.get("enabled", True)}')
    L.append(f'  timeout_seconds: {tc.get("timeout_seconds", 300)}')
    L.append(f'  fallback_timeout_seconds: {tc.get("fallback_timeout_seconds", 60)}')
    L.append(f'  mitm_port: {tc.get("mitm_port", 8080)}')
    L.append(f'  mode: "{tc.get("mode", "pc_wechat")}"')
    L.append(f'  refresh_time: "{tc.get("refresh_time", "11:30")}"')
    L.append("")
    return "\n".join(L)


def save_config(cfg):
    CONFIG_PATH.write_text(render_config(cfg), encoding="utf-8")
    console.print(f"[green]配置已保存到 {CONFIG_PATH}[/green]")


def ask(question, default=""):
    prompt = f"[cyan]{question}[/cyan]"
    if default:
        prompt += f" [dim](默认 {default})[/dim]"
    try:
        val = console.input(f"{prompt}: ").strip()
    except EOFError:
        return default
    return val or default


def ensure_token():
    tok = token_util.read_cached_token()
    if tok:
        return tok
    console.print("[yellow]无有效 token。请先运行:[/yellow] python capture_token.py --wait 300")
    console.print("    （在电脑微信打开南邮小程序 → 进入场地页，捕获后自动保存）")
    console.print("[yellow]也可以先运行 configure.py --slots 查看提示。[/yellow]")
    return None


def pick_location(cfg):
    """选择预约地点。返回 (location, 说明文字)。"""
    current = cfg.get("booking", {}).get("location", "仙林")
    console.print("\n[bold]选择预约地点:[/bold]")
    console.print("  1) 仙林     2) 三牌楼     3) 不限（全部）    0) 保持当前")
    choice = console.input(f"[cyan]请选择 (默认 {current or '不限'}):[/cyan] ").strip()
    mapping = {"1": "仙林", "2": "三牌楼", "3": ""}
    if choice in mapping:
        return mapping[choice]
    if choice == "0":
        return current
    return current


def show_court_table(slots, location, only_available=True):
    """展示场地表格（id → 场地名 → 时间段 → 状态）。"""
    # 按地点过滤
    filtered = []
    for sid, name, start, end, status, date in slots:
        if location and location not in name:
            continue
        if only_available and not status:
            continue
        filtered.append((sid, name, start, end, status, date))

    if not filtered:
        console.print(f"[yellow]所选地点「{location or '不限'}」当天没有可用场次[/yellow]")
        return []

    table = Table(title=f"可选场次（{location or '不限地点'}，{only_available and '仅可用' or '全部'}）")
    table.add_column("#", justify="right", style="dim")
    table.add_column("场地ID", style="cyan")
    table.add_column("场地名", style="bold")
    table.add_column("时间段", style="green")
    table.add_column("状态", justify="center")
    for i, (sid, name, start, end, status, _date) in enumerate(filtered, 1):
        table.add_row(str(i), str(sid), name, f"{start}-{end}",
                      "[green]可用[/green]" if status else "[red]已满[/red]")
    console.print(table)
    return filtered


def update_basic_info(cfg):
    """基本信息：学号。"""
    sid = (cfg.get("auth") or {}).get("student_id", "")
    new_sid = ask("学号（当前 {0}）".format(sid or "未设置"), sid)
    if new_sid:
        cfg.setdefault("auth", {})["student_id"] = new_sid


def main():
    cfg = load_config()
    is_slots_only = "--slots" in sys.argv

    if not is_slots_only:
        console.print("[bold]===== 预约配置向导 =====[/bold]")
        update_basic_info(cfg)

    token = ensure_token()
    if not token:
        return 1
    type_id = pre_fetch_type_id(token)
    if not type_id:
        console.print("[red]无法获取运动类型。token 可能已失效，请重新运行 capture_token.py --refresh[/red]")
        return 1

    # 选择查询日期（--slots 模式直接用默认日期，不交互）
    today = datetime.now().date()
    book_ahead = cfg.get("booking", {}).get("book_ahead_days", 7)
    default_date = (today + timedelta(days=book_ahead)).strftime("%Y-%m-%d")
    if is_slots_only:
        date = default_date
        console.print(f"[dim]默认查询日期: {default_date}（今天 + {book_ahead} 天）[/dim]")
    else:
        console.print(f"\n[bold]选择要预约的日期:[/bold]")
        console.print(f"  1) 今天 ({today.strftime('%Y-%m-%d')})")
        console.print(f"  2) {book_ahead} 天后（可预约日, 默认）({default_date})")
        console.print(f"  3) 自定义（输入 YYYY-MM-DD）")
        choice = console.input(f"[cyan]选择[/cyan] (默认 2): ").strip()
        if choice == "1":
            date = today.strftime("%Y-%m-%d")
        elif choice == "3":
            date = ask("输入日期 (YYYY-MM-DD)", default_date)
        else:
            date = default_date
    console.print(f"[green]将查询日期 {date} 的场次[/green]")
    slots = query_slots(token, date, type_id)

    # 选择地点
    location = pick_location(cfg) if not is_slots_only else cfg.get("booking", {}).get("location", "仙林")
    filtered = show_court_table(slots, location)

    if is_slots_only:
        return 0
    if not filtered:
        return 1

    # 选择目标场次
    console.print("\n[bold]选择要预约的场次（按优先级，从高到低）:[/bold]")
    console.print("  例：输入 [cyan]1,3,5[/cyan] 表示 1、3、5 号场次，先抢 1 号")
    picks = console.input("[cyan]序号（逗号分隔，回车=清空目标）:[/cyan] ").strip()
    if not picks:
        new_targets = []
        console.print("[yellow]已清空预约目标[/yellow]")
    else:
        new_targets = []
        for p in picks.replace("，", ",").split(","):
            p = p.strip()
            if not p.isdigit():
                continue
            idx = int(p) - 1
            if 0 <= idx < len(filtered):
                sid, name, start, end, _status, _date = filtered[idx]
                new_targets.append({"court_name": name, "time": f"{start}-{end}"})
        if not new_targets:
            console.print("[yellow]未识别到有效选择[/yellow]")

    # 写回配置
    cfg.setdefault("booking", {})["location"] = location
    cfg["booking"]["target_date"] = date
    cfg["booking"]["targets"] = new_targets
    save_config(cfg)

    console.print("\n[bold green]===== 配置完成 =====[/bold green]")
    console.print(f"  地点: [cyan]{location or '不限'}[/cyan]")
    console.print(f"  学号: [cyan]{cfg.get('auth', {}).get('student_id')}[/cyan]")
    console.print(f"  目标场次: {len(new_targets)} 个")
    for t in new_targets:
        console.print(f"    - {t['court_name']}  {t['time']}")
    console.print("\n运行 [bold]python book.py --slots[/bold] 可复查，[bold]python book.py[/bold] 开始抢票")
    return 0


if __name__ == "__main__":
    sys.exit(main())
