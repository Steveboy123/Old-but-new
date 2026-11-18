@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Dr Exploit Control Nexus V2.27
if not exist ".venv\Scripts\pythonw.exe" (
    call install_windows.bat
    if errorlevel 1 exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "control_nexus_launcher.py"
exit /b 0
