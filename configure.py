#!/usr/bin/env python3
"""
交互式配置向导：用表格展示场地 → 选择预约地点/场次/时间 → 写回 config.yaml。

用法:
  python configure.py          # 交互式配置
  python configure.py --slots  # 只查看某天场地表格（不修改配置）

需要先有有效 token（运行一次 capture_token.py 捕获后）。
"""

import sys
import os
import tempfile
from datetime import datetime
from pathlib import Path

import yaml
from rich.console import Console
from rich.table import Table

import token_util
from book import FALLBACK_MODES, pre_fetch_type_id, query_slots, validate_booking

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config" / "config.yaml"
console = Console()


def load_config():
    if not CONFIG_PATH.exists():
        console.print(f"[red]缺少 {CONFIG_PATH}，请先运行 setup.bat 生成[/red]")
        sys.exit(1)
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def render_config(cfg):
    """保留已有配置，防止丢失重试、计划、通知等设置。"""
    cfg = {**cfg, "booking": {**(cfg.get("booking") or {})}}
    cfg["booking"].setdefault("fallback_mode", "strict")
    cfg["booking"]["targets"] = cfg["booking"].get("targets") or []
    cfg["booking"]["fallback_targets"] = cfg["booking"].get("fallback_targets") or []
    return "# 羽毛球预约配置；weekdays: 1=周一，7=周日\n" + yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)


def save_config(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=CONFIG_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(render_config(cfg))
        os.replace(tmp, CONFIG_PATH)
    finally:
        Path(tmp).unlink(missing_ok=True)
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
    """按时间段分组展示场地（序号 | 场地名 | 时间段），返回可按序号选择的列表。"""
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

    # 按时间段分组（如 16:00-17:00 / 17:00-18:00 ...）
    groups = {}
    for entry in filtered:
        sid, name, start, end, status, date = entry
        groups.setdefault(f"{start}-{end}", []).append(entry)

    ordered = []
    for period in sorted(groups):
        table = Table(title=f"【{period}】", show_lines=True)
        table.add_column("#", justify="right", style="dim")
        table.add_column("场地名", style="bold")
        table.add_column("时间段", style="green")
        for entry in groups[period]:
            ordered.append(entry)
            sid, name, start, end, status, date = entry
            table.add_row(str(len(ordered)), name, f"{start}-{end}")
        console.print(table)
    return ordered


def parse_picks(picks, filtered):
    """解析序号输入（支持中文逗号），按「场地+时间段」去重，返回 (目标列表, 重复数)。"""
    new_targets = []
    seen = set()
    dup_count = 0
    for p in picks.replace("，", ",").split(","):
        p = p.strip()
        if not p.isdigit():
            continue
        idx = int(p) - 1
        if 0 <= idx < len(filtered):
            _sid, name, start, end, _status, _date = filtered[idx]
            key = (name, f"{start}-{end}")
            if key in seen:
                dup_count += 1
                continue
            seen.add(key)
            new_targets.append({"court_name": name, "time": f"{start}-{end}"})
    return new_targets, dup_count


def update_basic_info(cfg):
    """基本信息：学号。"""
    sid = (cfg.get("auth") or {}).get("student_id", "")
    new_sid = ask("学号（当前 {0}）".format(sid or "未设置"), sid)
    if new_sid:
        cfg.setdefault("auth", {})["student_id"] = new_sid


def pick_fallback(filtered):
    console.print("\n[bold]主选失败后的处理:[/bold]")
    console.print("  1) 仅已选场次（默认）  2) 同时段换场  3) 指定备选场次")
    choice = ask("请选择", "1")
    modes = {"1": "strict", "2": "same_time", "3": "explicit"}
    if choice not in modes:
        console.print("[red]无效模式，配置未保存[/red]")
        return None
    mode = modes[choice]
    backups = []
    if mode == "explicit":
        picks = ask("备选序号（使用上面的表格，按优先级填写）")
        backups, _ = parse_picks(picks, filtered)
        if not backups:
            console.print("[red]未选择有效备选，配置未保存[/red]")
            return None
    return mode, backups


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

    # 预约日期固定为当天
    date = datetime.now().strftime("%Y-%m-%d")
    console.print(f"[green]将查询今天 ({date}) 的场次[/green]")
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
        console.print("[yellow]已清空预约目标；预约程序将停止，不会自动选择其他场次[/yellow]")
    else:
        new_targets, dup_count = parse_picks(picks, filtered)
        if dup_count:
            console.print(f"[yellow]已忽略 {dup_count} 个重复场次[/yellow]")
        if not new_targets:
            console.print("[yellow]未识别到有效选择，配置未保存[/yellow]")
            return 1

    fallback = pick_fallback(filtered) if new_targets else ("strict", [])
    if fallback is None:
        return 1

    # 写回配置
    cfg.setdefault("booking", {})["location"] = location
    cfg["booking"]["targets"] = new_targets
    cfg["booking"]["fallback_mode"], cfg["booking"]["fallback_targets"] = fallback
    if new_targets:
        try:
            validate_booking(cfg["booking"])
        except ValueError as e:
            console.print(f"[red]{e}，配置未保存[/red]")
            return 1
    save_config(cfg)

    console.print("\n[bold green]===== 配置完成 =====[/bold green]")
    console.print(f"  地点: [cyan]{location or '不限'}[/cyan]")
    console.print(f"  学号: [cyan]{cfg.get('auth', {}).get('student_id')}[/cyan]")
    console.print(f"  目标场次: {len(new_targets)} 个")
    console.print(f"  顺延规则: {FALLBACK_MODES[fallback[0]]}")
    for t in new_targets:
        console.print(f"    - {t['court_name']}  {t['time']}")
    for t in fallback[1]:
        console.print(f"    备选: {t['court_name']}  {t['time']}")
    console.print("\n运行 [bold]python book.py --slots[/bold] 可复查，[bold]python book.py[/bold] 开始抢票")
    return 0


if __name__ == "__main__":
    sys.exit(main())
