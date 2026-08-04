@echo off
chcp 437 >nul
echo ========================================
echo  Badminton Booking - Task Installer
echo ========================================
echo.

set "PY=D:\pyproject\bashforbadminton\.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo ERROR: %PY% not found
    pause
    exit /b 1
)

echo Using: %PY%
echo.
echo Creating scheduled task...

schtasks /create /tn BadmintonBooking /tr "%PY% -m src.main" /sc daily /st 11:55 /f

if %ERRORLEVEL% EQU 0 (
    echo SUCCESS - Task created daily at 11:55
) else (
    echo FAILED - Run as Administrator
)
pause
