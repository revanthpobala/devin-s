@echo off
title End-of-day catch-up
cd /d "D:\My-Projects\Stock"
if not exist ".venv\Scripts\python.exe" (
    echo ERROR: .venv not found in D:\My-Projects\Stock
    exit /b 1
)
.venv\Scripts\python.exe -m src.tracking.backend_tasks --once >> logs\eod_catchup.log 2>&1
