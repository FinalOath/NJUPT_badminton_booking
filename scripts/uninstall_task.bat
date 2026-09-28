@echo off
chcp 65001 >nul

set FAILED=0
schtasks /delete /tn "BadmintonTokenRefresh" /f >nul 2>nul
if errorlevel 1 set FAILED=1
schtasks /delete /tn "BadmintonBooking" /f >nul 2>nul
if errorlevel 1 set FAILED=1

if "%FAILED%"=="1" (
    echo [WARN] Failed to delete one or more tasks - run as Administrator
) else (
    echo [SUCCESS] Deleted BadmintonTokenRefresh and BadmintonBooking tasks
)
pause
