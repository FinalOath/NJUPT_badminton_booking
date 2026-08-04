@echo off
REM ========================================
REM 羽毛球预约脚本 - 手动运行
REM ========================================
cd /d "%~dp0.."

REM 如果存在虚拟环境则激活
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

python book.py %*
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo 脚本执行完毕，退出码: %ERRORLEVEL%
    echo.
    pause
)
