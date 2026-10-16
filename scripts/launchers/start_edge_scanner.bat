@echo off
title Edge Scanner Engine (Port 7777)
cd /d "%~dp0\..\.."

echo ======================================================================
echo   Starting Edge Scanner Real-Time Intraday Engine
echo ======================================================================

if exist "edge_scanner_tmp\data\universe.csv" (
    echo [1/2] Existing universe found. Launching Edge Scanner immediately...
    start /b python scripts\generate_edge_universe.py --limit 150 > logs\edge_universe_bg.log 2>&1
) else (
    echo [1/2] Generating initial Edge Scanner universe from Schwab 1000...
    python scripts\generate_edge_universe.py --limit 150
)

echo.
echo [2/2] Launching Edge Scanner Dashboard ^& WebSocket on Port 7777...
cd edge_scanner_tmp
python scripts\run_live.py --universe data\universe.csv --feed alpaca --log-level INFO

pause
