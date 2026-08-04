@echo off
REM ========================================
REM Badminton booking - manual run
REM ========================================
cd /d "%~dp0.."

REM Activate virtual environment if exists
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

python book.py %*
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Exit code: %ERRORLEVEL%
    echo.
    pause
)
