# 南邮羽毛球预约脚本 - 需要自备的软件

> 适用于"自装版"：不用下载打包好的完整包，自己装好下面的软件后运行本项目的 `setup.bat` 即可。

## 需要下载安装的 3 样东西

### 1. Python（3.10 或更高版本）
- **下载**：https://www.python.org/downloads/
- 选最新的 **Windows installer (64-bit)**
- **安装时务必勾选**：`Add python.exe to PATH`（把 Python 加入环境变量）
- 验证：安装后打开 cmd 输入 `python --version` 能显示版本号

### 2. mitmproxy（HTTPS 抓包代理）
- **下载**：https://mitmproxy.org/downloads/
- 选 **Windows** 版本，安装默认路径即可
- 或者用 Windows 包管理器：`winget install mitmproxy`
- 验证：cmd 输入 `mitmdump --version` 能显示版本

### 3. 本项目代码
- 从发布处下载 `南邮羽毛球预约.zip`
- 解压到任意目录（路径不要带中文和空格，例如 `D:\badminton`）
- 项目里已有 `config/config.yaml.template` 配置模板

## 安装完成后

在项目目录双击 **`setup.bat`**，它会自动：
1. 创建 Python 虚拟环境并安装依赖
2. 引导你填写学号
3. 安装 mitmproxy 证书（无需管理员）
4. 询问是否注册每日 11:30 刷新 + 11:55 抢票计划任务

## 前置条件（每个同学都有的）

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 10/11 |
| 电脑微信 | 已安装，且登录的是你绑定了学号的微信 |
| 网络 | 能访问 `wechat.njupt.edu.cn`（校园网/公网均可） |

## 常见问题

- **`python` 不是内部或外部命令** → Python 没加入 PATH，重装时勾选 `Add python.exe to PATH`
- **`mitmdump` 找不到** → mitmproxy 没装成功，或装到了非默认路径（脚本也会尝试找）
- **打开小程序显示"网络异常"** → 抓包期间系统代理临时切换，先重试；仍不行再**重启电脑微信**
- **抢不到场次** → 12:00 竞争激烈属正常，脚本会自动顺延到其他仙林场次
