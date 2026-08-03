@echo off
title Market Hours Orchestrator
cd /d "D:\My-Projects\Stock"
set PYTHONUTF8=1
echo =======================================================================
echo              MARKET HOURS PIPELINE ORCHESTRATOR
echo =======================================================================
echo.
echo Enforcing control window: 7:15 AM - 3:00 PM Mountain Time (Mon-Fri)
echo LLM: In-process GGUF (Qwen3.5-9B) via llama-cpp-python
echo Macro context: Finnhub news + FMP VIX/movers (injected into every alert)
echo Releases services automatically when market is closed.
echo.
echo Logs path: D:\My-Projects\Stock\logs\orchestrator.log
echo.
echo Starting orchestrator...
echo [Press Ctrl+C at any time to shut down all processes and exit]
echo.

REM Step 1: Apply sleep prevention settings
echo [SETUP] Applying sleep prevention settings...
call "%~dp0start_sleep_prevention.bat"
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to apply sleep prevention settings.
    pause
    exit /b 1
)

echo.
echo [RUN] Starting orchestrator...
python run_market_orchestrator.py

echo.
echo Orchestrator stopped.
pause
