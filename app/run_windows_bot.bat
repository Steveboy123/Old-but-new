@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Dr Exploit Control Nexus V2.27 - Bot
if not exist ".venv\Scripts\python.exe" (
    echo Run install_windows.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" bot.py %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" echo Bot exited with code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
