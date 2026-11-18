@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if exist "START_CONTROL_NEXUS_APP.vbs" (
    start "" wscript.exe "%~dp0START_CONTROL_NEXUS_APP.vbs"
    exit /b 0
)
if not exist ".venv\Scripts\python.exe" (
    call install_windows.bat
    if errorlevel 1 exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "control_nexus_launcher.py"
exit /b 0
