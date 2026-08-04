@echo off
REM ========================================
REM 一键配置向导入口
REM 用法: 双击 setup.bat 或在命令行运行
REM ========================================
cd /d "%~dp0"

REM 检查 Python
where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python not found. Please install Python 3.10+ from https://www.python.org/downloads/
    echo         and check "Add python.exe to PATH" during install.
    pause
    exit /b 1
)

REM 创建虚拟环境（如不存在）
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
)

REM 安装依赖
echo Installing dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt -q
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to install dependencies. Check your network.
    pause
    exit /b 1
)

REM 运行配置向导
".venv\Scripts\python.exe" setup.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Setup completed with warnings. Please read the output above.
)

pause
