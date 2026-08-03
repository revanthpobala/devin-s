@echo off
title TradingView Alerts Tracker
cd /d "D:\My-Projects\Stock"
echo =======================================================================
echo              TRADINGVIEW ALERTS TRACKER LOOP SERVICE
echo =======================================================================
echo.
echo Active Market Hours: 7:15 AM - 3:00 PM Mountain Time (Monday-Friday)
echo Polling Interval  : 60 seconds
echo logs path         : D:\My-Projects\Stock\logs\tracker.log
echo.
echo Checking virtual environment...

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo ERROR: Virtual environment (.venv) not found in D:\My-Projects\Stock.
    echo Please run this batch script inside the project directory.
    echo.
    pause
    exit /b
)

echo Virtual environment verified. Starting tracker loop...
echo [Press Ctrl+C at any time to stop the service]
echo.

.venv\Scripts\python.exe main.py --loop

echo.
echo Tracker loop has stopped.
pause
