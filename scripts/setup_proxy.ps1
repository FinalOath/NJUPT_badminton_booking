# ============================================================
# 一次性配置：mitmproxy 证书信任 + 防火墙（手机模式可选）
# 用法（在项目目录执行）:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_proxy.ps1
# 说明: 证书安装为当前用户作用域，不需要管理员；防火墙规则需要管理员。
# ============================================================
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$mitmConf = Join-Path $HOME ".mitmproxy"
$certCer = Join-Path $mitmConf "mitmproxy-ca-cert.cer"

Write-Host "=== 1/4 检查 mitmproxy CA 证书 ===" -ForegroundColor Cyan
if (-not (Test-Path $certCer)) {
    Write-Host "未找到 $certCer" -ForegroundColor Yellow
    Write-Host "请先手动运行一次: mitmdump（会自动生成证书到 ~\.mitmproxy），再重跑本脚本。"
    exit 1
}
$inStore = certutil -user -store Root | Select-String -SimpleMatch "mitmproxy"
if ($inStore) {
    Write-Host "[OK] CA 证书已在系统信任列表中" -ForegroundColor Green
} else {
    Write-Host "安装 CA 证书到 当前用户-受信任的根证书颁发机构..." -ForegroundColor Cyan
    certutil -user -addstore -f Root $certCer
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[OK] 证书已安装" -ForegroundColor Green
    } else {
        Write-Host "[FAIL] 证书安装失败，请手动: certutil -user -addstore -f Root $certCer" -ForegroundColor Red
        exit 1
    }
}

Write-Host "=== 2/4 检查代理端口 8080 是否被占用 ===" -ForegroundColor Cyan
$portUsed = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
if ($portUsed) {
    Write-Host "[WARN] 8080 已被占用（Fiddler 可能正在运行）。使用 capture_token.py 时会自动换端口。" -ForegroundColor Yellow
} else {
    Write-Host "[OK] 8080 空闲" -ForegroundColor Green
}

Write-Host "=== 3/4 防火墙放行 8080（仅手机模式需要，PC 微信模式可跳过）===" -ForegroundColor Cyan
$choice = Read-Host "手机(Shadowrocket)模式需要放行入站 8080。PC 微信模式不需要。放行吗? [y/N]"
if ($choice -match "^[yY]") {
    netsh advfirewall firewall delete rule name="mitmweb" 2>$null | Out-Null
    netsh advfirewall firewall add rule name="mitmweb" dir=in action=allow protocol=TCP localport=8080
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[OK] 防火墙已放行 8080" -ForegroundColor Green
    } else {
        Write-Host "[!] 防火墙规则添加失败（需要以管理员运行本脚本）。可手动运行 scripts\allow_mitm.bat。" -ForegroundColor Yellow
    }
} else {
    Write-Host "跳过防火墙配置。" -ForegroundColor DarkGray
}

Write-Host "=== 4/4 验证 ===" -ForegroundColor Cyan
Write-Host "运行诊断:"
Write-Host "    $root\.venv\Scripts\python.exe $root\capture_token.py --check"
Write-Host ""
Write-Host "日常使用（PC 微信模式）:"
Write-Host "    python capture_token.py --wait 300   # 起代理→你在电脑微信打开南邮小程序→自动捕获→还原代理"
Write-Host "    python book.py                       # token 过期时会自动尝试短时抓包"
Write-Host ""
Write-Host "注意: 改代理后若电脑微信已打开，需重启电脑微信再打开小程序。"
Write-Host "计划任务: scripts\install_task.ps1 会注册每日 11:30 自动刷新 + 11:55 抢票。"
