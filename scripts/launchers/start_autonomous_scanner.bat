@echo off
title Schwab 1000 Autonomous Scanner & Research Engine
cd /d "D:\My-Projects\Stock"
echo =======================================================================
echo     SCHWAB 1000 CONTINUOUS SCANNER ^& AUTONOMOUS RESEARCH ENGINE
echo =======================================================================
echo.
echo Universe         : Schwab 1000 (SCHK ETF - 983 stocks)
echo Scan Interval    : 300s (5 min)
echo Autonomous Deep  : 1 in slot when qualified (Score ^>= 60)
echo Logging to       : D:\My-Projects\Stock\logs\continuous_screener.log
echo.

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Virtual environment (.venv) not found.
    pause
    exit /b
)

echo Starting Continuous Autonomous Scanner...
echo [Press Ctrl+C at any time to stop the service]
echo.

.venv\Scripts\python.exe run_continuous_screener.py --interval 300 --auto-deep --headless

echo.
echo Autonomous scanner stopped.
pause
