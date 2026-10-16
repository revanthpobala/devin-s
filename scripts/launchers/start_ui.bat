@echo off
title Stock Trading Operations Cockpit UI
cd /d "%~dp0..\.."

echo Starting Trading Cockpit UI (port 8050)...
echo Edge Scanner, Notifier and Research Backend auto-start asynchronously in background.
echo.
python run_ui.py --port 8050
pause
