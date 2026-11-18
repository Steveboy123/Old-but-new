@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Dr Exploit Control Nexus V2.28 - Repair Python Environment

set "PYEXE="
where py >nul 2>&1
if not errorlevel 1 (
    for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%P"
)
if not defined PYEXE (
    where python >nul 2>&1
    if not errorlevel 1 for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%P"
)
if not defined PYEXE (
    echo Python 3 was not found. Re-run the main installer so it can install Python automatically.
    pause
    exit /b 1
)

if exist ".venv" rmdir /s /q ".venv"
"%PYEXE%" -m venv ".venv"
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --upgrade pip
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r "requirements.txt"
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -m py_compile bot.py web_ui.py rise_fall_bot.py ai_optimizer.py control_nexus_launcher.py
if errorlevel 1 goto :fail

echo Control Nexus Python environment repaired successfully.
exit /b 0

:fail
echo Control Nexus Python environment setup failed.
pause
exit /b 1
