@echo off
cd /d "%~dp0"
set "BOOKING_PY=python"
if exist "python\python.exe" set "BOOKING_PY=%CD%\python\python.exe"
if exist "..\python\python.exe" set "BOOKING_PY=%CD%\..\python\python.exe"
if exist ".venv\Scripts\python.exe" set "BOOKING_PY=%CD%\.venv\Scripts\python.exe"
"%BOOKING_PY%" dashboard.py
if errorlevel 1 pause
