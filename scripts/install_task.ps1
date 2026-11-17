param([string]$PythonPath = "", [switch]$DryRun)
$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) {
    $candidates = @(
        (Join-Path $project ".venv\Scripts\python.exe"),
        (Join-Path $project "python\python.exe"),
        (Join-Path (Split-Path -Parent $project) "python\python.exe")
    )
    $PythonPath = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $PythonPath) { $PythonPath = (Get-Command python -ErrorAction Stop).Source }
}
$planText = & $PythonPath (Join-Path $project "task_config.py")
if ($LASTEXITCODE -ne 0) { throw "Unable to read task configuration" }
$plan = ($planText -join "`n") | ConvertFrom-Json
$days = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
$selectedDays = @($plan.weekdays | ForEach-Object { $days[[int]$_ - 1] })
$definitions = @(
    @{Name="BadmintonTokenRefresh"; Time=$plan.refresh_time; Script="capture_token.py"; Args="--scheduled"},
    @{Name="BadmintonBooking"; Time=$plan.booking_time; Script="book.py"; Args=""}
)
if ($DryRun) {
    [pscustomobject]@{Python=$PythonPath; Days=$selectedDays; Tasks=$definitions} | ConvertTo-Json -Depth 5
    exit 0
}
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
foreach ($definition in $definitions) {
    $scriptPath = Join-Path $project $definition.Script
    $arguments = '-u "{0}" {1}' -f $scriptPath, $definition.Args
    $action = New-ScheduledTaskAction -Execute $PythonPath -Argument $arguments -WorkingDirectory $project
    $trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $selectedDays -At $definition.Time
    Register-ScheduledTask -TaskName $definition.Name -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
    Write-Host ("Registered {0} at {1} ({2})" -f $definition.Name, $definition.Time, ($selectedDays -join ", "))
}
