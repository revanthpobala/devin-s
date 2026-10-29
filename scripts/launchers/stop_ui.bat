@echo off
title Stop Stock Trading Operations Cockpit UI (Port 8050)
cd /d "%~dp0..\.."

echo Stopping any process listening on port 8050...
python scripts\kill_port_8050.py --port 8050

if %ERRORLEVEL% EQU 0 (
    echo [OK] Port 8050 is clear.
) else (
    echo [ERROR] Could not clear port 8050.
)
ping 127.0.0.1 -n 3 >nul
