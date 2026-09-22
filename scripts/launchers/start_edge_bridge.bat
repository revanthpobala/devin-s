@echo off
title Edge Scanner to Deep Research Bridge Daemon
cd /d "%~dp0\..\.."

echo ======================================================================
echo   Starting Edge Scanner to Deep Research Bridge Daemon
echo ======================================================================
echo   Listening to: ws://localhost:7777/ws/alerts
echo   Min Score:    75.0
echo   Research:     FIRST (Slot-Gated)
echo   Tastytrade:   LAST (Post-Arbitration Approved Only)
echo ======================================================================
echo.

python src\streaming\edge_scanner_bridge.py --auto-deep --min-score 75

pause
