@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PIDFILE=.runtime_control\windows_backend.pid"
if not exist "%PIDFILE%" (
    echo Control Nexus backend PID file was not found.
    echo If the dashboard is already stopped, no action is needed.
    pause
    exit /b 0
)
set /p NEXUSPID=<"%PIDFILE%"
if not defined NEXUSPID goto :cleanup
echo Stopping Control Nexus backend process tree %NEXUSPID%...
taskkill /PID %NEXUSPID% /T /F >nul 2>&1
:cleanup
del /q "%PIDFILE%" >nul 2>&1
echo Control Nexus backend stopped.
pause
