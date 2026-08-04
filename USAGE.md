# 使用说明（简版）

> 完整教程请看 **README.md**。这里是命令速查和常见问题。

## 每日使用流程

1. **刷新 token**（每天预约前做一次）：
   ```
   python capture_token.py --wait 300
   ```
   然后**在电脑微信打开南邮小程序 → 进入场地页**，5~20 秒自动捕获。
   若一直无法获取，再**重启电脑微信**重试。

2. **抢票**（已注册计划任务则 11:55 自动执行）：
   ```
   python book.py
   ```

## 配置预约（选地点/场次）

```
python configure.py
```

依次选择：**地点**（仙林/三牌楼）→ **场次**（表格里按序号选，支持中文逗号，自动去重）。预约日期固定为当天。

## 命令速查

| 命令 | 作用 |
|---|---|
| `python configure.py` | 配置向导 |
| `python configure.py --slots` | 只看场地表格 |
| `python capture_token.py --check` | 环境诊断 |
| `python capture_token.py --refresh` | 强制刷新 token |
| `python capture_token.py --install-cert` | 重装证书 |
| `python book.py --slots` | 查看场次 |
| `python book.py --test` | 演练 |
| `python book.py --now` | 立即抢 |
| `python benchmark.py` | 抢票性能基准 |

## 常见问题

| 现象 | 解决 |
|---|---|
| `--check` 显示 "CA 未被信任" | `python capture_token.py --install-cert`，然后**重启电脑微信** |
| 打开小程序"网络异常" | 抓包期间系统代理临时切换；仍不行就**重启电脑微信**再打开 |
| "端口 8080 被占用" | 关闭 Fiddler 等代理工具 |
| 抢票提示 token 失效 | `python capture_token.py --refresh` 后重试 |
| 抢不到场次 | 12:00 竞争激烈，脚本会自动顺延到其他场次 |

## 隐私

`config\config.yaml`（含学号）、`data\session_cache.json`（含 token）只在本机，**不要分享**。
