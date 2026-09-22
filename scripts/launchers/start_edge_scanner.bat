@echo off
title Edge Scanner Engine (Port 7777)
cd /d "%~dp0\..\.."

echo ======================================================================
echo   Starting Edge Scanner Real-Time Intraday Engine
echo ======================================================================

echo [1/2] Refreshing Active Coiled Universe from Schwab 1000...
python scripts\generate_edge_universe.py --limit 150
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to generate universe CSV!
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo [2/2] Launching Edge Scanner Dashboard ^& WebSocket on Port 7777...
cd edge_scanner_tmp
python scripts\run_live.py --universe data\universe.csv --feed alpaca --log-level INFO

pause
