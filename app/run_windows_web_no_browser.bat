@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Dr Exploit Control Nexus V2.27 - Web UI
if not exist ".venv\Scripts\python.exe" (
    echo Run install_windows.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" web_ui.py --host 127.0.0.1 --port 8765
pause
