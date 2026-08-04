# 🏸 南邮羽毛球场地自动预约

自动化预约南京邮电大学小程序中的羽毛球场，每天 12:00 开放抢购时精确到秒抢购，并自动维护登录 token。

> ⚠️ **免责声明**：本项目仅供学习交流使用，请遵守学校场馆管理规定，理性使用预约资源。使用者需自担风险。

## 功能

- ⏱️ **精确抢购**：同步服务器时间，12:00:00 按配置的优先级顺序抢购，失败自动顺延到其他场次
- 🔑 **token 自动刷新**：token 有效期仅约 3 小时，脚本用 mitmproxy 自动从小程序流量中提取并保存新 token，无需手动抓包复制
- 🤖 **全自动**：注册 Windows 计划任务后，每天 11:30 自动刷新 token、11:55 自动抢票，你只需 11:30 前后打开一次小程序
- 🛠️ **一键配置**：`setup.bat` 自动完成依赖安装、证书信任、计划任务注册

## 工作原理

南邮小程序的预约接口需要 JWT token，而 token 只能通过微信 `wx.login()` 静默登录获取（无法用学号密码直接换取）。脚本因此采用代理抓包方案：

```
你在电脑微信打开小程序
   ↓  (小程序静默调用 wx.login() 获取登录态)
mitmproxy 监听流量，自动提取新 token
   ↓  (写入 data/session_cache.json)
book.py 用 token 调用预约接口，12:00 精确抢购
```

## 快速开始

### 方式一：打包完整版（推荐，无需装任何软件）

1. 从 [Releases](../../releases) 下载 `dist-完整版.zip`（已内置 mitmproxy）
2. 解压到任意目录（**路径不要含中文和空格**，如 `D:\badminton`）
3. 双击 `setup.bat` → 按提示填学号即可

### 方式二：自装版

1. 安装 [Python 3.10+](https://www.python.org/downloads/)（安装时勾选 *Add python.exe to PATH*）
2. 安装 [mitmproxy](https://mitmproxy.org/downloads/)（Windows 版）
3. 克隆本仓库，运行 `setup.bat`

> 两种方式装完后用法完全一样，详见《使用说明.md》。

## 每日使用

```bash
python configure.py                # 配置向导：表格选场地/地点/时间（推荐）
python configure.py --slots        # 只看某天场地表格，不改配置
python capture_token.py --check    # 环境诊断
python capture_token.py --wait 300 # 手动刷新 token（打开小程序自动捕获）
python book.py --slots             # 查看可约场次（只读）
python book.py --test              # 演练模式（不实际预约）
python book.py                     # 抢票（自动等到 12:00）
```

**关键一步**：每天 11:30 前后，在**电脑微信**里打开南邮小程序并进入场地页，token 会自动刷新。如果注册了计划任务，11:55 会全自动抢票。

## 命令速查

| 命令 | 作用 |
|---|---|
| `python configure.py` | 配置向导（选场地/地点/时间） |
| `python configure.py --slots` | 查看场地表格（不改配置） |
| `python capture_token.py --check` | 环境诊断 |
| `python capture_token.py --refresh` | 强制刷新 token |
| `python capture_token.py --install-cert` | 重装 mitmproxy 证书 |
| `python book.py --now` | 立即抢购（不等 12:00） |
| `python book.py --date 2026-06-10` | 抢指定日期 |

## 项目结构

```
├── book.py                    # 预约脚本（主程序）
├── token_util.py              # JWT 读取/校验/保存（共享模块）
├── token_capture_addon.py     # mitmproxy 插件：提取 token
├── capture_token.py           # token 自动抓取/刷新 CLI
├── configure.py               # 配置向导（表格选场地/地点/时间）
├── setup.py / setup.bat       # 一键配置向导
├── config/
│   ├── config.yaml.template   # 配置模板
│   └── config.schema.yaml     # 配置说明
├── scripts/
│   ├── install_task.ps1       # 计划任务注册（11:30 刷新 + 11:55 抢票）
│   ├── setup_proxy.ps1        # 证书/防火墙配置
│   ├── make_dist.py           # 组装分发包
│   └── run.bat                # 手动运行
└── tests/                     # 单元测试
```

## 常见问题

| 现象 | 处理 |
|---|---|
| `--check` 显示 "CA 未被信任" | `python capture_token.py --install-cert`，然后**重启电脑微信** |
| 打开小程序"网络异常" | 抓包期间系统代理临时切换，**重启电脑微信**再打开 |
| "端口 8080 被占用" | 关闭 Fiddler 等其他代理工具 |
| 抢票提示 token 失效 | `python capture_token.py --refresh` 后重试 |

## 隐私说明

- `config/config.yaml`（含学号）与 `data/session_cache.json`（含 token）均已被 gitignore，**不会上传**
- 脚本仅向 `wechat.njupt.edu.cn` 发送预约相关请求

## License

MIT
