# 注册两个每日计划任务：
#   11:30  BadmintonTokenRefresh  -> capture_token.py --wait 300（自动抓取新 token）
#   11:55  BadmintonBooking       -> book.py（抢票；token 过期时会自动短时抓包兜底）
# 用法: powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1
$project = Split-Path -Parent $PSScriptRoot
$py = Join-Path $project ".venv\Scripts\python.exe"

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

$refreshAction = New-ScheduledTaskAction -Execute $py -Argument "capture_token.py --wait 300" -WorkingDirectory $project
$refreshTrigger = New-ScheduledTaskTrigger -Daily -At 11:30
Register-ScheduledTask -TaskName "BadmintonTokenRefresh" -Action $refreshAction -Trigger $refreshTrigger -Settings $settings -Force
Write-Host "Task created daily at 11:30 -> capture_token.py --wait 300"

$bookAction = New-ScheduledTaskAction -Execute $py -Argument "book.py" -WorkingDirectory $project
$bookTrigger = New-ScheduledTaskTrigger -Daily -At 11:55
Register-ScheduledTask -TaskName "BadmintonBooking" -Action $bookAction -Trigger $bookTrigger -Settings $settings -Force
Write-Host "Task created daily at 11:55 -> book.py"
