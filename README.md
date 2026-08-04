# 南邮羽毛球自动预约

每天 12:00 自动抢南邮羽毛球场地，登录 token 自动维护，全程无需手动抓包。

> ⚠️ **免责声明**：仅供学习交流使用，请遵守学校场馆管理规定，理性使用预约资源。

---

## 快速开始（5 步）

### 第 1 步：下载

从 [Releases](../../releases) 下载安装包：

| 版本 | 说明 |
|---|---|
| **dist-完整版.zip**（推荐） | 内置全部软件，解压即用，无需装任何东西 |
| dist-纯代码版.zip | 需自装 Python 和 mitmproxy（见文末） |

### 第 2 步：解压

解压到任意目录，**路径不要含中文和空格**，例如 `D:\badminton`。

### 第 3 步：安装

双击 **`setup.bat`**，按提示操作：

1. 输入你的**学号**
2. 问是否注册每日自动任务 → 推荐输入 **y**

看到 `=== 完成 ===` 即安装成功。

> 安装失败？看文末「常见问题」。

### 第 4 步：刷新登录

每天抢票前需要一次有效登录（token 约 3 小时过期）：

```
python capture_token.py --wait 300
```

然后**在电脑微信里打开南邮小程序 → 进入场地页**，等待 5~20 秒。看到 `[+] 新 token 已捕获` 即成功。

> 若电脑微信在运行，先**重启电脑微信**再打开小程序。
> 首次使用请先跑一次 `python capture_token.py --check`，确认环境全部 `[OK]`。

### 第 5 步：抢票

```bash
python book.py            # 手动抢票（自动等到 12:00）
```

已注册计划任务的用户，每天 **11:55 自动抢票**，无需手动运行。

---

## 修改预约设置（选场地/时间）

运行配置向导，用表格直观选择：

```
python configure.py
```

按提示依次选择：**日期** → **地点**（仙林/三牌楼）→ **场次**（表格里选），选完自动保存。

也可以只看场地不改配置：`python configure.py --slots`

---

## 常见问题

| 现象 | 解决 |
|---|---|
| `setup.bat` 双击闪退或乱码 | 确认下载的是**最新版**安装包；路径不要含中文 |
| `--check` 显示 "CA 未被信任" | 运行 `python capture_token.py --install-cert`，然后**重启电脑微信** |
| 打开小程序显示"网络异常" | 抓包期间系统代理临时切换，**重启电脑微信**再打开小程序 |
| "端口 8080 被占用" | 关闭 Fiddler 等其他代理工具 |
| 抢票提示 token 失效 | 运行 `python capture_token.py --refresh` 后重试 |
| 抢不到场次 | 12:00 竞争激烈属正常，脚本会自动顺延到其他场次 |

---

## 命令速查

| 命令 | 作用 |
|---|---|
| `python configure.py` | 配置向导（选日期/地点/场次） |
| `python configure.py --slots` | 只看场地表格 |
| `python capture_token.py --check` | 环境诊断 |
| `python capture_token.py --refresh` | 手动刷新 token |
| `python book.py --slots` | 查看场次（只读） |
| `python book.py --test` | 演练（不实际预约） |
| `python book.py --now` | 立即抢（不等 12:00） |
| `python book.py --date 2026-08-11` | 抢指定日期 |

---

## 其他安装方式

**自装版**（dist-纯代码版.zip）额外步骤：
1. 安装 [Python 3.10+](https://www.python.org/downloads/)（勾选 *Add python.exe to PATH*）
2. 安装 [mitmproxy](https://mitmproxy.org/downloads/)
3. 解压项目 → 双击 `setup.bat`

---

## 隐私说明

- `config\config.yaml`（含学号）和 `data\session_cache.json`（含 token）都只在本机，**不要分享**
- 脚本仅向 `wechat.njupt.edu.cn` 发送预约请求

## License

MIT
