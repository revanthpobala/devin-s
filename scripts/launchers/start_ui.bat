@echo off
title Stock Trading Operations Cockpit UI
cd /d "%~dp0..\.."

echo [1/2] Refreshing Edge Scanner universe from Schwab 1000...
python scripts\generate_edge_universe.py --limit 150
echo.
echo [2/2] Starting Trading Cockpit UI (port 8050)...
echo       Edge Scanner will auto-start in background (port 7777)
echo.
python run_ui.py --port 8050
pause
