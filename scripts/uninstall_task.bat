@echo off
chcp 65001 >nul
schtasks /delete /tn "BadmintonBooking" /f
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Task deleted
) else (
    echo [FAILED] Run as Administrator
)
pause
